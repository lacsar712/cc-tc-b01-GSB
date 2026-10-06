"""速率拟合：以办结时刻为横轴、收敛毫米值为纵轴做最小二乘直线拟合，斜率即速率（mm/周）。

口径：
- 样本只含已办结（done）点，由调用方保证；没办结的点不许进样本。
- 横轴取办结时刻 processed_at，换算成相对首点的周数，避免大时间戳损失精度。
- 样本不足 2 个、或所有样本时刻相同（斜率无定义）时返回 None，页面显示"空"。
"""
from datetime import datetime

SECONDS_PER_WEEK = 7 * 24 * 3600.0


def fit_rate(points: list[tuple[datetime, float]]) -> float | None:
    """points 为 (办结时刻, 收敛mm)，返回 mm/周斜率；无法拟合返回 None。"""
    pts = sorted(((t, float(v)) for t, v in points if t is not None), key=lambda p: p[0])
    if len(pts) < 2:
        return None
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() / SECONDS_PER_WEEK for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def trend_of(rate: float | None) -> str:
    if rate is None:
        return "none"
    if rate > 0:
        return "up"
    if rate < 0:
        return "down"
    return "flat"
