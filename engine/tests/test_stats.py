import math
import unittest

import numpy as np

from engine.data.synthetic import cointegrated_pair, factor_walks, random_walk
from engine.research.stats import (
    current_zscore,
    engle_granger_pvalue,
    fdr_bh,
    fit_hedge_ratio,
    half_life,
    pair_cointegration,
)


def ar1(n, phi, seed):
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    values = np.zeros(n)
    for t in range(1, n):
        values[t] = phi * values[t - 1] + noise[t]
    return values


class HedgeRatioTests(unittest.TestCase):
    def test_recovers_beta_and_alpha_on_a_cointegrated_pair(self):
        df = cointegrated_pair(2000, seed=1, beta=1.2, alpha=0.3, half_life=10)
        fit = fit_hedge_ratio(np.log(df['Y']), np.log(df['X']))
        self.assertAlmostEqual(fit.beta, 1.2, delta=0.02)
        self.assertAlmostEqual(fit.alpha, 0.3, delta=0.15)
        self.assertAlmostEqual(float(fit.spread.mean()), 0.0, places=9)

    def test_spread_keeps_the_index_of_y(self):
        df = cointegrated_pair(100, seed=2)
        fit = fit_hedge_ratio(np.log(df['Y']), np.log(df['X']))
        self.assertTrue(fit.spread.index.equals(df.index))

    def test_constant_x_is_rejected(self):
        with self.assertRaises(ValueError):
            fit_hedge_ratio(np.arange(10.0), np.ones(10))

    def test_mismatched_lengths_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_hedge_ratio(np.arange(10.0), np.arange(9.0))


class HalfLifeTests(unittest.TestCase):
    def test_recovers_the_half_life_of_an_ou_process(self):
        series = ar1(5000, phi=0.5 ** (1 / 10), seed=3)
        self.assertAlmostEqual(half_life(series), 10.0, delta=2.5)

    def test_random_walk_does_not_mean_revert(self):
        walk = np.cumsum(np.random.default_rng(4).standard_normal(3000))
        self.assertGreater(half_life(walk), 60)

    def test_explosive_series_returns_infinity(self):
        self.assertEqual(half_life(1.01 ** np.arange(200)), math.inf)

    def test_white_noise_reverts_within_one_period(self):
        noise = np.random.default_rng(5).standard_normal(2000)
        self.assertLess(half_life(noise), 1.0)

    def test_too_short_series_is_rejected(self):
        with self.assertRaises(ValueError):
            half_life(np.arange(5.0))


class CointegrationTests(unittest.TestCase):
    def test_detects_cointegrated_pairs_with_fast_reversion(self):
        hits = 0
        for seed in range(20):
            df = cointegrated_pair(500, seed=seed, half_life=5)
            hits += pair_cointegration(np.log(df['Y']), np.log(df['X'])).pvalue < 0.05
        self.assertGreaterEqual(hits, 19)

    def test_false_positive_rate_on_independent_random_walks(self):
        flagged = 0
        for seed in range(100):
            a = random_walk(500, seed=seed, name='A')
            b = random_walk(500, seed=10_000 + seed, name='B')
            flagged += pair_cointegration(np.log(a), np.log(b)).pvalue < 0.05
        # Nominal 5 %: con 100 pares esperamos ~5; 12 deja margen de sobra.
        self.assertLessEqual(flagged, 12)

    def test_high_correlation_alone_is_not_cointegration(self):
        flagged = 0
        for seed in range(50):
            df = factor_walks(500, seed=seed)
            returns_corr = np.corrcoef(np.diff(np.log(df['C'])), np.diff(np.log(df['D'])))[0, 1]
            self.assertGreater(returns_corr, 0.7)  # están realmente correlacionados
            flagged += pair_cointegration(np.log(df['C']), np.log(df['D'])).pvalue < 0.05
        self.assertLessEqual(flagged, 10)

    def test_swapping_the_pair_flips_the_orientation_not_the_pvalue(self):
        df = cointegrated_pair(500, seed=6, half_life=5)
        a, b = np.log(df['Y']), np.log(df['X'])
        forward = pair_cointegration(a, b)
        backward = pair_cointegration(b, a)
        self.assertAlmostEqual(forward.pvalue, backward.pvalue, places=12)
        self.assertNotEqual(forward.a_is_dependent, backward.a_is_dependent)

    def test_degenerate_series_count_as_not_cointegrated(self):
        self.assertEqual(engle_granger_pvalue(np.ones(100), np.arange(100.0)), 1.0)


class FdrTests(unittest.TestCase):
    def test_known_example(self):
        # Ordenados: 0.005, 0.01, 0.03, 0.04 -> p*n/rango = 0.02, 0.02, 0.04, 0.04
        np.testing.assert_allclose(fdr_bh([0.01, 0.04, 0.03, 0.005]), [0.02, 0.04, 0.04, 0.02])

    def test_adjusted_values_are_bounded_and_keep_the_order(self):
        p = np.random.default_rng(0).uniform(size=50)
        adjusted = fdr_bh(p)
        self.assertTrue(np.all(adjusted >= p - 1e-12))
        self.assertTrue(np.all(adjusted <= 1.0))
        self.assertTrue(np.all(np.diff(adjusted[np.argsort(p)]) >= -1e-12))

    def test_empty_input(self):
        self.assertEqual(len(fdr_bh([])), 0)


class ZScoreTests(unittest.TestCase):
    def test_matches_a_manual_computation(self):
        values = np.random.default_rng(5).standard_normal(200)
        values[-1] = 4.0
        expected = (values[-1] - values.mean()) / values.std(ddof=1)
        self.assertAlmostEqual(current_zscore(values), expected, places=12)

    def test_constant_spread_gives_nan(self):
        self.assertTrue(math.isnan(current_zscore(np.ones(50))))

    def test_holdout_excludes_the_last_observations_from_the_reference(self):
        values = np.random.default_rng(7).standard_normal(200)
        values[-1] = 6.0
        reference = values[:-1]
        expected = (6.0 - reference.mean()) / reference.std(ddof=1)
        self.assertAlmostEqual(current_zscore(values, holdout=1), expected, places=12)
        # Si el propio dato extremo entra en la referencia, el z sale más pequeño.
        self.assertLess(current_zscore(values, holdout=0), current_zscore(values, holdout=1))

    def test_negative_holdout_is_rejected(self):
        with self.assertRaises(ValueError):
            current_zscore(np.arange(10.0), holdout=-1)


if __name__ == '__main__':
    unittest.main()
