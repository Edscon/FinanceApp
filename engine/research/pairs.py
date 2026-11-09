"""Búsqueda de pares correlacionados y cointegrados, con su z-score actual.

Pipeline (sobre precios ajustados de cierre):

1. Ventana de las últimas ``lookback`` sesiones; se descartan tickers con huecos.
   Las últimas ``holdout`` sesiones (1 por defecto) NO se usan para estimar nada:
   solo para medir el gap. Si entraran en la estimación, un salto grande en la
   última sesión empeoraría el propio test que califica al par y podría hacerlo
   desaparecer justo cuando más interesa.
2. Candidatos: pares del mismo grupo (sector/industria) con correlación de
   retornos >= ``min_corr``. La correlación solo *preselecciona*.
3. Test de cointegración de Engle-Granger a cada candidato, corrigiendo por el
   número de pares probados (Benjamini-Hochberg).
4. Para los supervivientes: razón de cobertura, half-life y z-score del spread,
   más un chequeo de estabilidad (cointegración en cada mitad de la ventana).

Los parámetros se estiman con datos anteriores al gap, pero siguen siendo *en
muestra* respecto a la ventana: es un escáner, no una validación. La validación
fuera de muestra y con costes es la Fase 2 (backtesting).

Potencia: con ~500 sesiones diarias el test de Engle-Granger detecta casi
siempre reversiones de hasta ~10 días de half-life, pero solo ~2/3 de las de 15
días y ~1/4 de las de 30 (simulado; reprodúcelo con
``python -m engine.calibration``). Para half-lives largos hace falta más
historia (``lookback`` mayor), a costa de más riesgo de cambios estructurales.
"""
from __future__ import annotations

import datetime as dt
import itertools
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .stats import (
    current_zscore,
    engle_granger_pvalue,
    fdr_bh,
    fit_hedge_ratio,
    half_life,
    pair_cointegration,
)


@dataclass(frozen=True)
class ScanConfig:
    lookback: int = 504  # sesiones de la ventana (~2 años), holdout incluido
    holdout: int = 1  # últimas sesiones excluidas de la estimación (solo miden el gap)
    min_corr: float = 0.70  # correlación mínima de retornos para ser candidato
    max_p_adj: float = 0.05  # p-valor máximo tras la corrección FDR
    min_half_life: float = 2.0  # días
    max_half_life: float = 60.0  # días
    maxlag: int = 5  # retardos máximos del test ADF dentro de Engle-Granger
    stability_p: float = 0.10  # umbral (informativo) en cada mitad de la ventana
    min_obs: int = 100  # sesiones mínimas para analizar
    max_fill: int = 3  # huecos consecutivos que se rellenan hacia delante

    def __post_init__(self):
        if self.min_obs < 30:
            raise ValueError('min_obs debe ser >= 30.')
        if not 0 <= self.holdout < 20:
            raise ValueError('holdout debe estar entre 0 y 19.')
        if self.lookback - self.holdout < self.min_obs:
            raise ValueError(
                f'lookback - holdout ({self.lookback - self.holdout}) debe ser >= '
                f'min_obs ({self.min_obs}).'
            )
        if not 0.0 < self.max_p_adj <= 1.0:
            raise ValueError('max_p_adj debe estar en (0, 1].')
        if not 0.0 <= self.min_half_life < self.max_half_life:
            raise ValueError('Se exige 0 <= min_half_life < max_half_life.')


@dataclass(frozen=True)
class PairResult:
    y: str  # variable dependiente de la regresión
    x: str  # variable de cobertura
    group: str | None
    corr: float  # correlación de retornos diarios
    alpha: float
    beta: float  # ratio de nocionales x/y de 1 : beta
    pvalue: float  # Engle-Granger (mejor orientación, x2 Bonferroni)
    p_adj: float  # tras FDR sobre todos los pares probados
    half_life: float  # días
    z: float  # z-score actual del spread (z > 0: y caro frente a x)
    stable: bool  # cointegración también en cada mitad de la ventana (informativo)


@dataclass(frozen=True)
class ScanReport:
    as_of: dt.date
    n_obs: int
    config: ScanConfig
    tickers_used: tuple[str, ...]
    tickers_dropped: dict[str, str]  # ticker -> motivo
    n_tested: int  # pares que pasaron el filtro de correlación y se probaron
    pairs: list[PairResult]  # supervivientes, ordenados por p_adj


def trade_legs(pair: PairResult, entry_z: float = 2.0) -> tuple[str, str] | None:
    """Devuelve ``(corto, largo)`` si |z| >= entry_z, o ``None`` si no hay gap.

    z > 0: el spread está por encima de su media, y está caro frente a x
    (corto y, largo x). z < 0: al revés.
    """
    if not math.isfinite(pair.z) or abs(pair.z) < entry_z:
        return None
    return (pair.y, pair.x) if pair.z > 0 else (pair.x, pair.y)


