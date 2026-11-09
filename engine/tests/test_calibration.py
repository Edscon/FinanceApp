import contextlib
import io
import unittest

from engine.calibration import (
    detection_rate,
    false_positive_rate_common_factor,
    false_positive_rate_independent,
    main,
)


class CalibrationTests(unittest.TestCase):
    def test_fast_reversion_is_almost_always_detected(self):
        self.assertGreaterEqual(detection_rate(500, half_life=5, seeds=10, level=0.05), 90.0)

    def test_slow_reversion_is_detected_less_often_than_fast_reversion(self):
        fast = detection_rate(500, half_life=5, seeds=20, level=0.05)
        slow = detection_rate(500, half_life=30, seeds=20, level=0.05)
        self.assertLess(slow, fast)

    def test_false_positive_rates_stay_near_the_nominal_level(self):
        # Con 40 pares el ruido de muestreo es grande: solo se descartan desvíos enormes.
        self.assertLessEqual(false_positive_rate_independent(500, seeds=40, level=0.05), 20.0)
        self.assertLessEqual(false_positive_rate_common_factor(500, seeds=40, level=0.05), 20.0)

    def test_command_line_prints_both_tables(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(['--n', '300', '--seeds', '3', '--half-lives', '5', '20'])
        text = out.getvalue()
        self.assertIn('Potencia', text)
        self.assertIn('half-life    5 d', text)
        self.assertIn('Tamaño', text)
        self.assertIn('correlados por factor común', text)


if __name__ == '__main__':
    unittest.main()
