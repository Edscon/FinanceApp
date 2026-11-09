"""Series sintéticas con estructura conocida, para tests y demos.

Sirven para comprobar que el motor *recupera* lo que se sabe que hay (beta,
half-life, qué pares cointegran) antes de fiarse de él con datos reales.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

DEFAULT_END = dt.date(2025, 12, 31)


def _index(n: int, end: dt.date | str) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=n)


def random_walk(
    n: int,
    *,
    seed: int,
    start: float = 100.0,
    drift: float = 0.0002,
    vol: float = 0.015,
    end: dt.date | str = DEFAULT_END,
    name: str = 'RW',
) -> pd.Series:
    """Precio cuyo log es un camino aleatorio con deriva (retornos diarios iid)."""
    rng = np.random.default_rng(seed)
    log_price = math.log(start) + np.cumsum(drift + vol * rng.standard_normal(n))
    return pd.Series(np.exp(log_price), index=_index(n, end), name=name)


def cointegrated_pair(
    n: int,
    *,
    seed: int,
    beta: float = 1.2,
    alpha: float = 0.3,
    half_life: float = 10.0,
    spread_vol: float = 0.02,
    vol: float = 0.015,
    drift: float = 0.0002,
    start: float = 100.0,
    end: dt.date | str = DEFAULT_END,
    names: tuple[str, str] = ('Y', 'X'),
) -> pd.DataFrame:
    """Dos precios con ``log y = alpha + beta * log x + s``, donde ``s`` es un AR(1).

    ``x`` es un camino aleatorio y ``s`` revierte a 0 con el half-life dado y
    desviación típica estacionaria ``spread_vol``. Devuelve columnas (y, x).
    """
    rng = np.random.default_rng(seed)
    log_x = math.log(start) + np.cumsum(drift + vol * rng.standard_normal(n))
    phi = 0.5 ** (1.0 / half_life)
    innovations = spread_vol * math.sqrt(1.0 - phi * phi) * rng.standard_normal(n)
    spread = np.empty(n)
    spread[0] = spread_vol * rng.standard_normal()
    for t in range(1, n):
        spread[t] = phi * spread[t - 1] + innovations[t]
    log_y = alpha + beta * log_x + spread
    return pd.DataFrame(
        {names[0]: np.exp(log_y), names[1]: np.exp(log_x)}, index=_index(n, end)
    )


def factor_walks(
    n: int,
    *,
    seed: int,
    names: tuple[str, ...] = ('C', 'D'),
    loading: float = 1.0,
    factor_vol: float = 0.015,
    idio_vol: float = 0.007,
    drift: float = 0.0002,
    start: float = 100.0,
    end: dt.date | str = DEFAULT_END,
) -> pd.DataFrame:
    """Precios que comparten un factor común pero NO cointegran entre sí.

    Los retornos de cada uno son ``loading`` veces el factor más ruido propio. Ese
    ruido se acumula como un camino aleatorio, así que el spread entre dos
    cualquiera nunca revierte, aunque la correlación de retornos sea alta (~0.8
    con los valores por defecto): el caso "correlación sin cointegración" que el
    escáner debe rechazar.
    """
    rng = np.random.default_rng(seed)
    factor = factor_vol * rng.standard_normal(n)
    columns = {}
    for name in names:
        returns = drift + loading * factor + idio_vol * rng.standard_normal(n)
        columns[name] = np.exp(math.log(start) + np.cumsum(returns))
    return pd.DataFrame(columns, index=_index(n, end))


def synthetic_universe(
    n: int = 600, *, seed: int = 4, end: dt.date | str = DEFAULT_END
) -> tuple[pd.DataFrame, dict[str, str], set[frozenset]]:
    """Universo pequeño con estructura conocida.

    Devuelve ``(precios, grupos, verdad)``:

    * Tecnologia: TA/TB cointegran (beta 1.2, half-life 5 días); TC/TD están muy
      correlados (~0.8) por un factor común pero no cointegran (caso trampa).
    * Energia: EA/EB cointegran (beta 0.8, half-life 7 días); EC es independiente.
    * ``verdad``: los pares que de verdad cointegran.

    Los half-lives son cortos a propósito: con ~500 sesiones diarias el test de
    Engle-Granger solo detecta con fiabilidad reversiones de hasta ~10 días.
    La semilla por defecto está elegida para que el caso trampa (TC/TD) no salga
    como falso positivo por azar, algo que ocurre en torno al 5 % de las semillas.
    """
    tech = cointegrated_pair(
        n, seed=seed, beta=1.2, alpha=0.3, half_life=5.0, end=end, names=('TA', 'TB')
    )
    energy = cointegrated_pair(
        n, seed=seed + 1, beta=0.8, alpha=0.5, half_life=7.0, end=end, names=('EA', 'EB')
    )
    trap = factor_walks(n, seed=seed + 2, names=('TC', 'TD'), end=end)
    prices = pd.DataFrame(
        {
            'TA': tech['TA'],
            'TB': tech['TB'],
            'TC': trap['TC'],
            'TD': trap['TD'],
            'EA': energy['EA'],
            'EB': energy['EB'],
            'EC': random_walk(n, seed=seed + 4, end=end, name='EC'),
        }
    )
    groups = {
        'TA': 'Tecnologia',
        'TB': 'Tecnologia',
        'TC': 'Tecnologia',
        'TD': 'Tecnologia',
        'EA': 'Energia',
        'EB': 'Energia',
        'EC': 'Energia',
    }
    truth = {frozenset(('TA', 'TB')), frozenset(('EA', 'EB'))}
    return prices, groups, truth
