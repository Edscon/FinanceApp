"""Pares cointegrados en walk-forward.

- Ventanas: formación de ``formation`` sesiones seguida de operativa de ``trading`` sesiones.
- Al cierre del último día de formación se eligen los pares con ``find_pairs`` usando SOLO esa
  formación (``holdout=0``). α, β y la σ del spread quedan fijos durante toda la operativa.
- Cada cierre de la operativa: ``z = (log y - α - β log x - μ) / σ``.
  Entrada si entry_z <= |z| < stop_z (y, con ``require_cross``, si ayer no lo estaba).
  Salida al revertir hasta exit_z, stop en stop_z, time stop a ``max_hold_hl`` half-lives y cierre
  al final de la ventana. No hay entradas el último día de la ventana.
- Todas las órdenes se ejecutan en t+1 (las ejecuta el simulador).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from dataclasses import dataclass, field

import pandas as pd

from engine.research.pairs import ScanConfig, find_pairs

from ..config import PairsRules
from ..result import TradeRecord
from ..simulator import Book, Fill, Order, SimulationLog
from ..view import MarketView
from .base import BaseStrategy

SELECTION_VERSION = 1


@dataclass(frozen=True)
class Window:
    index: int
    f_start: int
    f_end: int
    t_start: int
    t_end: int


def make_windows(formation: int, trading: int, start_idx: int, end_idx: int) -> list[Window]:
    windows = []
    t = max(start_idx, formation)
    while t <= end_idx:
        t_end = min(t + trading - 1, end_idx)
        windows.append(Window(len(windows), t - formation, t - 1, t, t_end))
        t = t_end + 1
    return windows


@dataclass(frozen=True)
class SelectedPair:
    y: str
    x: str
    group: str | None
    alpha: float
    beta: float
    spread_mean: float
    spread_std: float
    half_life: float
    p_adj: float
    corr: float


def select_pairs(close: pd.DataFrame, groups: dict, windows: list[Window], rules: PairsRules, progress=None):
    """Para cada ventana, los pares elegidos con datos de su formación (y nada posterior)."""
    scan = ScanConfig(
        lookback=rules.formation,
        holdout=0,
        min_corr=rules.min_corr,
        max_p_adj=rules.max_p_adj,
        min_half_life=rules.min_half_life,
        max_half_life=rules.max_half_life,
        min_obs=min(100, rules.formation),
    )
    use_groups = groups if (rules.use_groups and groups) else None
    selections = []
    for k, window in enumerate(windows):
        frame = close.iloc[window.f_start : window.f_end + 1]
        try:
            report = find_pairs(frame, use_groups, scan)
        except ValueError:
            selections.append([])
            continue
        chosen, used = [], set()
        for p in sorted(report.pairs, key=lambda p: (p.p_adj, p.y, p.x)):
            if len(chosen) >= rules.max_pairs:
                break
            if not rules.beta_min <= p.beta <= rules.beta_max:
                continue
            if p.y in used or p.x in used or not (math.isfinite(p.spread_std) and p.spread_std > 0):
                continue
            chosen.append(
                SelectedPair(p.y, p.x, p.group, p.alpha, p.beta, 0.0, p.spread_std, p.half_life, p.p_adj, p.corr)
            )
            used |= {p.y, p.x}
        selections.append(chosen)
        if progress:
            progress((k + 1) / len(windows), f'Seleccionando pares: ventana {k + 1} de {len(windows)}')
    return selections


def selections_to_json(selections) -> list:
    return [[dataclasses.asdict(p) for p in window] for window in selections]


def selections_from_json(data) -> list:
    return [[SelectedPair(**p) for p in window] for window in data]


def selection_key(fingerprint: str, rules: PairsRules, windows: list[Window]) -> str:
    relevant = {
        k: getattr(rules, k)
        for k in ('formation', 'trading', 'max_pairs', 'min_corr', 'max_p_adj', 'min_half_life',
                  'max_half_life', 'beta_min', 'beta_max', 'use_groups')
    }
    payload = json.dumps(
        {'v': SELECTION_VERSION, 'fp': fingerprint, 'rules': relevant, 'windows': [dataclasses.astuple(w) for w in windows]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class _Trade:
    id: str
    window: int
    pair: SelectedPair
    direction: int  # +1 largo spread (largo y, corto x); -1 corto spread
    signal_t: int
    entry_z: float
    max_hold: int
    gross: float
    entry_fill_t: int | None = None
    exit_signal_t: int | None = None
    exit_z: float = math.nan
    exit_reason: str = ''
    exit_fill_t: int | None = None
    legs_open: set = field(default_factory=set)
    closing: bool = False

    @property
    def key(self):
        return (self.pair.y, self.pair.x)

    @property
    def legs(self):
        return (self.pair.y, self.pair.x)


class PairsWalkForward(BaseStrategy):
    name = 'pairs'

    def __init__(self, rules: PairsRules, tickers: list[str], windows: list[Window], selections: list):
        self.rules = rules
        self.column = {t: i for i, t in enumerate(tickers)}
        self.windows = windows
        self.selections = selections
        self._window_of = {}
        for w in windows:
            for t in range(w.t_start, w.t_end + 1):
                self._window_of[t] = w
        self.open: dict[tuple, _Trade] = {}
        self.by_id: dict[str, _Trade] = {}
        self.done: list[_Trade] = []
        self.blocked: set = set()
        self.current_window = None
        self._count = 0

    def first_decision_index(self, data, start_idx: int) -> int:
        if not self.windows:
            return len(data.dates) + self.rules.formation  # el simulador avisará de datos insuficientes
        return self.windows[0].t_start

    def _z(self, pair: SelectedPair, prices) -> float:
        y, x = prices[self.column[pair.y]], prices[self.column[pair.x]]
        if not (math.isfinite(y) and math.isfinite(x) and y > 0 and x > 0):
            return math.nan
        spread = math.log(y) - pair.alpha - pair.beta * math.log(x)
        return (spread - pair.spread_mean) / pair.spread_std

    def _close(self, trade: _Trade, t: int, z: float, reason: str, book: Book) -> list[Order]:
        if not trade.closing:
            trade.closing = True
            trade.exit_signal_t = t
            trade.exit_z = z
            trade.exit_reason = reason
        return [Order(leg, 0.0, 'close', trade.id, trade.exit_reason) for leg in trade.legs
                if book.position(leg) != 0 and leg not in book.pending]

    def decide(self, t: int, view: MarketView, book: Book) -> list[Order]:
        window = self._window_of.get(t)
        if window is None:
            return []
        if self.current_window != window.index:
            self.current_window = window.index
            self.blocked = set()
        r = self.rules
        prices = view.close[t]
        orders: list[Order] = []

        for key, trade in list(self.open.items()):
            held = [book.position(leg) != 0 for leg in trade.legs]
            pending = any(leg in book.pending for leg in trade.legs)
            if trade.entry_fill_t is None:
                if not any(held) and not pending:  # la entrada no pudo ejecutarse (sin precio)
                    del self.open[key]
                continue
            if trade.closing:
                orders += self._close(trade, t, math.nan, trade.exit_reason, book)
                continue
            if not all(held) and not pending:  # una pata se cerró por hueco de datos
                orders += self._close(trade, t, math.nan, 'data_gap', book)
                continue
            z = self._z(trade.pair, prices)
            reason = None
            if trade.window != window.index or t >= window.t_end:
                reason = 'window_end'
            elif math.isfinite(z):
                if (trade.direction == 1 and z <= -r.stop_z) or (trade.direction == -1 and z >= r.stop_z):
                    reason = 'stop'
                elif (trade.direction == 1 and z >= -r.exit_z) or (trade.direction == -1 and z <= r.exit_z):
                    reason = 'target'
                elif t - trade.signal_t >= trade.max_hold:
                    reason = 'time'
            if reason:
                orders += self._close(trade, t, z, reason, book)
                if reason == 'stop':
                    self.blocked.add(key)

        if t < window.t_end:
            busy = {leg for trade in self.open.values() for leg in trade.legs} | set(book.shares) | set(book.pending)
            capacity = r.max_pairs - len(self.open)
            previous = view.close[t - 1] if t > 0 else None
            for pair in self.selections[window.index]:
                if capacity <= 0:
                    break
                key = (pair.y, pair.x)
                if key in self.open or key in self.blocked or pair.y in busy or pair.x in busy:
                    continue
                z = self._z(pair, prices)
                if not math.isfinite(z) or not (r.entry_z <= abs(z) < r.stop_z):
                    continue
                if r.require_cross:
                    z_prev = self._z(pair, previous) if previous is not None else math.nan
                    if not math.isfinite(z_prev) or abs(z_prev) >= r.entry_z:
                        continue
                direction = -1 if z > 0 else 1
                gross = r.gross_per_pair * book.equity
                beta = pair.beta
                y_notional, x_notional = gross / (1 + beta), beta * gross / (1 + beta)
                self._count += 1
                trade = _Trade(
                    id=f'w{window.index}-{pair.y}-{pair.x}-{self._count}',
                    window=window.index,
                    pair=pair,
                    direction=direction,
                    signal_t=t,
                    entry_z=z,
                    max_hold=max(1, math.ceil(r.max_hold_hl * pair.half_life)),
                    gross=gross,
                )
                orders.append(Order(pair.y, direction * y_notional / prices[self.column[pair.y]], 'open', trade.id, 'entry'))
                orders.append(Order(pair.x, -direction * x_notional / prices[self.column[pair.x]], 'open', trade.id, 'entry'))
                self.open[key] = trade
                self.by_id[trade.id] = trade
                busy |= {pair.y, pair.x}
                capacity -= 1
        return orders

    def on_fill(self, fill: Fill) -> None:
        trade = self.by_id.get(fill.tag)
        if trade is None:
            return
        if fill.kind == 'open':
            if trade.entry_fill_t is None:
                trade.entry_fill_t = fill.t
            trade.legs_open.add(fill.ticker)
            return
        trade.legs_open.discard(fill.ticker)
        if not trade.closing:  # cierre forzado por el simulador (data_gap / end_of_data)
            trade.closing = True
            trade.exit_signal_t = fill.t
            trade.exit_reason = fill.reason or 'data_gap'
        if not trade.legs_open and trade.exit_fill_t is None:
            trade.exit_fill_t = fill.t
            self.done.append(trade)
            self.open.pop(trade.key, None)

    def finalize(self, log: SimulationLog):
        records = []
        for trade in sorted(self.done, key=lambda tr: (tr.entry_fill_t or 0, tr.id)):
            if trade.entry_fill_t is None or trade.exit_fill_t is None:
                continue
            rows = slice(log.row(trade.entry_fill_t), log.row(trade.exit_fill_t) + 1)
            cols = [log.tickers.index(leg) for leg in trade.legs]
            net = float(log.pnl[rows][:, cols].sum())
            commission = float(log.commission[rows][:, cols].sum())
            slippage = float(log.slippage[rows][:, cols].sum())
            borrow = float(log.borrow[rows][:, cols].sum())
            records.append(
                TradeRecord(
                    trade_id=trade.id,
                    window=trade.window,
                    y=trade.pair.y,
                    x=trade.pair.x,
                    direction=trade.direction,
                    beta=trade.pair.beta,
                    half_life=trade.pair.half_life,
                    signal_date=log.date(trade.signal_t),
                    entry_date=log.date(trade.entry_fill_t),
                    exit_signal_date=log.date(trade.exit_signal_t if trade.exit_signal_t is not None else trade.exit_fill_t),
                    exit_date=log.date(trade.exit_fill_t),
                    exit_reason=trade.exit_reason,
                    entry_z=trade.entry_z,
                    exit_z=trade.exit_z,
                    holding_sessions=trade.exit_fill_t - trade.entry_fill_t,
                    entry_gross_notional=trade.gross,
                    gross_pnl=net + commission + slippage + borrow,
                    commission=commission,
                    slippage=slippage,
                    borrow=borrow,
                    net_pnl=net,
                )
            )
        trades_per_window = {}
        for record in records:
            trades_per_window[record.window] = trades_per_window.get(record.window, 0) + 1
        windows = []
        for w, selection in zip(self.windows, self.selections):
            if w.t_start < log.s0:
                continue
            windows.append({
                'index': w.index,
                'trading_start': log.date(w.t_start),
                'trading_end': log.date(w.t_end),
                'pairs': [dataclasses.asdict(p) for p in selection],
                'n_trades': trades_per_window.get(w.index, 0),
            })
        return records, windows

    def warnings(self) -> list[str]:
        empty = sum(1 for s in self.selections if not s)
        out = []
        if empty:
            out.append(f'En {empty} de {len(self.selections)} ventanas no se encontró ningún par que cumpla los filtros.')
        return out

    def summary(self) -> dict:
        sizes = [len(s) for s in self.selections]
        return {
            'windows': len(self.windows),
            'avg_pairs_per_window': sum(sizes) / len(sizes) if sizes else 0.0,
            'distinct_pairs': len({(p.y, p.x) for s in self.selections for p in s}),
        }
