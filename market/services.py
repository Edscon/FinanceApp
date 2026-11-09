"""Pegamento entre la base de datos y el motor (``engine``, que no conoce Django)."""
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd
from django.db import transaction
from django.db.models import Count, Max, Min, Sum

from engine.backtest.data import MarketData
from engine.data.providers import Bar

from .models import DailyBar, Instrument, Universe, UniverseMember

PRESETS_DIR = Path(__file__).resolve().parent / 'presets'


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def parse_tickers(text: str) -> list[str]:
    """Tickers separados por comas, espacios o saltos de línea, sin duplicados y en orden."""
    raw = text.replace(',', ' ').replace(';', ' ').split()
    return list(dict.fromkeys(normalize_ticker(t) for t in raw if t.strip()))


def upsert_bars(instrument: Instrument, bars: Iterable[Bar], source: str = '') -> int:
    """Inserta o actualiza barras (idempotente) y refresca las estadísticas del instrumento."""
    rows = [
        DailyBar(
            instrument=instrument,
            date=bar.date,
            adj_open=bar.adj_open,
            adj_high=bar.adj_high,
            adj_low=bar.adj_low,
            adj_close=bar.adj_close,
            volume=bar.volume,
        )
        for bar in bars
    ]
    if rows:
        DailyBar.objects.bulk_create(
            rows,
            batch_size=500,
            update_conflicts=True,
            unique_fields=['instrument', 'date'],
            update_fields=['adj_open', 'adj_high', 'adj_low', 'adj_close', 'volume'],
        )
    refresh_stats(instrument, source=source)
    return len(rows)


def quality_flags(dates: list[dt.date], closes: np.ndarray, opens: np.ndarray) -> list[str]:
    flags = []
    if len(dates) < 2:
        return flags
    gaps = np.diff(np.array(dates, dtype='datetime64[D]')).astype(int)
    if (gaps > 7).any():
        flags.append(f'{int((gaps > 7).sum())} huecos de más de una semana')
    with np.errstate(divide='ignore', invalid='ignore'):
        jumps = np.abs(np.diff(np.log(closes)))
    if (jumps > np.log(1.5)).any():
        flags.append(f'{int((jumps > np.log(1.5)).sum())} movimientos diarios extremos (más de +50 % o -33 %): '
                     'pueden ser reales (crac, resultados) o un ajuste erróneo (p. ej. una escisión)')
    missing_open = float(np.mean(~np.isfinite(opens))) if len(opens) else 1.0
    if missing_open > 0.05:
        flags.append(f'{100 * missing_open:.0f} % de sesiones sin apertura')
    return flags


def refresh_stats(instrument: Instrument, source: str = '') -> Instrument:
    stats = instrument.bars.aggregate(first=Min('date'), last=Max('date'), n=Count('id'))
    rows = list(instrument.bars.order_by('date').values_list('date', 'adj_close', 'adj_open'))
    dates = [r[0] for r in rows]
    closes = np.array([r[1] for r in rows], dtype=float)
    opens = np.array([np.nan if r[2] is None else r[2] for r in rows], dtype=float)
    instrument.first_date = stats['first']
    instrument.last_date = stats['last']
    instrument.n_bars = stats['n'] or 0
    instrument.quality = quality_flags(dates, closes, opens)
    if source:
        instrument.source = source
    instrument.save(update_fields=['first_date', 'last_date', 'n_bars', 'quality', 'source', 'updated_at'])
    return instrument


def coverage_summary() -> dict:
    with_data = Instrument.objects.filter(n_bars__gt=0)
    agg = with_data.aggregate(first=Min('first_date'), last=Max('last_date'), bars=Sum('n_bars'))
    return {
        'instruments': with_data.count(),
        'bars': agg['bars'] or 0,
        'first': agg['first'],
        'last': agg['last'],
        'universes': Universe.objects.count(),
    }


