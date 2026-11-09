"""Métricas de rendimiento a partir de la curva de equity diaria."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(equity) -> np.ndarray:
    equity = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(equity)
    return equity / peak - 1.0


def _longest_underwater(dd: np.ndarray) -> int:
    longest = current = 0
    for value in dd:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _period_returns(dates: pd.DatetimeIndex, equity: np.ndarray, freq: str) -> dict:
    series = pd.Series(equity, index=dates)
    key = series.index.year if freq == 'year' else [series.index.year, series.index.month]
    last = series.groupby(key).last()
    first_base = series.iloc[0]
    out = {}
    previous = first_base
    for label, value in last.items():
        out[label] = value / previous - 1.0
        previous = value
    if freq == 'year':
        return {int(year): float(ret) for year, ret in out.items()}
    monthly: dict[int, dict[int, float]] = {}
    for (year, month), ret in out.items():
        monthly.setdefault(int(year), {})[int(month)] = float(ret)
    return monthly


def _sharpe(returns: np.ndarray) -> float:
    if len(returns) < 2:
        return math.nan
    sd = returns.std(ddof=1)
    return float(returns.mean() / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else math.nan


def compute_metrics(
    dates,
    equity,
    *,
    gross_exposure=None,
    benchmark=None,
    traded_notional: float = 0.0,
    commission: float = 0.0,
    slippage: float = 0.0,
    borrow: float = 0.0,
    trades=None,
) -> dict:
    dates = pd.DatetimeIndex(dates)
    equity = np.asarray(equity, dtype=float)
    n = len(equity)
    returns = equity[1:] / equity[:-1] - 1.0 if n > 1 else np.array([])
    years = (n - 1) / TRADING_DAYS if n > 1 else 0.0
    start_value, end_value = float(equity[0]), float(equity[-1])

    sharpe = _sharpe(returns)
    if len(returns) > 1 and returns.std(ddof=1) > 0:
        daily_sr = returns.mean() / returns.std(ddof=1)
        # Error estándar del Sharpe (Lo 2002, rendimientos iid), anualizado.
        sharpe_se = math.sqrt((1 + 0.5 * daily_sr**2) / len(returns)) * math.sqrt(TRADING_DAYS)
    else:
        sharpe_se = math.nan
    downside = math.sqrt(float(np.mean(np.minimum(returns, 0.0) ** 2))) if len(returns) else 0.0
    sortino = float(returns.mean() / downside * math.sqrt(TRADING_DAYS)) if downside > 0 else math.nan
    dd = drawdown(equity)
    max_dd = float(dd.min()) if n else math.nan
    cagr = (end_value / start_value) ** (1 / years) - 1 if years > 0 and end_value > 0 else math.nan

    net_pnl = end_value - start_value
    costs = commission + slippage + borrow
    gross_pnl = net_pnl + costs
    metrics = {
        'start': dates[0].date().isoformat() if n else None,
        'end': dates[-1].date().isoformat() if n else None,
        'sessions': n,
        'years': years,
        'initial_equity': start_value,
        'final_equity': end_value,
        'total_return': end_value / start_value - 1.0,
        'cagr': cagr,
        'volatility': float(returns.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(returns) > 1 else math.nan,
        'sharpe': sharpe,
        'sharpe_se': sharpe_se,
        'sortino': sortino,
        'max_drawdown': max_dd,
        'max_drawdown_sessions': _longest_underwater(dd),
        'calmar': cagr / abs(max_dd) if max_dd < 0 and math.isfinite(cagr) else math.nan,
        'net_pnl': net_pnl,
        'gross_pnl': gross_pnl,
        'commission': commission,
        'slippage': slippage,
        'borrow': borrow,
        'costs': costs,
        'cost_share': costs / abs(gross_pnl) if gross_pnl else math.nan,
        'traded_notional': traded_notional,
        'turnover': traded_notional / float(equity.mean()) / years if years > 0 else math.nan,
        # Coste por lado (comisión + deslizamiento, en pb) que dejaría el resultado en cero.
        'break_even_bps': (net_pnl + commission + slippage) / traded_notional * 1e4 if traded_notional > 0 else math.nan,
        'positive_days': float((returns > 0).mean()) if len(returns) else math.nan,
        'yearly': _period_returns(dates, equity, 'year') if n else {},
        'monthly': _period_returns(dates, equity, 'month') if n else {},
    }

    if gross_exposure is not None and n:
        gross = np.asarray(gross_exposure, dtype=float)
        metrics['avg_gross_exposure'] = float(np.mean(gross / equity))
        metrics['time_in_market'] = float(np.mean(gross > 0))

    half = len(returns) // 2
    metrics['halves'] = {
        'first': {'sharpe': _sharpe(returns[:half]), 'start': metrics['start'], 'end': dates[half].date().isoformat() if n else None},
        'second': {'sharpe': _sharpe(returns[half:]), 'start': dates[half].date().isoformat() if n else None, 'end': metrics['end']},
    }

    if benchmark is not None and len(benchmark) == n:
        bench = pd.Series(benchmark, dtype=float).ffill().to_numpy()
        valid = np.isfinite(bench)
        if valid.sum() > 2:
            first, last = np.flatnonzero(valid)[0], np.flatnonzero(valid)[-1]
            b_ret = bench[1:] / bench[:-1] - 1.0
            mask = np.isfinite(b_ret)
            if mask.sum() > 2 and np.var(b_ret[mask]) > 0 and np.var(returns[mask]) > 0:
                cov = np.cov(returns[mask], b_ret[mask])
                metrics['beta'] = float(cov[0, 1] / cov[1, 1])
                metrics['correlation'] = float(np.corrcoef(returns[mask], b_ret[mask])[0, 1])
            metrics['benchmark_total_return'] = float(bench[last] / bench[first] - 1.0)

    if trades:
        nets = np.array([t.net_pnl for t in trades], dtype=float)
        wins, losses = nets[nets > 0].sum(), -nets[nets < 0].sum()
        metrics['trades'] = {
            'n': int(len(nets)),
            'win_rate': float((nets > 0).mean()),
            'avg_net': float(nets.mean()),
            'profit_factor': float(wins / losses) if losses > 0 else None,
            'avg_holding': float(np.mean([t.holding_sessions for t in trades])),
        }
    return metrics
