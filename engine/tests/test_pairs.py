import math
import unittest

import numpy as np

from engine.data.synthetic import synthetic_universe
from engine.research.pairs import PairResult, ScanConfig, find_pairs, trade_legs
from engine.research.report import format_report


def key(pair):
    return frozenset((pair.y, pair.x))


class FindPairsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prices, cls.groups, cls.truth = synthetic_universe(600, seed=4)
        cls.report = find_pairs(cls.prices, cls.groups)

    def test_finds_exactly_the_truly_cointegrated_pairs(self):
        # TC/TD tienen correlación ~0.8 pero no cointegran: no deben salir.
        self.assertEqual({key(p) for p in self.report.pairs}, self.truth)

    def test_only_same_group_pairs_above_the_correlation_threshold_are_tested(self):
        # TA/TB, TC/TD y EA/EB; el resto de combinaciones intra-grupo no correlaciona.
        self.assertEqual(self.report.n_tested, 3)

    def test_estimates_are_consistent_with_the_generating_process(self):
        pair = next(p for p in self.report.pairs if key(p) == frozenset(('TA', 'TB')))
        # log TA = 0.3 + 1.2 log TB + s; si la regresión sale al revés, beta ~ 1/1.2.
        expected_beta = 1.2 if pair.y == 'TA' else 1 / 1.2
        self.assertAlmostEqual(pair.beta, expected_beta, delta=0.1)
        self.assertGreater(pair.corr, 0.8)
        self.assertTrue(3.0 <= pair.half_life <= 8.0, pair.half_life)  # real: 5
        self.assertLess(pair.p_adj, 0.01)
        self.assertEqual(pair.group, 'Tecnologia')
        self.assertTrue(pair.stable)

    def test_independent_stock_is_never_a_candidate(self):
        involved = {t for p in self.report.pairs for t in (p.y, p.x)}
        self.assertNotIn('EC', involved)

    def test_report_metadata(self):
        self.assertEqual(self.report.n_obs, 504)
        self.assertEqual(len(self.report.tickers_used), 7)
        self.assertEqual(self.report.tickers_dropped, {})
        self.assertEqual(self.report.as_of, self.prices.index[-1].date())

    def pair_after_a_jump(self, ticker, log_jump, **config):
        """Par TA/TB tras un salto de ``ticker`` en la última sesión."""
        prices = self.prices.copy()
        prices.loc[prices.index[-1], ticker] *= math.exp(log_jump)
        report = find_pairs(prices, self.groups, ScanConfig(**config))
        return next(p for p in report.pairs if key(p) == frozenset(('TA', 'TB')))

    def test_a_gap_up_in_one_leg_means_short_it_and_long_the_other(self):
        # +20 % en TA son ~10 sigmas del spread: no depende del z que ya hubiera antes.
        pair = self.pair_after_a_jump('TA', 0.20)
        self.assertGreater(abs(pair.z), 3.0)
        self.assertEqual(trade_legs(pair), ('TA', 'TB'))  # corto TA (caro), largo TB

    def test_a_gap_down_in_one_leg_means_long_it_and_short_the_other(self):
        pair = self.pair_after_a_jump('TA', -0.20)
        self.assertEqual(trade_legs(pair), ('TB', 'TA'))  # corto TB, largo TA (barato)

    def test_a_gap_in_the_other_leg_gives_the_mirror_trade(self):
        self.assertEqual(trade_legs(self.pair_after_a_jump('TB', 0.20)), ('TB', 'TA'))

    def test_the_last_session_does_not_contaminate_the_estimates(self):
        base = next(p for p in self.report.pairs if key(p) == frozenset(('TA', 'TB')))
        jumped = self.pair_after_a_jump('TA', 0.20)
        for field in ('y', 'x', 'alpha', 'beta', 'corr', 'pvalue', 'p_adj', 'half_life', 'stable'):
            self.assertEqual(getattr(jumped, field), getattr(base, field), field)
        self.assertNotEqual(jumped.z, base.z)

    def test_a_huge_gap_does_not_make_the_pair_disappear(self):
        # Sin holdout, un salto enorme en la última sesión puede empeorar el test que
        # califica al par justo cuando el gap es más grande.
        for seed in (1, 2, 3, 4):
            prices, groups, _ = synthetic_universe(600, seed=seed)
            for jump in (0.30, -0.30):
                with self.subTest(seed=seed, jump=jump):
                    jumped = prices.copy()
                    jumped.loc[jumped.index[-1], 'TA'] *= math.exp(jump)
                    found = {key(p): p for p in find_pairs(jumped, groups).pairs}
                    self.assertIn(frozenset(('TA', 'TB')), found)
                    self.assertGreater(abs(found[frozenset(('TA', 'TB'))].z), 10.0)

    def test_holdout_zero_estimates_with_the_whole_window(self):
        report = find_pairs(self.prices, self.groups, ScanConfig(holdout=0))
        self.assertEqual({key(p) for p in report.pairs}, self.truth)

    def test_ticker_with_a_long_gap_is_dropped_but_short_gaps_are_filled(self):
        prices = self.prices.copy()
        prices.iloc[100:110, prices.columns.get_loc('EC')] = np.nan  # 10 sesiones: no se rellena
        prices.iloc[300:302, prices.columns.get_loc('TD')] = np.nan  # 2 sesiones: se rellena
        report = find_pairs(prices, self.groups)
        self.assertIn('EC', report.tickers_dropped)
        self.assertNotIn('TD', report.tickers_dropped)
        self.assertEqual(len(report.tickers_used), 6)

    def test_non_positive_prices_are_dropped(self):
        prices = self.prices.copy()
        prices.iloc[-5, prices.columns.get_loc('EC')] = 0.0
        self.assertEqual(find_pairs(prices, self.groups).tickers_dropped, {'EC': 'precios no positivos'})

    def test_too_short_history_is_rejected(self):
        with self.assertRaises(ValueError):
            find_pairs(self.prices.iloc[-50:], self.groups)

    def test_empty_prices_are_rejected(self):
        with self.assertRaises(ValueError):
            find_pairs(self.prices.iloc[0:0], self.groups)

    def test_lookback_limits_the_window_to_the_latest_sessions(self):
        report = find_pairs(self.prices, self.groups, ScanConfig(lookback=300))
        self.assertEqual(report.n_obs, 300)

    def test_without_groups_every_pair_is_considered(self):
        report = find_pairs(self.prices, None, ScanConfig(min_corr=-1.0))
        self.assertEqual(report.n_tested, 21)  # C(7, 2)
        self.assertTrue(self.truth <= {key(p) for p in report.pairs})

    def test_group_filter_blocks_cross_group_pairs(self):
        groups = dict(self.groups, TB='Otro')  # TA y TB dejan de compartir grupo
        report = find_pairs(self.prices, groups)
        self.assertEqual(report.n_tested, 2)  # solo TC/TD y EA/EB
        self.assertNotIn(frozenset(('TA', 'TB')), {key(p) for p in report.pairs})

    def test_tickers_without_group_are_ignored_when_groups_are_given(self):
        groups = {t: g for t, g in self.groups.items() if t != 'TA'}
        report = find_pairs(self.prices, groups)
        self.assertNotIn(frozenset(('TA', 'TB')), {key(p) for p in report.pairs})

    def test_half_life_filter(self):
        report = find_pairs(self.prices, self.groups, ScanConfig(min_half_life=50, max_half_life=60))
        self.assertEqual(report.pairs, [])
        self.assertEqual(report.n_tested, 3)


