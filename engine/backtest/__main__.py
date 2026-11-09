"""Backtest desde la línea de comandos sobre el mercado sintético de demostración.

    python -m engine.backtest --demo
    python -m engine.backtest --demo --strategy pairs
"""
from __future__ import annotations

import argparse

from engine.data.synthetic import synthetic_market

from .config import BacktestConfig
from .data import MarketData
from .simulator import run_backtest


def _fmt(value, pct=False, digits=2):
    if value is None:
        return '—'
    return f'{100 * value:.{digits}f} %' if pct else f'{value:.{digits}f}'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m engine.backtest', description=__doc__)
    parser.add_argument('--demo', action='store_true', help='usar el mercado sintético (obligatorio por ahora)')
    parser.add_argument('--strategy', choices=['reversal', 'pairs', 'both'], default='both')
    parser.add_argument('--n', type=int, default=1500, help='sesiones del mercado sintético')
    parser.add_argument('--seed', type=int, default=11)
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error('Fuera de la web solo hay modo --demo (los datos reales se cargan en la app Django).')

    market = synthetic_market(args.n, seed=args.seed)
    data = MarketData(market['close'], market['open'], market['volume'], market['groups'])
    strategies = ['reversal', 'pairs'] if args.strategy == 'both' else [args.strategy]
    for name in strategies:
        result = run_backtest(data, BacktestConfig.default(name))
        m = result.metrics
        print(f'\n== {name} ({m["start"]} a {m["end"]}, {m["sessions"]} sesiones) ==')
        print(f'CAGR {_fmt(m["cagr"], pct=True)} · Sharpe {_fmt(m["sharpe"])} ± {_fmt(m["sharpe_se"])} · '
              f'máx. drawdown {_fmt(m["max_drawdown"], pct=True)}')
        print(f'Rotación anual {_fmt(m["turnover"], digits=1)}x · costes {m["costs"]:,.0f} $ · '
              f'break-even {_fmt(m["break_even_bps"], digits=1)} pb por lado')
        if m.get('trades'):
            t = m['trades']
            print(f'Operaciones {t["n"]} · acierto {_fmt(t["win_rate"], pct=True)} · días medios {_fmt(t["avg_holding"], digits=1)}')
        for warning in result.warnings[3:]:
            print(f'Aviso: {warning}')


if __name__ == '__main__':
    main()
