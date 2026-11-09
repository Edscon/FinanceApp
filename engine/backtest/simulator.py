"""Simulador diario con una única regla temporal.

En cada sesión ``t`` (desde la primera decisión ``s0`` hasta la última ``end``):

1. Préstamo de los cortos mantenidos desde el cierre de t-1: ``|acciones| x C[t-1] x bps x días/365``.
2. Se ejecutan las órdenes decididas al cierre de t-1, al cierre de t (``next_close``) o a la
   apertura de t (``next_open``; si falta la apertura, al cierre). Compra a P(1+s), venta a
   P(1-s), más comisión en bps. Si no hay precio, las órdenes de cierre se posponen y las demás
   se cancelan.
3. Una posición cuyo precio falta más de ``MAX_STALE`` sesiones se cierra a su último precio
   (``data_gap``). En la última sesión se liquida todo al cierre (``end_of_data``).
4. Valoración al cierre: ``equity = caja + Σ acciones x cierre``.
5. La estrategia decide con los datos hasta t (``MarketView``); sus órdenes se ejecutan en t+1.

Atribución: ``pnl[t, i] = valor_i(t) - valor_i(t-1) + flujo_de_caja_i(t)``. Por construcción
``Σ_i pnl[t, i] = equity(t) - equity(t-1)`` exactamente (lo comprueba un test).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np
import pandas as pd

from . import ENGINE_VERSION
from .config import BacktestConfig, Execution
from .data import MarketData
from .metrics import compute_metrics
from .result import BacktestResult, TradeRecord
from .view import MarketView

MAX_STALE = 3

STANDARD_WARNINGS = [
    'Universo y grupos son los de hoy: hay sesgo de supervivencia (faltan las empresas que '
    'quebraron o dejaron de cotizar). En una estrategia que compra perdedores, ese sesgo infla '
    'el resultado.',
    'Precios ajustados por splits y dividendos: valen para retornos, no para dimensionar órdenes '
    'reales en acciones.',
    'Sin interés sobre la caja ni sobre el colateral de los cortos (tipo libre de riesgo = 0).',
]


@dataclass
class Order:
    ticker: str
    target_shares: float
    kind: str = 'rebalance'  # open | close | rebalance
    tag: str = ''
    reason: str = ''


@dataclass
class Fill:
    t: int
    ticker: str
    delta: float
    ref_price: float
    fill_price: float
    commission: float
    slippage: float
    kind: str
    tag: str
    reason: str


@dataclass
class Book:
    """Estado de la cartera que ve la estrategia al decidir (cierre de t)."""

    t: int
    equity: float
    cash: float
    shares: dict[str, float]
    pending: set[str]

    def position(self, ticker: str) -> float:
        return self.shares.get(ticker, 0.0)


@dataclass
class SimulationLog:
    """Detalle por sesión simulada e instrumento (filas = t - s0)."""

    s0: int
    dates: pd.DatetimeIndex
    tickers: list[str]
    pnl: np.ndarray
    commission: np.ndarray
    slippage: np.ndarray
    borrow: np.ndarray
    fills: list[Fill] = field(default_factory=list)

    def row(self, t: int) -> int:
        return t - self.s0

    def date(self, t: int) -> str:
        return self.dates[t - self.s0].date().isoformat()


class Strategy(Protocol):
    name: str

    def first_decision_index(self, data: MarketData, start_idx: int) -> int: ...

    def decide(self, t: int, view: MarketView, book: Book) -> list[Order]: ...

    def on_fill(self, fill: Fill) -> None: ...

    def finalize(self, log: SimulationLog) -> tuple[list[TradeRecord], list[dict]]: ...

    def warnings(self) -> list[str]: ...


def index_range(data: MarketData, config: BacktestConfig) -> tuple[int, int]:
    """Índices (incluidos) de la primera y la última sesión dentro de [start, end]."""
    dates = data.dates
    start_idx = int(dates.searchsorted(pd.Timestamp(config.start), side='left')) if config.start else 0
    end_idx = int(dates.searchsorted(pd.Timestamp(config.end), side='right')) - 1 if config.end else len(dates) - 1
    if start_idx > end_idx or end_idx < 0:
        raise ValueError('No hay sesiones en el rango de fechas pedido.')
    return start_idx, end_idx


class _Simulation:
    def __init__(self, data: MarketData, config: BacktestConfig, strategy: Strategy, progress):
        self.data = data
        self.config = config
        self.strategy = strategy
        self.progress = progress
        self.start_idx, self.end_idx = index_range(data, config)
        close, open_, volume = data.arrays()
        stop = self.end_idx + 1  # nada posterior a la fecha final entra en la simulación
        self.C = close[:stop]
        self.O = None if open_ is None else open_[:stop]
        self.V = None if volume is None else volume[:stop]
        marked = data.close.iloc[:stop].ffill(limit=MAX_STALE).to_numpy(dtype=float, copy=True)
        marked.setflags(write=False)
        self.Cff = marked
        self.dates = data.dates[:stop]
        self.tickers = data.tickers
        self.column = {ticker: i for i, ticker in enumerate(self.tickers)}
        self.groups = data.groups

    def run(self) -> BacktestResult:
        cfg = self.config
        s0 = int(self.strategy.first_decision_index(self.data, self.start_idx))
        if s0 >= self.end_idx:
            needed = s0 - self.start_idx
            raise ValueError(
                f'Datos insuficientes: la estrategia necesita {needed} sesiones de historia antes de '
                f'operar y el rango termina antes. Amplía las fechas o importa más historia.'
            )
        self.s0 = s0
        n_days, n_assets = self.end_idx - s0 + 1, len(self.tickers)
        self.pnl = np.zeros((n_days, n_assets))
        self.commission_m = np.zeros((n_days, n_assets))
        self.slippage_m = np.zeros((n_days, n_assets))
        self.borrow_m = np.zeros((n_days, n_assets))
        self.shares = np.zeros(n_assets)
        self.cash = float(cfg.capital)
        self.position_tag = [''] * n_assets
        self.fills: list[Fill] = []
        self.traded = 0.0
        self.counters = {'open_fallback': 0, 'cancelled_orders': 0, 'postponed': 0, 'data_gap': 0}
        self.c_rate = cfg.costs.commission_bps / 1e4
        self.s_rate = cfg.costs.slippage_bps / 1e4
        b_rate = cfg.costs.borrow_bps_annual / 1e4
        use_open = cfg.execution == Execution.NEXT_OPEN and self.O is not None

        equity_s, cash_s, gross_s, net_s, npos_s = [], [], [], [], []
        prev_value = np.zeros(n_assets)
        last_mark = np.full(n_assets, np.nan)
        pending: dict[int, Order] = {}
        step = max(1, n_days // 50)

        for t in range(s0, self.end_idx + 1):
            self.row = t - s0
            self.t = t
            self.cashflow = np.zeros(n_assets)

            # 1. Préstamo de los cortos mantenidos durante la noche (t-1 -> t).
            if t > s0:
                short = self.shares < 0
                if short.any():
                    days = (self.dates[t] - self.dates[t - 1]).days
                    previous = self.Cff[t - 1]
                    ok = short & np.isfinite(previous)
                    cost = np.where(ok, -self.shares * np.nan_to_num(previous) * b_rate * days / 365.0, 0.0)
                    self.cash -= float(cost.sum())
                    self.cashflow -= cost
                    self.borrow_m[self.row] = cost

            # 2. Ejecución de las órdenes decididas en t-1.
            if t > s0 and pending:
                still_pending = {}
                for i in sorted(pending):
                    order = pending[i]
                    if use_open:
                        ref = self.O[t, i]
                        if not (math.isfinite(ref) and ref > 0):
                            ref = self.C[t, i]
                            if math.isfinite(ref):
                                self.counters['open_fallback'] += 1
                    else:
                        ref = self.C[t, i]
                    if not (math.isfinite(ref) and ref > 0):
                        if order.kind == 'close':
                            still_pending[i] = order
                            self.counters['postponed'] += 1
                        else:
                            self.counters['cancelled_orders'] += 1
                        continue
                    self._execute(i, order.target_shares, float(ref), order.kind, order.tag, order.reason)
                pending = still_pending

            # 3. Posiciones sin precio durante más de MAX_STALE sesiones: cierre al último precio.
            stale = (self.shares != 0) & ~np.isfinite(self.Cff[t])
            for i in np.flatnonzero(stale):
                if math.isfinite(last_mark[i]):
                    self.counters['data_gap'] += 1
                    self._execute(i, 0.0, float(last_mark[i]), 'close', self.position_tag[i], 'data_gap')
                    pending.pop(i, None)

            # 4. Última sesión: se liquida todo al cierre.
            if t == self.end_idx:
                for i in np.flatnonzero(self.shares != 0):
                    self._execute(i, 0.0, float(self.Cff[t, i]), 'close', self.position_tag[i], 'end_of_data')
                pending.clear()

            # 5. Valoración al cierre.
            marks = self.Cff[t]
            value = np.where(self.shares != 0, self.shares * np.nan_to_num(marks), 0.0)
            self.pnl[self.row] = value - prev_value + self.cashflow
            prev_value = value
            last_mark = np.where(np.isfinite(marks), marks, last_mark)
            equity = self.cash + float(value.sum())
            equity_s.append(equity)
            cash_s.append(self.cash)
            gross_s.append(float(np.abs(value).sum()))
            net_s.append(float(value.sum()))
            npos_s.append(int(np.count_nonzero(self.shares)))

            # 6. Decisión con los datos hasta t; se ejecuta en t+1.
            if t < self.end_idx:
                view = MarketView(self.C, self.O, self.V, self.dates, self.tickers, self.groups, t)
                held = {self.tickers[i]: float(self.shares[i]) for i in np.flatnonzero(self.shares)}
                book = Book(t, equity, self.cash, held, {self.tickers[i] for i in pending})
                for order in self.strategy.decide(t, view, book) or []:
                    pending[self.column[order.ticker]] = order

            if self.progress and (self.row % step == 0 or t == self.end_idx):
                self.progress((self.row + 1) / n_days, f'Simulando {self.dates[t].date().isoformat()}')

        return self._result(equity_s, cash_s, gross_s, net_s, npos_s)

    def _execute(self, i: int, target: float, ref: float, kind: str, tag: str, reason: str) -> None:
        current = self.shares[i]
        delta = target - current
        if abs(delta) * ref < 1e-9:
            return
        side = 1.0 if delta > 0 else -1.0
        fill_price = ref * (1.0 + side * self.s_rate)
        slippage = abs(delta) * ref * self.s_rate
        commission = abs(delta) * ref * self.c_rate
        flow = delta * fill_price + commission
        self.cash -= flow
        self.cashflow[i] -= flow
        self.commission_m[self.row, i] += commission
        self.slippage_m[self.row, i] += slippage
        self.traded += abs(delta) * ref
        self.shares[i] = 0.0 if abs(target) < 1e-12 else target
        if kind != 'close' and tag:
            self.position_tag[i] = tag
        fill = Fill(self.t, self.tickers[i], delta, ref, fill_price, commission, slippage, kind, tag, reason)
        self.fills.append(fill)
        self.strategy.on_fill(fill)

    def _result(self, equity_s, cash_s, gross_s, net_s, npos_s) -> BacktestResult:
        dates = self.dates[self.s0 : self.end_idx + 1]
        log = SimulationLog(
            self.s0, dates, self.tickers, self.pnl, self.commission_m, self.slippage_m, self.borrow_m, self.fills
        )
        trades, windows = self.strategy.finalize(log)
        benchmark = None
        if self.data.benchmark is not None:
            series = self.data.benchmark.iloc[self.s0 : self.end_idx + 1].ffill()
            if series.notna().any():
                benchmark = [None if not math.isfinite(v) else float(v) for v in series.to_numpy(dtype=float)]
        equity = np.asarray(equity_s, dtype=float)
        bench_array = None if benchmark is None else np.array([np.nan if v is None else v for v in benchmark])
        metrics = compute_metrics(
            dates,
            equity,
            gross_exposure=np.asarray(gross_s, dtype=float),
            benchmark=bench_array,
            traded_notional=self.traded,
            commission=float(self.commission_m.sum()),
            slippage=float(self.slippage_m.sum()),
            borrow=float(self.borrow_m.sum()),
            trades=trades,
        )
        summary = getattr(self.strategy, 'summary', None)
        metrics['strategy'] = summary() if callable(summary) else {}
        warnings = list(STANDARD_WARNINGS) + list(self.strategy.warnings())
        if self.config.execution == Execution.NEXT_OPEN and self.O is None:
            warnings.append('Se pidió ejecutar a la apertura pero no hay aperturas: se ejecutó al cierre.')
        if self.counters['open_fallback']:
            warnings.append(f"{self.counters['open_fallback']} órdenes sin precio de apertura se ejecutaron al cierre.")
        if self.counters['cancelled_orders']:
            warnings.append(f"{self.counters['cancelled_orders']} órdenes se cancelaron por falta de precio.")
        if self.counters['data_gap']:
            warnings.append(f"{self.counters['data_gap']} posiciones se cerraron por un hueco de datos de más de {MAX_STALE} sesiones.")
        return BacktestResult(
            strategy=self.strategy.name,
            config=self.config.to_dict(),
            dates=[d.date().isoformat() for d in dates],
            equity=equity_s,
            cash=cash_s,
            gross_exposure=gross_s,
            net_exposure=net_s,
            n_positions=npos_s,
            benchmark=benchmark,
            metrics=metrics,
            attribution=attribution(log, self.groups, trades),
            trades=trades,
            windows=windows,
            warnings=warnings,
            fingerprint=self.data.fingerprint(),
            engine_version=ENGINE_VERSION,
        )


def attribution(log: SimulationLog, groups: dict, trades: list[TradeRecord]) -> dict:
    net = log.pnl.sum(axis=0)
    commission = log.commission.sum(axis=0)
    slippage = log.slippage.sum(axis=0)
    borrow = log.borrow.sum(axis=0)
    by_ticker = {}
    for i, ticker in enumerate(log.tickers):
        if net[i] == 0 and commission[i] == 0 and borrow[i] == 0:
            continue
        costs = commission[i] + slippage[i] + borrow[i]
        by_ticker[ticker] = {
            'group': groups.get(ticker, 'Sin grupo'),
            'net': float(net[i]),
            'gross': float(net[i] + costs),
            'costs': float(costs),
        }
    by_group: dict[str, dict] = {}
    for item in by_ticker.values():
        bucket = by_group.setdefault(item['group'], {'net': 0.0, 'gross': 0.0, 'costs': 0.0, 'n': 0})
        bucket['net'] += item['net']
        bucket['gross'] += item['gross']
        bucket['costs'] += item['costs']
        bucket['n'] += 1
    by_reason: dict[str, dict] = {}
    by_pair: dict[str, dict] = {}
    for trade in trades:
        reason = by_reason.setdefault(trade.exit_reason, {'n': 0, 'net': 0.0})
        reason['n'] += 1
        reason['net'] += trade.net_pnl
        pair = by_pair.setdefault(f'{trade.y}/{trade.x}', {'n': 0, 'net': 0.0, 'wins': 0})
        pair['n'] += 1
        pair['net'] += trade.net_pnl
        pair['wins'] += int(trade.net_pnl > 0)
    return {'by_ticker': by_ticker, 'by_group': by_group, 'by_exit_reason': by_reason, 'by_pair': by_pair}


def run_backtest(
    data: MarketData,
    config: BacktestConfig,
    strategy: Strategy | None = None,
    *,
    progress: Callable[[float, str], None] | None = None,
    selections=None,
) -> BacktestResult:
    """Ejecuta un backtest completo. ``selections`` (pares) permite reutilizar una selección
    cacheada; ``progress(fracción, mensaje)`` informa del avance."""
    if strategy is None:
        from .strategies import build_strategy

        strategy = build_strategy(config, data, selections=selections, progress=progress)
    return _Simulation(data, config, strategy, progress).run()
