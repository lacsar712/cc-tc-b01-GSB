"""周进尺速率：只用"已办结"读数做最小二乘拟合。

口径（务必与专页下块文案一致）：
- 样本只取 status == "done" 且 processed_at 非空的读数；pending / 失败的点一律不进样本。
- 自变量 x 取办结时刻 processed_at（不是登记时刻），折算成"周"（7 天）。
- 因变量 y 取收敛值 delta_mm；对同一断面做一元线性回归 y = a + b*x。
- b 即拟合速率，单位 mm/周：b>0 拱顶持续抬高（越挤越快方向），b≈0 走平，b<0 回落。
- 窗口内有效样本少于 2 个（成不了一条直线）时 rate 为 None（空），不编造数字。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

WEEK_SECONDS = 7 * 24 * 3600.0


def _aware(dt: datetime) -> datetime:
    """归一到带时区的 UTC，便于做差。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fit_weekly_line(points: Iterable[tuple[datetime, float]]) -> Optional[dict]:
    """一元 OLS，返回直线参数，供画拟合线：

    {"slope": mm/周, "intercept": t0 处的 y, "t0": 最早办结时刻(UTC), "n": 点数}
    少于 2 点或时刻全相同返回 None。
    """
    pts = [(t, float(y)) for t, y in points]
    n = len(pts)
    if n < 2:
        return None

    t0 = min(_aware(t) for t, _ in pts)
    xs = [(_aware(t) - t0).total_seconds() / WEEK_SECONDS for t, _ in pts]
    ys = [y for _, y in pts]

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0:
        return None
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    return {"slope": slope, "intercept": intercept, "t0": t0, "n": n}


def fit_weekly_rate(points: Iterable[tuple[datetime, float]]) -> Optional[float]:
    """对 (办结时刻, 收敛mm) 序列做一元 OLS，返回斜率 mm/周。

    少于 2 个点、或所有时刻相同（分母为 0）时返回 None。
    """
    fit = fit_weekly_line(points)
    return fit["slope"] if fit else None


def in_window(t: datetime, start_at: Optional[datetime], end_at: Optional[datetime]) -> bool:
    """闭区间 [start_at, end_at]；端点为空表示该侧不限。"""
    tt = _aware(t)
    if start_at is not None and tt < _aware(start_at):
        return False
    if end_at is not None and tt > _aware(end_at):
        return False
    return True


def classify(slope: Optional[float], flat_band: float = 0.05) -> str:
    """把斜率归成 正/平/空 三态。|b| < flat_band mm/周 视为走平。"""
    if slope is None:
        return "空"
    if slope > flat_band:
        return "正"
    if slope < -flat_band:
        return "负"
    return "平"
