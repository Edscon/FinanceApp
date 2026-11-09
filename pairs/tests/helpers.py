import math

from engine.data.providers import Bar
from engine.data.synthetic import synthetic_universe
from pairs.services import upsert_bars
from screener.models import Company


def bars_from_series(series):
    """Serie de precios (índice de fechas) -> lista de ``Bar`` solo con cierre."""
    return [
        Bar(date=ts.date(), adj_open=None, adj_high=None, adj_low=None, adj_close=float(value), volume=None)
        for ts, value in series.items()
    ]


def seed_synthetic_universe(n=600, seed=4, with_sector=True, jump=None):
    """Crea empresas y barras con el universo sintético. ``jump=(ticker, log_salto)`` aplica un
    salto en la última sesión de ese valor."""
    prices, groups, truth = synthetic_universe(n, seed=seed)
    if jump is not None:
        ticker, log_jump = jump
        prices.loc[prices.index[-1], ticker] *= math.exp(log_jump)
    for ticker in prices.columns:
        company = Company.objects.create(ticker=ticker, sector=groups[ticker] if with_sector else None)
        upsert_bars(company, bars_from_series(prices[ticker]))
    return prices, groups, truth
