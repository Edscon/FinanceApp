"""Importación de precios a la base de datos (Yahoo, FMP, CSV) y datos de demostración."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from django.conf import settings
from django.db import transaction

from engine.data.providers import Bar, FMPProvider, ProviderError, YahooProvider, read_price_csv
from engine.data.synthetic import synthetic_market

from .models import DailyBar, Instrument, Universe
from .services import normalize_ticker, set_members, upsert_bars

DEMO_UNIVERSE = 'Demo (sintético)'
PROVIDERS = {'yahoo': 'Yahoo Finance', 'fmp': 'Financial Modeling Prep'}


def _summary(tickers, imported, errors) -> dict:
    message = f'Importados {len(imported)} de {len(tickers)} valores'
    if errors:
        message += f'; {len(errors)} con errores'
    return {'imported': imported, 'errors': errors, 'message': message}


def import_prices(tickers, provider: str = 'yahoo', start: dt.date | None = None, end: dt.date | None = None,
                  progress=None, yahoo: YahooProvider | None = None) -> dict:
    """Descarga y guarda barras diarias ajustadas. Un ticker que falla no detiene a los demás."""
    tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers if t.strip()))
    imported: dict[str, int] = {}
    errors: dict[str, str] = {}

    def save(ticker, bars, source):
        instrument, _ = Instrument.objects.get_or_create(ticker=ticker)
        imported[ticker] = upsert_bars(instrument, bars, source=source)

    if provider == 'yahoo':
        scaled = (lambda f, m: progress(0.9 * f, m)) if progress else None
        bars_by_ticker, errors = (yahoo or YahooProvider()).daily_bars_many(tickers, start, end, progress=scaled)
        for k, (ticker, bars) in enumerate(bars_by_ticker.items()):
            save(ticker, bars, 'yahoo')
            if progress:
                progress(0.9 + 0.1 * (k + 1) / max(1, len(bars_by_ticker)), f'Guardando {ticker}')
    elif provider == 'fmp':
        try:
            fmp = FMPProvider(settings.FMP_API_KEY)
        except ProviderError as exc:
            return _summary(tickers, {}, {t: str(exc) for t in tickers})
        for k, ticker in enumerate(tickers):
            try:
                bars = fmp.daily_bars(ticker, start, end)
            except ProviderError as exc:
                errors[ticker] = str(exc)
            else:
                if bars:
                    save(ticker, bars, 'fmp')
                else:
                    errors[ticker] = 'FMP no devolvió datos en ese rango.'
            if progress:
                progress((k + 1) / len(tickers), f'{ticker} ({k + 1} de {len(tickers)})')
    else:
        raise ValueError(f'Proveedor desconocido: {provider}')
    return _summary(tickers, imported, errors)


def import_csv_dir(tickers, directory, start=None, end=None) -> dict:
    """Lee ``<directorio>/<TICKER>.csv`` (columnas date + adj_close o close)."""
    directory = Path(directory)
    imported, errors = {}, {}
    for ticker in (normalize_ticker(t) for t in tickers):
        try:
            bars = [b for b in read_price_csv(directory / f'{ticker}.csv')
                    if (start is None or b.date >= start) and (end is None or b.date <= end)]
        except ProviderError as exc:
            errors[ticker] = str(exc)
            continue
        if not bars:
            errors[ticker] = 'sin datos en el rango pedido'
            continue
        instrument, _ = Instrument.objects.get_or_create(ticker=ticker)
        imported[ticker] = upsert_bars(instrument, bars, source='csv')
    return _summary(list(imported) + list(errors), imported, errors)


def _bars_from_frames(ticker, close, opens, volume) -> list[Bar]:
    return [
        Bar(date=ts.date(), adj_open=float(o), adj_high=None, adj_low=None, adj_close=float(c), volume=int(v))
        for ts, c, o, v in zip(close.index, close[ticker], opens[ticker], volume[ticker])
    ]


@transaction.atomic
def load_demo(n: int = 1500, seed: int = 11) -> Universe:
    """Mercado sintético de estructura conocida (tickers con prefijo DEMO-). Idempotente."""
    market = synthetic_market(n, seed=seed)
    DailyBar.objects.filter(instrument__ticker__startswith='DEMO-').delete()
    universe, _ = Universe.objects.update_or_create(
        name=DEMO_UNIVERSE,
        defaults={
            'description': 'Datos inventados con estructura conocida: reversión plantada en dos grupos, un grupo de '
                           'ruido, dos pares cointegrados, un par trampa y un par que se rompe. Sirve para probar la '
                           'app sin descargar nada; sus resultados no dicen nada del mercado real.',
            'benchmark': '',
            'is_preset': True,
        },
    )
    groups = market['groups']
    set_members(universe, sorted(groups.items()), kind='stock')
    for ticker in groups:
        instrument = Instrument.objects.get(ticker=ticker)
        instrument.name = 'Valor sintético'
        instrument.sector = groups[ticker]
        instrument.save(update_fields=['name', 'sector'])
        upsert_bars(instrument, _bars_from_frames(ticker, market['close'], market['open'], market['volume']), source='synthetic')
    return universe
