"""周速率拟合纯逻辑单测（只用标准库，系统 python3 直接运行）：

    python3 backend/test_rates.py
"""
import unittest
from datetime import datetime, timedelta, timezone

from rates import classify, fit_weekly_line, fit_weekly_rate, in_window

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


class WeeklyRateFit(unittest.TestCase):
    def test_three_weekly_rises_slope_positive(self):
        # 连续三周 0.6 -> 1.2 -> 1.8，最小二乘斜率应为 +0.6 mm/周（正）。
        pts = [(NOW - timedelta(weeks=2), 0.6),
               (NOW - timedelta(weeks=1), 1.2),
               (NOW, 1.8)]
        self.assertAlmostEqual(fit_weekly_rate(pts), 0.6, places=9)
        self.assertEqual(classify(fit_weekly_rate(pts)), "正")

    def test_line_endpoints(self):
        pts = [(NOW - timedelta(weeks=1), 1.0), (NOW, 2.0)]
        fit = fit_weekly_line(pts)
        # y = 1.0 + 1.0 * x（周）
        self.assertAlmostEqual(fit["slope"], 1.0, places=9)
        self.assertAlmostEqual(fit["intercept"], 1.0, places=9)

    def test_less_than_two_points_is_empty(self):
        self.assertIsNone(fit_weekly_rate([(NOW, 1.0)]))
        self.assertIsNone(fit_weekly_rate([]))

    def test_identical_timestamps_is_empty(self):
        # 时刻全相同（分母为 0）不能拟合，记空而不是报错或编造。
        self.assertIsNone(fit_weekly_rate([(NOW, 1.0), (NOW, 2.0)]))

    def test_flat_and_negative(self):
        flat = [(NOW - timedelta(weeks=1), 2.0), (NOW, 2.0)]
        self.assertAlmostEqual(fit_weekly_rate(flat), 0.0, places=9)
        self.assertEqual(classify(fit_weekly_rate(flat)), "平")
        down = [(NOW - timedelta(weeks=1), 3.0), (NOW, 2.0)]
        self.assertEqual(classify(fit_weekly_rate(down)), "负")

    def test_classify_empty(self):
        self.assertEqual(classify(None), "空")

    def test_window_filters_points(self):
        t0, t1, t2 = NOW - timedelta(weeks=2), NOW - timedelta(weeks=1), NOW
        self.assertTrue(in_window(t1, t0, t2))
        self.assertFalse(in_window(t0 - timedelta(days=1), t0, t2))
        self.assertFalse(in_window(t2 + timedelta(days=1), t0, t2))
        # 单侧为空表示该侧不限。
        self.assertTrue(in_window(t2, None, t2))
        self.assertTrue(in_window(t0, t0, None))

    def test_naive_datetime_treated_as_utc(self):
        naive = NOW.replace(tzinfo=None)
        self.assertAlmostEqual(
            fit_weekly_rate([(naive - timedelta(weeks=1), 0.0), (naive, 7.0)]),
            7.0, places=9,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
