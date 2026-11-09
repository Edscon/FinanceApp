"""Estadística básica para pares.

Convención: el spread de un par (y, x) se define sobre log-precios,

    spread = log(y) - alpha - beta * log(x)

con alpha y beta de una regresión OLS de log(y) sobre log(x). Un spread por
encima de su media (z > 0) significa que y está "caro" frente a x. Como beta se
estima en logaritmos, el ratio de nocionales de las dos patas es 1 : beta.
"""
from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests
from statsmodels.tsa.stattools import coint


@dataclass(frozen=True)
class HedgeFit:
    """Resultado de la regresión y = alpha + beta * x + spread."""

    alpha: float
    beta: float
    spread: pd.Series


class Cointegration(NamedTuple):
    pvalue: float  # p-valor del par, ya corregido por probar dos orientaciones
    a_is_dependent: bool  # True si la orientación elegida es "a en función de b"


def fit_hedge_ratio(y, x) -> HedgeFit:
    """OLS con constante de ``y`` sobre ``x`` (series alineadas y de igual longitud)."""
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    if y_arr.ndim != 1 or y_arr.shape != x_arr.shape:
        raise ValueError('y y x deben ser series 1-D de la misma longitud.')
    if len(y_arr) < 3:
        raise ValueError('Se necesitan al menos 3 observaciones.')
    x_centered = x_arr - x_arr.mean()
    sxx = float(np.dot(x_centered, x_centered))
    if sxx <= 0.0:
        raise ValueError('x es constante: no se puede estimar la razón de cobertura.')
    beta = float(np.dot(x_centered, y_arr - y_arr.mean()) / sxx)
    alpha = float(y_arr.mean() - beta * x_arr.mean())
    index = y.index if isinstance(y, pd.Series) else None
    spread = pd.Series(y_arr - alpha - beta * x_arr, index=index, name='spread')
    return HedgeFit(alpha=alpha, beta=beta, spread=spread)


def half_life(spread) -> float:
    """Half-life (en observaciones) de la reversión a la media de un spread.

    Ajusta un AR(1) por OLS, ``s_t = c + phi * s_{t-1} + e_t``, y devuelve
    ``-ln(2) / ln(phi)``. Devuelve ``inf`` si phi >= 1 (no revierte) y ``0.0`` si
    phi <= 0 (revierte dentro de una misma observación; en datos diarios suele
    ser ruido). El estimador de phi tiene sesgo a la baja en muestras pequeñas,
    así que el half-life tiende a subestimarse.
    """
    values = np.asarray(spread, dtype=float)
    if values.ndim != 1 or len(values) < 10:
        raise ValueError('Se necesitan al menos 10 observaciones.')
    lagged = values[:-1]
    delta = np.diff(values)
    lagged_centered = lagged - lagged.mean()
    denominator = float(np.dot(lagged_centered, lagged_centered))
    if denominator <= 0.0:
        return math.inf
    slope = float(np.dot(lagged_centered, delta - delta.mean()) / denominator)
    phi = 1.0 + slope
    if phi >= 1.0:
        return math.inf
    if phi <= 0.0:
        return 0.0
    return -math.log(2.0) / math.log(phi)


def engle_granger_pvalue(y, x, maxlag: int = 5) -> float:
    """p-valor del test de cointegración de Engle-Granger (H0: no cointegran).

    El test no es simétrico: depende de cuál es la variable dependiente. Si el
    cálculo falla (series degeneradas) se devuelve 1.0, es decir, "no cointegra".
    """
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', category=RuntimeWarning)
            _, pvalue, _ = coint(y_arr, x_arr, trend='c', maxlag=maxlag, autolag='aic')
    except (ValueError, np.linalg.LinAlgError):
        return 1.0
    return float(pvalue) if math.isfinite(pvalue) else 1.0


def pair_cointegration(a, b, maxlag: int = 5) -> Cointegration:
    """Cointegración de un par probando las dos orientaciones.

    Se queda con la orientación de menor p-valor y lo multiplica por 2
    (Bonferroni por las dos pruebas), con tope en 1. Es conservador a propósito.
    """
    p_a_on_b = engle_granger_pvalue(a, b, maxlag)
    p_b_on_a = engle_granger_pvalue(b, a, maxlag)
    return Cointegration(
        pvalue=min(1.0, 2.0 * min(p_a_on_b, p_b_on_a)),
        a_is_dependent=p_a_on_b <= p_b_on_a,
    )


def current_zscore(spread, holdout: int = 0) -> float:
    """z-score de la última observación del spread.

    La media y la desviación se calculan con todas las observaciones salvo las
    ``holdout`` últimas, para que el propio movimiento que se quiere medir no
    contamine la referencia de "lo normal". Con ``holdout=0`` se usa toda la
    serie, incluida la última. Devuelve NaN si la desviación es 0.
    """
    values = np.asarray(spread, dtype=float)
    if holdout < 0:
        raise ValueError('holdout no puede ser negativo.')
    base = values[: len(values) - holdout]
    if len(base) < 3:
        raise ValueError('Se necesitan al menos 3 observaciones de referencia.')
    std = float(base.std(ddof=1))
    if not math.isfinite(std) or std == 0.0:
        return math.nan
    return float((values[-1] - base.mean()) / std)


def fdr_bh(pvalues: Sequence[float]) -> np.ndarray:
    """p-valores ajustados por Benjamini-Hochberg (control de la tasa de falsos descubrimientos)."""
    values = np.asarray(pvalues, dtype=float)
    if values.size == 0:
        return values
    return np.asarray(multipletests(values, method='fdr_bh')[1], dtype=float)
