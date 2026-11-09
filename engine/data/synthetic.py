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


def _ar1(rng, n: int, half_life: float, sd: float) -> np.ndarray:
    phi = 0.5 ** (1.0 / half_life)
    shocks = sd * math.sqrt(1.0 - phi * phi) * rng.standard_normal(n)
    out = np.empty(n)
    out[0] = sd * rng.standard_normal()
    for t in range(1, n):
        out[t] = phi * out[t - 1] + shocks[t]
    return out


def synthetic_market(n: int = 1500, *, seed: int = 11, prefix: str = 'DEMO-', end: dt.date | str = DEFAULT_END) -> dict:
    """Mercado sintético con OHLC, volumen y estructura conocida, para la demo y los tests.

    Grupos (26 valores, todos con el prefijo ``prefix`` para no chocar con tickers reales):

    * «Reversión A» y «Reversión B» (6 + 6): cada valor tiene un componente propio que revierte a
      la media (half-life 4 días): la reversión intra-sector debería ganar aquí.
    * «Ruido» (6): solo caminos aleatorios propios; control nulo, aquí no debería ganar.
    * «Pares» (8): dos pares cointegrados (half-life 5 y 8), un par trampa correlacionado pero sin
      cointegración, y un par cointegrado que se rompe al 60 % de la muestra.

    Devuelve ``{'close', 'open', 'volume', 'groups', 'truth'}``.
    """
    rng = np.random.default_rng(seed)
    index = _index(n, end)
    market = np.cumsum(0.0003 + 0.009 * rng.standard_normal(n))
    logs: dict[str, np.ndarray] = {}
    groups: dict[str, str] = {}

    def add(code, path, group):
        logs[f'{prefix}{code}'] = path
        groups[f'{prefix}{code}'] = group

    for code, group in (('RA', 'Reversión A'), ('RB', 'Reversión B')):
        factor = np.cumsum(0.006 * rng.standard_normal(n))
        for k in range(6):
            own = _ar1(rng, n, half_life=4.0, sd=0.02) + np.cumsum(0.004 * rng.standard_normal(n))
            add(f'{code}{k + 1}', math.log(40 + 10 * k) + market + factor + own, group)

    factor = np.cumsum(0.006 * rng.standard_normal(n))
    for k in range(6):
        add(f'RU{k + 1}', math.log(30 + 5 * k) + market + factor + np.cumsum(0.012 * rng.standard_normal(n)), 'Ruido')

    def x_path():
        return math.log(60) + market + np.cumsum(0.01 * rng.standard_normal(n))

    x1 = x_path()
    add('P1X', x1, 'Pares')
    add('P1Y', 0.3 + 1.0 * x1 + _ar1(rng, n, half_life=5.0, sd=0.02), 'Pares')
    x2 = x_path()
    add('P2X', x2, 'Pares')
    add('P2Y', 0.1 + 0.9 * x2 + _ar1(rng, n, half_life=8.0, sd=0.02), 'Pares')

    common = market + np.cumsum(0.008 * rng.standard_normal(n))
    add('TRC', math.log(50) + common + np.cumsum(0.006 * rng.standard_normal(n)), 'Pares')
    add('TRD', math.log(55) + common + np.cumsum(0.006 * rng.standard_normal(n)), 'Pares')

    break_at = int(0.6 * n)
    x3 = x_path()
    drift = np.concatenate([np.zeros(break_at), np.cumsum(0.012 * rng.standard_normal(n - break_at))])
    add('BKX', x3, 'Pares')
    add('BKY', 0.2 + 1.0 * x3 + _ar1(rng, n, half_life=5.0, sd=0.02) + drift, 'Pares')

    log_close = pd.DataFrame(logs, index=index)
    close = np.exp(log_close)
    daily = log_close.diff()
    overnight = 0.35 * daily + 0.002 * rng.standard_normal(log_close.shape)
    log_open = log_close.shift(1) + overnight
    log_open.iloc[0] = log_close.iloc[0]
    opens = np.exp(log_open)
    volume = pd.DataFrame(
        np.exp(math.log(2e6) + 0.3 * rng.standard_normal(log_close.shape)).round(), index=index, columns=log_close.columns
    )
    p = prefix
    truth = {
        'reversal_groups': ['Reversión A', 'Reversión B'],
        'noise_group': 'Ruido',
        'pairs': [(f'{p}P1Y', f'{p}P1X'), (f'{p}P2Y', f'{p}P2X')],
        'trap': (f'{p}TRC', f'{p}TRD'),
        'broken': (f'{p}BKY', f'{p}BKX'),
        'break_at': index[break_at].date().isoformat(),
    }
    return {'close': close, 'open': opens, 'volume': volume, 'groups': groups, 'truth': truth}
