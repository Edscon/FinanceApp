"""Tests del simulador: contabilidad exacta, invariantes, momento de ejecución y bordes."""
import math
import unittest

import numpy as np
import pandas as pd

from engine.backtest.config import BacktestConfig, CostModel, Execution
from engine.backtest.data import MarketData
from engine.backtest.simulator import Order, run_backtest
from engine.backtest.strategies.base import BaseStrategy


class Scripted(BaseStrategy):
    """Estrategia de guion: en la sesión t devuelve las órdenes de ``script[t]``."""

    name = 'scripted'

    def __init__(self, script, start=0):
        self.script = script
        self.start = start
        self.fills = []
        self.views = {}

    def first_decision_index(self, data, start_idx):
        return max(start_idx, self.start)

    def decide(self, t, view, book):
        self.views[t] = view.close.shape[0]
        return [Order(*spec) for spec in self.script.get(t, [])]

    def on_fill(self, fill):
        self.fills.append(fill)


def market(closes, opens=None, start='2024-01-04'):  # 2024-01-04 es jueves
    index = pd.bdate_range(start, periods=len(closes['A']))
    close = pd.DataFrame(closes, index=index)
    open_ = None if opens is None else pd.DataFrame(opens, index=index)
    return MarketData(close, open_)


def config(costs=CostModel(0, 0, 0), execution=Execution.NEXT_CLOSE, **kw):
    return BacktestConfig.default('reversal', costs=costs, execution=execution, **kw)


CLOSES = {'A': [10.0, 10, 11, 12, 11, 13], 'B': [20.0, 20, 19, 21, 22, 20]}
SCRIPT = {
    0: [('A', 100, 'open', 'tr1'), ('B', -50, 'open', 'tr1')],
    3: [('A', 0, 'close', 'tr1', 'target'), ('B', 0, 'close', 'tr1', 'target')],
}


class KnownAnswerTests(unittest.TestCase):
    def test_without_costs(self):
        result = run_backtest(market(CLOSES), config(), Scripted(SCRIPT))
        np.testing.assert_allclose(result.equity, [100000, 100000, 100150, 100150, 100000, 100000])
        self.assertEqual(result.n_positions, [0, 2, 2, 2, 0, 0])

    def test_with_commission_slippage_and_weekend_borrow(self):
        # comisión 10 pb, deslizamiento 20 pb, préstamo 365 pb/año = 0,01 % por día natural.
        costs = CostModel(commission_bps=10, slippage_bps=20, borrow_bps_annual=365)
        result = run_backtest(market(CLOSES), config(costs=costs), Scripted(SCRIPT))
        # t1 (vie): compra 100 A a 10,02 + 1 de comisión; venta 50 B a 19,96 - 1 -> caja 99.994.
        # t2 (lun): préstamo de 3 días naturales: 50 x 20 x 0,0001 x 3 = 0,3.
        # t3: 50 x 19 x 0,0001 = 0,095. t4: 50 x 21 x 0,0001 = 0,105 y cierre:
        # venta 100 A a 10,978 - 1,1; compra 50 B a 22,044 - 1,1.
        expected = [100000, 99994, 100143.7, 100143.605, 99986.9, 99986.9]
        np.testing.assert_allclose(result.equity, expected, rtol=0, atol=1e-6)
        m = result.metrics
        self.assertAlmostEqual(m['commission'], 4.2, places=9)  # 1 + 1 + 1,1 + 1,1
        self.assertAlmostEqual(m['slippage'], 8.4, places=9)
        self.assertAlmostEqual(m['borrow'], 0.5, places=9)
        self.assertAlmostEqual(m['gross_pnl'], 0.0, places=9)  # 100 x (11-10) - 50 x (22-20)

    def test_attribution_adds_up_exactly(self):
        costs = CostModel(commission_bps=10, slippage_bps=20, borrow_bps_annual=365)
        result = run_backtest(market(CLOSES), config(costs=costs), Scripted(SCRIPT))
        by_ticker = result.attribution['by_ticker']
        self.assertAlmostEqual(sum(v['net'] for v in by_ticker.values()), result.equity[-1] - 100000, places=9)
        for item in by_ticker.values():
            self.assertAlmostEqual(item['gross'], item['net'] + item['costs'], places=9)
            self.assertGreaterEqual(item['costs'], 0)