def find_pairs(
    prices: pd.DataFrame,
    groups: Mapping[str, str] | None = None,
    config: ScanConfig | None = None,
) -> ScanReport:
    """Escanea ``prices`` (índice = fechas, columnas = tickers, precios ajustados).

    ``groups`` asigna un grupo (p. ej. sector) a cada ticker. Si se indica, solo
    se emparejan tickers del mismo grupo y los que no tengan grupo se ignoran.
    Si es ``None`` se consideran todos los pares.
    """
    cfg = config or ScanConfig()
    if prices is None or prices.empty:
        raise ValueError('No hay precios que analizar.')

    window = prices.sort_index().iloc[-cfg.lookback:].ffill(limit=cfg.max_fill)
    dropped: dict[str, str] = {}
    for column in window.columns:
        series = window[column]
        if series.isna().any():
            dropped[str(column)] = 'datos incompletos en la ventana'
        elif (series <= 0).any():
            dropped[str(column)] = 'precios no positivos'
    used = [str(c) for c in window.columns if str(c) not in dropped]
    window = window.loc[:, [c for c in window.columns if str(c) in used]]
    if len(window) - cfg.holdout < cfg.min_obs:
        raise ValueError(
            f'Solo hay {len(window)} sesiones ({cfg.holdout} de holdout); '
            f'se necesitan al menos {cfg.min_obs} para estimar.'
        )

    last = window.index[-1]
    as_of = last.date() if hasattr(last, 'date') else last
    log_prices = np.log(window.astype(float))
    log_prices.columns = used
    # Todo lo que califica al par (correlación, cointegración, beta, half-life) se
    # estima sin las últimas ``holdout`` sesiones: estas solo sirven para medir el gap.
    formation = log_prices.iloc[: len(log_prices) - cfg.holdout]

    candidates = _candidate_pairs(formation, used, groups, cfg)

    tested = []
    for a, b, group, corr in candidates:
        result = pair_cointegration(formation[a], formation[b], cfg.maxlag)
        tested.append((a, b, group, corr, result))
    p_adj = fdr_bh([t[4].pvalue for t in tested])

    pairs: list[PairResult] = []
    for (a, b, group, corr, result), adjusted in zip(tested, p_adj):
        if adjusted > cfg.max_p_adj:
            continue
        y, x = (a, b) if result.a_is_dependent else (b, a)
        fit = fit_hedge_ratio(formation[y], formation[x])
        hl = half_life(fit.spread)
        if not cfg.min_half_life <= hl <= cfg.max_half_life:
            continue
        spread = log_prices[y] - fit.alpha - fit.beta * log_prices[x]
        z = current_zscore(spread, holdout=cfg.holdout)
        if not math.isfinite(z):
            continue
        pairs.append(
            PairResult(
                y=y,
                x=x,
                group=group,
                corr=float(corr),
                alpha=fit.alpha,
                beta=fit.beta,
                pvalue=float(result.pvalue),
                p_adj=float(adjusted),
                half_life=float(hl),
                z=float(z),
                stable=_is_stable(formation[y], formation[x], cfg),
            )
        )
    pairs.sort(key=lambda p: (p.p_adj, -abs(p.z)))

    return ScanReport(
        as_of=as_of,
        n_obs=len(window),
        config=cfg,
        tickers_used=tuple(used),
        tickers_dropped=dropped,
        n_tested=len(tested),
        pairs=pairs,
    )


def _candidate_pairs(log_prices, used, groups, cfg):
    """Pares (a, b, grupo, correlación) del mismo grupo con correlación >= min_corr."""
    returns = log_prices.diff().iloc[1:]
    corr = returns.corr().to_numpy()
    position = {ticker: i for i, ticker in enumerate(used)}

    if groups is None:
        buckets: dict[str | None, list[str]] = {None: list(used)}
    else:
        buckets = defaultdict(list)
        for ticker in used:
            label = groups.get(ticker)
            if label:
                buckets[label].append(ticker)

    candidates = []
    for label, members in buckets.items():
        for a, b in itertools.combinations(members, 2):
            value = corr[position[a], position[b]]
            if value >= cfg.min_corr:  # un NaN nunca cumple la condición
                candidates.append((a, b, label, float(value)))
    return candidates


def _is_stable(log_y: pd.Series, log_x: pd.Series, cfg: ScanConfig) -> bool:
    """¿Sigue cointegrando en cada mitad de la ventana? (diagnóstico, no un filtro)."""
    middle = len(log_y) // 2
    for part in (slice(0, middle), slice(middle, None)):
        if engle_granger_pvalue(log_y.iloc[part], log_x.iloc[part], cfg.maxlag) > cfg.stability_p:
            return False
    return True