class ScanConfigTests(unittest.TestCase):
    def test_invalid_values_are_rejected(self):
        for kwargs in (
            {'lookback': 50},  # menor que min_obs
            {'lookback': 104, 'holdout': 5},  # quedan 99 sesiones para estimar
            {'holdout': -1},
            {'holdout': 20},
            {'min_obs': 10},
            {'max_p_adj': 0.0},
            {'max_p_adj': 1.5},
            {'min_half_life': 10, 'max_half_life': 5},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ScanConfig(**kwargs)


def make_pair(z, y='AAA', x='BBB'):
    return PairResult(
        y=y, x=x, group='G', corr=0.9, alpha=0.0, beta=1.0,
        pvalue=0.001, p_adj=0.001, half_life=8.0, z=z, stable=True,
    )


class TradeLegsTests(unittest.TestCase):
    def test_positive_gap_shorts_y_and_longs_x(self):
        self.assertEqual(trade_legs(make_pair(2.5)), ('AAA', 'BBB'))

    def test_negative_gap_longs_y_and_shorts_x(self):
        self.assertEqual(trade_legs(make_pair(-2.5)), ('BBB', 'AAA'))

    def test_no_signal_below_the_threshold_or_without_a_valid_z(self):
        self.assertIsNone(trade_legs(make_pair(1.99)))
        self.assertIsNone(trade_legs(make_pair(math.nan)))

    def test_custom_threshold(self):
        self.assertEqual(trade_legs(make_pair(1.6), entry_z=1.5), ('AAA', 'BBB'))


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prices, groups, _ = synthetic_universe(600, seed=4)
        prices.loc[prices.index[-1], 'TA'] *= math.exp(0.20)
        cls.report = find_pairs(prices, groups)

    def test_table_lists_the_pairs_and_flags_the_gap(self):
        text = format_report(self.report)
        self.assertIn('PAR (y / x)', text)
        self.assertIn('EA', text)
        self.assertIn('CORTO TA / LARGO TB', text)
        self.assertIn('Aviso: resultados en muestra', text)

    def test_entry_threshold_controls_the_signal_column(self):
        self.assertNotIn('CORTO', format_report(self.report, entry_z=50))

    def test_top_limits_rows_and_mentions_the_rest(self):
        text = format_report(self.report, top=1)
        self.assertIn('... y 1 más', text)

    def test_every_sort_key_works_and_unknown_ones_fail(self):
        for sort in ('z', 'p', 'hl', 'corr'):
            format_report(self.report, sort=sort)
        with self.assertRaises(ValueError):
            format_report(self.report, sort='nope')

    def test_empty_result_message(self):
        prices, groups, _ = synthetic_universe(600, seed=4)
        empty = find_pairs(prices, groups, ScanConfig(min_half_life=50, max_half_life=60))
        self.assertIn('Ningún par supera el filtro', format_report(empty))


if __name__ == '__main__':
    unittest.main()