class TimingTests(unittest.TestCase):
    def test_orders_fill_at_the_next_close_never_at_the_signal_close(self):
        strategy = Scripted(SCRIPT)
        run_backtest(market(CLOSES), config(), strategy)
        opening = [f for f in strategy.fills if f.kind == 'open']
        self.assertTrue(all(f.t == 1 for f in opening))
        self.assertEqual({f.ticker: f.ref_price for f in opening}, {'A': 10.0, 'B': 20.0})
        closing = [f for f in strategy.fills if f.kind == 'close']
        self.assertTrue(all(f.t == 4 for f in closing))
        self.assertEqual({f.ticker: f.ref_price for f in closing}, {'A': 11.0, 'B': 22.0})

    def test_next_open_uses_the_open_and_falls_back_to_the_close(self):
        opens = {'A': [10.0, 9.5, 11, 12, math.nan, 13], 'B': [20.0, 20.5, 19, 21, 21.5, 20]}
        strategy = Scripted(SCRIPT)
        result = run_backtest(market(CLOSES, opens), config(execution=Execution.NEXT_OPEN), strategy)
        prices = {(f.t, f.ticker): f.ref_price for f in strategy.fills}
        self.assertEqual(prices[(1, 'A')], 9.5)
        self.assertEqual(prices[(1, 'B')], 20.5)
        self.assertEqual(prices[(4, 'A')], 11.0)  # sin apertura: cierre
        self.assertEqual(prices[(4, 'B')], 21.5)
        self.assertTrue(any('apertura' in w for w in result.warnings))

    def test_the_strategy_only_sees_data_up_to_t(self):
        strategy = Scripted({})
        run_backtest(market(CLOSES), config(), strategy)
        self.assertEqual(strategy.views, {t: t + 1 for t in range(5)})

    def test_a_jump_on_the_fill_day_shows_up_in_the_fill(self):
        closes = {'A': [10.0, 15, 15, 15], 'B': [20.0, 20, 20, 20]}
        strategy = Scripted({0: [('A', 10, 'open', 'x')]})
        run_backtest(market(closes), config(), strategy)
        self.assertEqual(strategy.fills[0].ref_price, 15.0)


class EdgeTests(unittest.TestCase):
    def test_missing_price_cancels_entries(self):
        closes = {'A': [10.0, math.nan, 11, 12, 12, 12], 'B': [20.0] * 6}
        strategy = Scripted({0: [('A', 10, 'open', 'x')]})
        result = run_backtest(market(closes), config(), strategy)
        self.assertEqual(strategy.fills, [])
        self.assertTrue(any('cancelaron' in w for w in result.warnings))

    def test_missing_price_postpones_exits(self):
        closes = {'A': [10.0, 10, 10, math.nan, 12, 12], 'B': [20.0] * 6}
        strategy = Scripted({0: [('A', 10, 'open', 'x')], 2: [('A', 0, 'close', 'x', 'target')]})
        run_backtest(market(closes), config(), strategy)
        exit_fill = [f for f in strategy.fills if f.kind == 'close'][0]
        self.assertEqual((exit_fill.t, exit_fill.ref_price), (4, 12.0))

    def test_long_data_gap_forces_a_close_at_the_last_price(self):
        closes = {'A': [10.0, 10, 11, math.nan, math.nan, math.nan, math.nan, math.nan, 9], 'B': [20.0] * 9}
        strategy = Scripted({0: [('A', 10, 'open', 'x')]})
        result = run_backtest(market(closes), config(), strategy)
        forced = [f for f in strategy.fills if f.reason == 'data_gap']
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0].ref_price, 11.0)
        self.assertEqual(forced[0].t, 6)  # 3 sesiones arrastrando el precio y cierre en la cuarta
        self.assertTrue(all(math.isfinite(f.fill_price) for f in strategy.fills))
        self.assertTrue(any('hueco de datos' in w for w in result.warnings))

    def test_everything_is_liquidated_at_the_end(self):
        strategy = Scripted({0: [('A', 100, 'open', 'x'), ('B', -50, 'open', 'x')]})
        result = run_backtest(market(CLOSES), config(), strategy)
        last = [f for f in strategy.fills if f.reason == 'end_of_data']
        self.assertEqual({f.ticker for f in last}, {'A', 'B'})
        self.assertEqual(result.net_exposure[-1], 0)
        self.assertAlmostEqual(result.equity[-1], result.cash[-1])

    def test_start_and_end_dates_limit_the_simulation(self):
        data = market(CLOSES)
        result = run_backtest(data, config(start=data.dates[1].date(), end=data.dates[4].date()), Scripted({}))
        self.assertEqual(result.dates[0], data.dates[1].date().isoformat())
        self.assertEqual(result.dates[-1], data.dates[4].date().isoformat())

    def test_insufficient_history_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'Datos insuficientes'):
            run_backtest(market(CLOSES), config(), Scripted({}, start=10))


class InvariantTests(unittest.TestCase):
    def test_random_trading_keeps_the_books_balanced(self):
        for seed in range(20):
            rng = np.random.default_rng(seed)
            n = 60
            closes = {t: 50 * np.exp(np.cumsum(0.02 * rng.standard_normal(n))) for t in 'ABC'}
            closes['A'][rng.integers(5, n, 3)] = np.nan
            script = {}
            for t in range(n - 1):
                if rng.random() < 0.3:
                    ticker = 'ABC'[rng.integers(0, 3)]
                    script[t] = [(ticker, float(rng.normal(0, 50)), 'rebalance', 'r')]
            costs = CostModel(commission_bps=rng.uniform(0, 5), slippage_bps=rng.uniform(0, 10),
                              borrow_bps_annual=rng.uniform(0, 300))
            result = run_backtest(market(closes), config(costs=costs), Scripted(script))
            with self.subTest(seed=seed):
                net = sum(v['net'] for v in result.attribution['by_ticker'].values())
                self.assertAlmostEqual(net, result.equity[-1] - 100000, places=6)
                np.testing.assert_allclose(np.array(result.cash) + np.array(result.net_exposure), result.equity)
                m = result.metrics
                self.assertAlmostEqual(m['gross_pnl'], m['net_pnl'] + m['commission'] + m['slippage'] + m['borrow'], places=6)
                self.assertTrue(min(m['commission'], m['slippage'], m['borrow']) >= 0)
                self.assertEqual(result.net_exposure[-1], 0)


if __name__ == '__main__':
    unittest.main()
