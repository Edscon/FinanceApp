"""Reversión relativa intra-sector ("gap frente a su grupo").

Cada ``rebalance`` sesiones, con datos hasta el cierre de t:

1. Elegibles: historia completa en la ventana de volatilidad, precio >= ``min_price`` y, si hay
   volumen y umbral, mediana de volumen en $ suficiente. Grupos con < ``min_group_size`` elegibles
   no se operan.
2. Gap de cada valor: rendimiento log de las últimas ``lookback`` sesiones menos la media de su
   grupo SIN él, dividido por su volatilidad diaria (``vol_window``) x sqrt(lookback): cuántas
   sigmas se ha separado de los suyos.
3. Pesos: dentro de cada grupo, ``w ∝ -(gap - media del grupo)``: largo en los rezagados, corto en
   los adelantados. Se escalan a la exposición bruta ``gross``, se recortan a ``max_weight`` y cada
   grupo se equilibra para ser neutral en dólares (el lado mayor se reduce al menor).

Referencias: Da, Liu & Schaumburg (Management Science 2014); Hameed & Mian (JFQA 2015).
"""
from __future__ import annotations

import math

import numpy as np

from ..config import ReversalRules
from ..simulator import Book, Order
from ..view import MarketView
from .base import BaseStrategy


class SectorReversal(BaseStrategy):
    name = 'reversal'

    def __init__(self, rules: ReversalRules, tickers: list[str], groups: dict[str, str]):
        self.rules = rules
        self.tickers = list(tickers)
        labels = [groups.get(t) for t in self.tickers]
        self.group_members = {
            g: np.array([i for i, label in enumerate(labels) if label == g], dtype=int)
            for g in sorted({label for label in labels if label})
        }
        self.ungrouped = [t for t, label in zip(self.tickers, labels) if not label]
        self._first: int | None = None
        self._rebalances = 0
        self._names = []
        self._gross = []

    def first_decision_index(self, data, start_idx: int) -> int:
        return max(start_idx, self.rules.warmup())

    def decide(self, t: int, view: MarketView, book: Book) -> list[Order]:
        if self._first is None:
            self._first = t
        if (t - self._first) % self.rules.rebalance:
            return []
        weights = self.target_weights(view)
        prices = view.close[t]
        equity = book.equity
        band = self.rules.no_trade_band * equity
        orders = []
        for i, ticker in enumerate(self.tickers):
            current = book.position(ticker)
            price = prices[i]
            target = weights[i] * equity / price if weights[i] != 0 and math.isfinite(price) and price > 0 else 0.0
            if target == 0.0 and current == 0.0:
                continue
            if target != 0.0 and abs(target - current) * price < band:
                continue
            orders.append(Order(ticker, target, kind='rebalance', reason='rebalance'))
        self._rebalances += 1
        self._names.append(int(np.count_nonzero(weights)))
        self._gross.append(float(np.abs(weights).sum()))
        return orders

    def target_weights(self, view: MarketView) -> np.ndarray:
        """Pesos objetivo (fracción del capital, con signo) con datos hasta ``view.t``."""
        r = self.rules
        close = view.close
        t = view.t
        n_assets = close.shape[1]
        if t < r.warmup() - 1:
            return np.zeros(n_assets)

        window = close[t - r.vol_window : t + 1]
        eligible = np.isfinite(window).all(axis=0)
        last = close[t]
        past = close[t - r.lookback]
        eligible &= np.isfinite(past) & (np.nan_to_num(last) >= r.min_price) & (np.nan_to_num(past) > 0)
        if r.min_dollar_volume > 0 and view.volume is not None:
            volume = view.volume[t - r.liquidity_window + 1 : t + 1]
            prices = close[t - r.liquidity_window + 1 : t + 1]
            with np.errstate(invalid='ignore'):
                dollar = np.nanmedian(volume * prices, axis=0)
            eligible &= np.nan_to_num(dollar) >= r.min_dollar_volume

        with np.errstate(divide='ignore', invalid='ignore'):
            log_window = np.log(np.where(eligible, window, np.nan))
            sigma = np.std(np.diff(log_window, axis=0), axis=0, ddof=1)
            move = np.log(np.where(eligible, last / past, np.nan))
        eligible &= np.isfinite(sigma) & (sigma > 0) & np.isfinite(move)

        gap = np.full(n_assets, np.nan)
        for members in self.group_members.values():
            members = members[eligible[members]]
            n = len(members)
            if n < r.min_group_size:
                continue
            moves = move[members]
            leave_one_out = (moves.sum() - moves) / (n - 1)
            gap[members] = (moves - leave_one_out) / (sigma[members] * math.sqrt(r.lookback))

        raw = np.zeros(n_assets)
        for members in self.group_members.values():
            members = members[np.isfinite(gap[members])]
            if len(members) < r.min_group_size:
                continue
            centered = gap[members] - gap[members].mean()
            if r.min_gap > 0:
                centered = np.where(np.abs(centered) >= r.min_gap, centered, 0.0)
            raw[members] = -centered
        return self._normalize(raw)

    def _normalize(self, raw: np.ndarray) -> np.ndarray:
        r = self.rules
        weights = raw.copy()
        for _ in range(8):
            total = np.abs(weights).sum()
            if total == 0:
                return weights
            weights = np.clip(weights * (r.gross / total), -r.max_weight, r.max_weight)
            weights = self._balance(weights)
            if abs(np.abs(weights).sum() - r.gross) < 1e-9:
                break
        return weights

    def _balance(self, weights: np.ndarray) -> np.ndarray:
        """Neutralidad en dólares por grupo: el lado mayor se reduce al menor."""
        weights = weights.copy()
        for members in self.group_members.values():
            group = weights[members]
            longs, shorts = group[group > 0].sum(), -group[group < 0].sum()
            if longs <= 0 or shorts <= 0:
                weights[members] = 0.0
                continue
            target = min(longs, shorts)
            weights[members] = np.where(group > 0, group * target / longs, group * target / shorts)
        return weights

    def warnings(self) -> list[str]:
        out = []
        if self.ungrouped:
            out.append(f'{len(self.ungrouped)} valores sin grupo no se operan: {", ".join(self.ungrouped[:10])}')
        return out

    def summary(self) -> dict:
        return {
            'rebalances': self._rebalances,
            'avg_names': float(np.mean(self._names)) if self._names else 0.0,
            'avg_target_gross': float(np.mean(self._gross)) if self._gross else 0.0,
            'groups': len(self.group_members),
        }
