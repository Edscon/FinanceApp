"""Tests de las estrategias: sin look-ahead, comportamiento en datos conocidos, costes y determinismo."""
import unittest

import numpy as np
import pandas as pd

from engine.backtest.config import BacktestConfig, CostModel, PairsRules, ReversalRules
from engine.backtest.data import MarketData
from engine.backtest.simulator import run_backtest
from engine.backtest.strategies import build_strategy, prepare_pairs
from engine.backtest.strategies.pairs import selection_key, selections_from_json, selections_to_json
from engine.backtest.view import MarketView
from engine.data.synthetic import synthetic_market

PAIRS_FAST = PairsRules(formation=252, trading=63)


def demo_data(n=1100, seed=5, **kw):
    m = synthetic_market(n, seed=seed, **kw)
    return MarketData(m['close'], m['open'], m['volume'], m['groups']), m['truth']


def perturb_after(data: MarketData, t_star: int, seed=0) -> MarketData:
    rng = np.random.default_rng(seed)

    def bend(frame):
        if frame is None:
            return None
        frame = frame.copy()
        noise = np.exp(rng.normal(0, 0.05, size=frame.iloc[t_star + 1 :].shape))
        frame.iloc[t_star + 1 :] = frame.iloc[t_star + 1 :].to_numpy() * noise
        return frame

    return MarketData(bend(data.close), bend(data.open), bend(data.volume), data.groups)


class NoLookAheadTests(unittest.TestCase):
    """Cambiar los datos posteriores a t* no puede cambiar nada de lo ocurrido hasta t*."""

    def check(self, config):
        data, _ = demo_data()
        base = run_backtest(data, config)
        t_star = 800
        changed = run_backtest(perturb_after(data, t_star), config)
        s0 = len(data.dates) - len(base.dates)
        upto = t_star - s0 + 1
        self.assertGreater(upto, 50)
        self.assertEqual(base.equity[:upto], changed.equity[:upto])
        self.assertNotEqual(base.equity[-1], changed.equity[-1])  # el futuro sí cambió algo
        return base, changed, t_star

    def test_reversal(self):
        self.check(BacktestConfig.default('reversal'))

    def test_reversal_next_open(self):
        self.check(BacktestConfig.default('reversal', execution='next_open'))

    def test_pairs_including_selection(self):
        config = BacktestConfig(strategy='pairs', rules=PAIRS_FAST)
        data, _ = demo_data()
        windows, selections = prepare_pairs(data, config)
        _, changed_selections = prepare_pairs(perturb_after(data, 800), config)
        for w, a, b in zip(windows, selections, changed_selections):
            if w.f_end <= 800:
                self.assertEqual(a, b, f'ventana {w.index}')
        base, _, t_star = self.check(config)
        self.assertTrue(base.trades)


class ReversalTests(unittest.TestCase):
    def weights(self, data, t, rules=ReversalRules()):
        strategy = build_strategy(BacktestConfig(strategy='reversal', rules=rules), data)
        close, open_, volume = data.arrays()
        return strategy, strategy.target_weights(MarketView(close, open_, volume, data.dates, data.tickers, data.groups, t))

    def test_weights_are_dollar_neutral_per_group_and_capped(self):
        data, _ = demo_data()
        for t in (100, 400, 900):
            strategy, w = self.weights(data, t)
            with self.subTest(t=t):
                for members in strategy.group_members.values():
                    self.assertAlmostEqual(float(w[members].sum()), 0.0, places=12)
                self.assertLessEqual(float(np.abs(w).max()), 0.05 + 1e-12)
                self.assertLessEqual(float(np.abs(w).sum()), 1.0 + 1e-9)
                self.assertGreater(float(np.abs(w).sum()), 0.5)

    def test_laggards_are_bought_and_leaders_sold(self):
        data, _ = demo_data()
        strategy, w = self.weights(data, 500)
        close = data.close.to_numpy()
        move = np.log(close[500] / close[495])
        for members in strategy.group_members.values():
            active = members[w[members] != 0]
            if len(active) >= 3:
                corr = np.corrcoef(move[active], w[active])[0, 1]
                self.assertLess(corr, 0)

    def test_planted_reversal_pays_gross_and_noise_does_not(self):
        data, truth = demo_data(n=1500)
        result = run_backtest(data, BacktestConfig.default('reversal', costs=CostModel(0, 0, 0)))
        groups = result.attribution['by_group']
        planted = sum(groups[g]['gross'] for g in truth['reversal_groups'])
        self.assertGreater(planted, 0)
        self.assertGreater(planted, 3 * abs(groups[truth['noise_group']]['gross']))

    def test_no_edge_means_losses_after_costs(self):
        nets = []
        for seed in range(5):
            rng = np.random.default_rng(seed)
            index = pd.bdate_range('2020-01-01', periods=700)
            close = pd.DataFrame(50 * np.exp(np.cumsum(0.015 * rng.standard_normal((700, 12)), axis=0)),
                                 index=index, columns=[f'N{i}' for i in range(12)])
            groups = {c: ('G1' if i < 6 else 'G2') for i, c in enumerate(close.columns)}
            result = run_backtest(MarketData(close, groups=groups), BacktestConfig.default('reversal'))
            nets.append(result.metrics['net_pnl'])
        self.assertLess(np.mean(nets), 0)


class PairsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.truth = demo_data(n=1500, seed=11)
        cls.config = BacktestConfig(strategy='pairs', rules=PAIRS_FAST)
        cls.windows, cls.selections = prepare_pairs(cls.data, cls.config)

    def share(self, pair, windows=None):
        chosen = [frozenset((p.y, p.x)) for s in self.selections for p in s]
        indexes = range(len(self.selections)) if windows is None else windows
        hits = sum(1 for k in indexes if frozenset(pair) in {frozenset((p.y, p.x)) for p in self.selections[k]})
        return hits / len(indexes)

    def test_planted_pairs_are_selected_in_most_windows(self):
        for pair in self.truth['pairs']:
            self.assertGreaterEqual(self.share(pair), 0.6, pair)

    def test_trap_pair_is_rarely_selected(self):
        self.assertLessEqual(self.share(self.truth['trap']), 0.2)

    def test_broken_pair_stops_being_selected_after_the_break(self):
        break_at = self.data.dates.searchsorted(pd.Timestamp(self.truth['break_at']))
        before = [w.index for w in self.windows if w.f_end < break_at]
        after = [w.index for w in self.windows if w.f_start > break_at + 60]
        self.assertTrue(before and after)
        self.assertGreater(self.share(self.truth['broken'], before), self.share(self.truth['broken'], after))

    def test_trading_rules_and_records(self):
        result = run_backtest(self.data, self.config, selections=self.selections)
        self.assertTrue(result.trades)
        reasons = {t.exit_reason for t in result.trades}
        self.assertTrue(reasons <= {'target', 'stop', 'time', 'window_end', 'data_gap', 'end_of_data'})
        for trade in result.trades:
            self.assertLess(trade.signal_date, trade.entry_date)  # siempre en t+1
            self.assertLessEqual(trade.entry_date, trade.exit_date)
            self.assertAlmostEqual(trade.gross_pnl, trade.net_pnl + trade.commission + trade.slippage + trade.borrow, places=6)
            if trade.exit_reason == 'target':  # revirtió hasta la banda de salida (puede cruzar el 0)
                if trade.direction == 1:
                    self.assertLessEqual(trade.entry_z, -2.0)
                    self.assertGreaterEqual(trade.exit_z, -0.5 - 1e-9)
                else:
                    self.assertGreaterEqual(trade.entry_z, 2.0)
                    self.assertLessEqual(trade.exit_z, 0.5 + 1e-9)
            if trade.exit_reason == 'stop':
                self.assertGreaterEqual(abs(trade.exit_z), 4.0 - 1e-9)
        self.assertAlmostEqual(sum(t.net_pnl for t in result.trades), result.metrics['net_pnl'], places=4)

    def test_double_costs_same_decisions_lower_result(self):
        base = run_backtest(self.data, self.config, selections=self.selections)
        costly = run_backtest(self.data, self.config.replace(costs=self.config.costs.scaled(2)), selections=self.selections)
        signature = lambda r: [(t.y, t.x, t.signal_date, t.exit_signal_date, t.exit_reason) for t in r.trades]
        self.assertEqual(signature(base), signature(costly))
        self.assertLess(costly.metrics['net_pnl'], base.metrics['net_pnl'])

    def test_selection_serialization_and_cache_key(self):
        restored = selections_from_json(selections_to_json(self.selections))
        self.assertEqual(restored, self.selections)
        key = selection_key(self.data.fingerprint(), self.config.rules, self.windows)
        self.assertEqual(key, selection_key(self.data.fingerprint(), self.config.rules, self.windows))
        other = selection_key(self.data.fingerprint(), PairsRules(formation=252, trading=63, min_corr=0.8), self.windows)
        self.assertNotEqual(key, other)
        same_selection = selection_key(self.data.fingerprint(), PairsRules(formation=252, trading=63, entry_z=2.5), self.windows)
        self.assertEqual(key, same_selection)  # las reglas de operativa no afectan a la selección

    def test_random_walks_produce_no_edge(self):
        rng = np.random.default_rng(3)
        index = pd.bdate_range('2018-01-01', periods=900)
        close = pd.DataFrame(50 * np.exp(np.cumsum(0.015 * rng.standard_normal((900, 10)), axis=0)),
                             index=index, columns=[f'R{i}' for i in range(10)])
        result = run_backtest(MarketData(close, groups={c: 'G' for c in close.columns}), self.config)
        self.assertLessEqual(result.metrics['net_pnl'], 1e-9)


class DeterminismTests(unittest.TestCase):
    def test_column_order_does_not_matter(self):
        data, _ = demo_data(n=700)
        shuffled = data.close[list(reversed(data.tickers))]
        other = MarketData(shuffled, data.open[list(reversed(data.tickers))], data.volume, data.groups)
        for strategy in ('reversal', 'pairs'):
            config = BacktestConfig.default(strategy) if strategy == 'reversal' else BacktestConfig(strategy='pairs', rules=PairsRules(formation=252, trading=63))
            with self.subTest(strategy=strategy):
                self.assertEqual(run_backtest(data, config).equity, run_backtest(other, config).equity)

    def test_config_round_trip(self):
        for config in (BacktestConfig.default('reversal', execution='next_open'),
                       BacktestConfig(strategy='pairs', rules=PAIRS_FAST, costs=CostModel(2, 3, 4))):
            self.assertEqual(BacktestConfig.from_dict(config.to_dict()), config)


if __name__ == '__main__':
    unittest.main()