def load_market_data(universe: Universe, end: dt.date | None = None, tickers: list[str] | None = None) -> MarketData:
    """Carga toda la historia (hasta ``end``) de los miembros con datos del universo.

    No se recorta por la fecha inicial del backtest: las estrategias necesitan historia previa
    para calentar (volatilidades, ventanas de formación). El motor aplica el inicio."""
    groups = universe.groups()
    members = tickers or universe.tickers()
    query = DailyBar.objects.filter(instrument__ticker__in=members)
    if end:
        query = query.filter(date__lte=end)
    rows = list(query.values_list('instrument__ticker', 'date', 'adj_open', 'adj_close', 'volume'))
    if not rows:
        if tickers:
            raise ValueError(f'No hay precios de {", ".join(tickers)} hasta esa fecha.')
        raise ValueError(f'El universo «{universe.name}» no tiene precios. Impórtalos desde la página Datos.')
    frame = pd.DataFrame(rows, columns=['ticker', 'date', 'open', 'close', 'volume'])
    frame['date'] = pd.to_datetime(frame['date'])
    pivot = lambda column: frame.pivot(index='date', columns='ticker', values=column).sort_index().astype(float)
    close = pivot('close')
    open_ = pivot('open')
    volume = pivot('volume')

    benchmark = None
    if universe.benchmark:
        bench_rows = DailyBar.objects.filter(instrument__ticker=universe.benchmark)
        if end:
            bench_rows = bench_rows.filter(date__lte=end)
        values = list(bench_rows.values_list('date', 'adj_close'))
        if values:
            benchmark = pd.Series({pd.Timestamp(d): c for d, c in values}).sort_index()
    return MarketData(
        close,
        open_ if np.isfinite(open_.to_numpy()).any() else None,
        volume if np.isfinite(volume.to_numpy()).any() else None,
        {t: g for t, g in groups.items() if t in close.columns},
        benchmark,
        universe.benchmark if benchmark is not None else '',
    )


@transaction.atomic
def set_members(universe: Universe, rows: list[tuple[str, str]], kind: str = 'stock') -> int:
    """Sustituye los miembros del universo por ``rows`` = [(ticker, grupo)]. Crea instrumentos
    nuevos (sin datos) si hace falta."""
    universe.members.all().delete()
    created = 0
    for ticker, group in rows:
        instrument, new = Instrument.objects.get_or_create(ticker=normalize_ticker(ticker), defaults={'kind': kind, 'sector': group})
        created += int(new)
        UniverseMember.objects.create(universe=universe, instrument=instrument, group=group)
    return created


def parse_members(text: str) -> list[tuple[str, str]]:
    """Una línea por valor: ``TICKER, Grupo`` (el grupo es opcional)."""
    rows = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.replace(';', ',').split(',', 1)]
        ticker = normalize_ticker(parts[0])
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        rows.append((ticker, parts[1] if len(parts) > 1 else ''))
    return rows


def load_presets() -> list[Universe]:
    """Crea o actualiza los universos predefinidos (sin descargar precios)."""
    universes = []
    for path in sorted(PRESETS_DIR.glob('*.json')):
        spec = json.loads(path.read_text(encoding='utf-8'))
        universe, _ = Universe.objects.update_or_create(
            name=spec['name'],
            defaults={'description': spec.get('description', ''), 'benchmark': spec.get('benchmark', ''), 'is_preset': True},
        )
        rows = [(ticker, group) for group, tickers in spec['groups'].items() for ticker in tickers]
        set_members(universe, rows, kind=spec.get('kind', 'stock'))
        for ticker, group in rows:
            Instrument.objects.filter(ticker=ticker, sector='').update(sector=group)
        if spec.get('benchmark'):
            Instrument.objects.get_or_create(ticker=spec['benchmark'], defaults={'kind': 'etf', 'name': 'Benchmark'})
        universes.append(universe)
    return universes
