"""Resultados de un backtest y su conversión segura a JSON."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum

import numpy as np


def jsonsafe(value):
    """Convierte recursivamente a tipos JSON estándar.

    NaN e infinito pasan a ``None``: ``JSON.parse`` del navegador falla con ``NaN`` e ``Infinity``,
    y un profit factor infinito o un Sharpe NaN son habituales.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return jsonsafe(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonsafe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [jsonsafe(v) for v in value.tolist()]
    if hasattr(value, 'isoformat'):  # pandas.Timestamp
        return value.isoformat()
    raise TypeError(f'Tipo no convertible a JSON: {type(value).__name__}')


@dataclass
class TradeRecord:
    """Operación completa (ida y vuelta) de la estrategia de pares."""

    trade_id: str
    window: int
    y: str
    x: str
    direction: int  # +1 largo spread (largo y, corto x); -1 corto spread
    beta: float
    half_life: float
    signal_date: str
    entry_date: str
    exit_signal_date: str
    exit_date: str
    exit_reason: str  # target | stop | time | window_end | data_gap | end_of_data
    entry_z: float
    exit_z: float
    holding_sessions: int
    entry_gross_notional: float
    gross_pnl: float
    commission: float
    slippage: float
    borrow: float
    net_pnl: float

    @property
    def return_on_gross(self) -> float:
        if not self.entry_gross_notional:
            return math.nan
        return self.net_pnl / self.entry_gross_notional


@dataclass
class BacktestResult:
    """Todo lo que produce un backtest. ``daily`` son listas alineadas con ``dates``."""

    strategy: str
    config: dict
    dates: list[str]
    equity: list[float]
    cash: list[float]
    gross_exposure: list[float]
    net_exposure: list[float]
    n_positions: list[int]
    benchmark: list[float] | None
    metrics: dict
    attribution: dict
    trades: list[TradeRecord] = field(default_factory=list)
    windows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ''
    engine_version: str = ''

    def daily(self) -> dict:
        return {
            'dates': self.dates,
            'equity': self.equity,
            'cash': self.cash,
            'gross': self.gross_exposure,
            'net': self.net_exposure,
            'n_positions': self.n_positions,
            'benchmark': self.benchmark,
        }
