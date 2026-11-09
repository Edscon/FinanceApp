import datetime as dt
import json
import math
import unittest

import numpy as np
import pandas as pd

from engine.backtest.config import BacktestConfig, Execution
from engine.backtest.metrics import compute_metrics, drawdown
from engine.backtest.result import jsonsafe
from engine.backtest.robustness import gate, variants


class MetricsTests(unittest.TestCase):
    def test_known_values(self):
        dates = pd.bdate_range('2024-01-01', periods=4)
        m = compute_metrics(dates, [100.0, 110.0, 99.0, 120.0], traded_notional=1000.0, commission=1.0, slippage=2.0, borrow=0.5)
        self.assertAlmostEqual(m['total_return'], 0.2)
        self.assertAlmostEqual(m['max_drawdown'], 99 / 110 - 1)
        self.assertEqual(m['max_drawdown_sessions'], 1)
        returns = np.array([0.1, 99 / 110 - 1, 120 / 99 - 1])
        self.assertAlmostEqual(m['sharpe'], returns.mean() / returns.std(ddof=1) * math.sqrt(252))
        self.assertAlmostEqual(m['gross_pnl'], 20 + 3.5)
        self.assertAlmostEqual(m['break_even_bps'], (20 + 1 + 2) / 1000 * 1e4)
        self.assertEqual(list(m['yearly']), [2024])
        self.assertAlmostEqual(m['yearly'][2024], 0.2)

    def test_drawdown(self):
        np.testing.assert_allclose(drawdown([1, 2, 1, 3]), [0, 0, -0.5, 0])

    def test_monthly_and_yearly_returns_chain(self):
        dates = pd.to_datetime(['2023-12-28', '2023-12-29', '2024-01-02', '2024-02-01'])
        m = compute_metrics(dates, [100, 110, 121, 133.1])
        self.assertAlmostEqual(m['yearly'][2023], 0.1)
        self.assertAlmostEqual(m['yearly'][2024], 0.21)
        self.assertAlmostEqual(m['monthly'][2024][1], 0.1)
        self.assertAlmostEqual(m['monthly'][2024][2], 0.1)

    def test_flat_equity_gives_no_beta_and_no_warnings(self):
        import warnings

        dates = pd.bdate_range('2024-01-01', periods=50)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            m = compute_metrics(dates, [100.0] * 50, benchmark=np.linspace(100, 120, 50))
        self.assertNotIn('beta', m)
        self.assertTrue(math.isnan(m['sharpe']))

    def test_benchmark_beta(self):
        dates = pd.bdate_range('2024-01-01', periods=300)
        rng = np.random.default_rng(0)
        bench = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
        strat = 100 * np.cumprod(1 + 0.5 * np.r_[0, np.diff(bench) / bench[:-1]])
        m = compute_metrics(dates, strat, benchmark=bench)
        self.assertAlmostEqual(m['beta'], 0.5, places=6)
        self.assertAlmostEqual(m['correlation'], 1.0, places=6)


class JsonSafeTests(unittest.TestCase):
    def test_produces_strict_json(self):
        value = {'a': math.nan, 'b': math.inf, 'c': np.float64(1.5), 'd': [np.int64(2), (3,)], 'e': dt.date(2024, 1, 2),
                 'f': Execution.NEXT_OPEN, 'g': np.array([1.0, np.nan]), 3: None}
        text = json.dumps(jsonsafe(value), allow_nan=False)
        self.assertEqual(json.loads(text), {'a': None, 'b': None, 'c': 1.5, 'd': [2, [3]], 'e': '2024-01-02',
                                            'f': 'next_open', 'g': [1.0, None], '3': None})


class RobustnessTests(unittest.TestCase):
    def test_variants(self):
        keys = [k for k, _, _ in variants(BacktestConfig.default('reversal'))]
        self.assertEqual(keys, ['costs_x1_5', 'costs_x2', 'costs_x0', 'fill', 'param_low', 'param_high'])
        labels = {k: c for k, _, c in variants(BacktestConfig.default('reversal'))}
        self.assertEqual((labels['param_low'].rules.lookback, labels['param_high'].rules.lookback), (4, 6))
        self.assertEqual(labels['fill'].execution, Execution.NEXT_OPEN)
        pairs = {k: c for k, _, c in variants(BacktestConfig.default('pairs'))}
        self.assertEqual((pairs['param_low'].rules.entry_z, pairs['param_high'].rules.entry_z), (1.6, 2.4))

    def test_gate(self):
        self.assertTrue(gate({'costs_x1_5': {'sharpe': 1.2, 'total_return': 0.1}})['passed'])
        self.assertFalse(gate({'costs_x1_5': {'sharpe': 0.8, 'total_return': 0.1}})['passed'])
        self.assertFalse(gate({'costs_x1_5': {'sharpe': None}})['passed'])


if __name__ == '__main__':
    unittest.main()
