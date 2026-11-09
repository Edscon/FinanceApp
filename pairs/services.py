"""Pegamento entre la base de datos (Django) y el motor (``engine``, que no conoce Django)."""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

import pandas as pd

from engine.data.providers import Bar, CompanyProfile
from screener.models import Company

from .models import DailyBar

GROUP_FIELDS = ('sector', 'industry')


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def upsert_bars(company: Company, bars: Iterable[Bar]) -> int:
    """Inserta o actualiza barras de una empresa (idempotente). Devuelve cuántas procesó."""
    rows = [
        DailyBar(
            company=company,
            date=bar.date,
            adj_open=bar.adj_open,
            adj_high=bar.adj_high,
            adj_low=bar.adj_low,
            adj_close=bar.adj_close,
            volume=bar.volume,
        )
        for bar in bars
    ]
    if not rows:
        return 0
    DailyBar.objects.bulk_create(
        rows,
        batch_size=500,
        update_conflicts=True,
        unique_fields=['company', 'date'],
        update_fields=['adj_open', 'adj_high', 'adj_low', 'adj_close', 'volume'],
    )
    return len(rows)


def load_price_matrix(
    tickers: Iterable[str] | None = None, end: dt.date | None = None
) -> pd.DataFrame:
    """Cierres ajustados como matriz: índice = fechas, columnas = tickers.

    ``tickers=None`` carga todas las empresas con barras. ``end`` descarta barras
    posteriores (sirve para reproducir un escaneo pasado sin ver el futuro).
    Devuelve un DataFrame vacío si no hay datos.
    """
    queryset = DailyBar.objects.all()
    if tickers is not None:
        queryset = queryset.filter(company__ticker__in=list(tickers))
    if end is not None:
        queryset = queryset.filter(date__lte=end)
    rows = list(queryset.values_list('company__ticker', 'date', 'adj_close'))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=['ticker', 'date', 'adj_close'])
    frame['date'] = pd.to_datetime(frame['date'])
    return frame.pivot(index='date', columns='ticker', values='adj_close').sort_index()


def load_groups(tickers: Iterable[str], field: str) -> dict[str, str]:
    """Grupo (sector o industria) de cada ticker que lo tenga informado."""
    if field not in GROUP_FIELDS:
        raise ValueError(f'field debe ser uno de {GROUP_FIELDS}.')
    rows = Company.objects.filter(ticker__in=list(tickers)).values_list('ticker', field)
    return {ticker: value for ticker, value in rows if value}


def apply_profile(company: Company, profile: CompanyProfile) -> list[str]:
    """Vuelca el perfil de FMP en la empresa y devuelve los campos que cambió.

    Sector, industria y capitalización se refrescan siempre que el perfil los
    traiga; nombre, bolsa y moneda solo se rellenan si estaban vacíos.
    """
    changed = []
    for field in ('sector', 'industry', 'market_cap'):
        value = getattr(profile, field)
        if value is not None and getattr(company, field) != value:
            setattr(company, field, value)
            changed.append(field)
    for field in ('name', 'exchange', 'currency'):
        value = getattr(profile, field)
        if value and not getattr(company, field):
            setattr(company, field, value)
            changed.append(field)
    if changed:
        company.save(update_fields=changed)
    return changed
