"""fit_rate 口径单测：python3 -m unittest test_rates -v"""
import unittest
from datetime import datetime, timedelta, timezone

from rates import fit_rate, trend_of

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
WEEK = timedelta(days=7)


class FitRateTest(unittest.TestCase):
    def test_three_rising_closures_turn_positive(self):
        # 连着抬高三笔办结：速率必须为正
        pts = [(T0, 0.5), (T0 + WEEK, 1.0), (T0 + 2 * WEEK, 1.5)]
        rate = fit_rate(pts)
        self.assertIsNotNone(rate)
        self.assertGreater(rate, 0)
        self.assertAlmostEqual(rate, 0.5, places=6)  # 每周抬高 0.5 mm

    def test_falling_turns_negative(self):
        pts = [(T0, 2.0), (T0 + WEEK, 1.0), (T0 + 2 * WEEK, 0.0)]
        self.assertLess(fit_rate(pts), 0)

    def test_window_shrunk_off_points_is_empty(self):
        # 窗口缩到窗外：一个点都落不进来，速率为空
        self.assertIsNone(fit_rate([]))
        self.assertIsNone(fit_rate([(T0, 1.2)]))

    def test_same_instant_is_empty(self):
        # 时刻全相同：斜率无定义，为空而不是报错
        pts = [(T0, 1.0), (T0, 2.0)]
        self.assertIsNone(fit_rate(pts))

    def test_flat_series_is_zero(self):
        pts = [(T0, 1.2), (T0 + WEEK, 1.2), (T0 + 2 * WEEK, 1.2)]
        self.assertAlmostEqual(fit_rate(pts), 0.0, places=9)

    def test_unsorted_input_ok(self):
        pts = [(T0 + 2 * WEEK, 1.5), (T0, 0.5), (T0 + WEEK, 1.0)]
        self.assertAlmostEqual(fit_rate(pts), 0.5, places=6)

    def test_trend_labels(self):
        self.assertEqual(trend_of(None), "none")
        self.assertEqual(trend_of(0.3), "up")
        self.assertEqual(trend_of(-0.3), "down")
        self.assertEqual(trend_of(0.0), "flat")


if __name__ == "__main__":
    unittest.main()
