"""速率专页的取数/拟合/窗口落库装配（不依赖 Flask，便于直接单测）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from models import ConvergenceLog, RateWindow
from rates import classify, fit_weekly_line, in_window, to_utc

FLAT_BAND = 0.05  # |斜率| mm/周 小于此值视为走平


def parse_dt(value) -> Optional[datetime]:
    """解析 ISO8601；None / 空串 => None（表示该侧不限）。非法抛 ValueError。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_utc(value)
    if not isinstance(value, str):
        raise ValueError("时间必须是 ISO8601 字符串")
    text = value.strip().replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError as exc:
        raise ValueError("时间格式不是 ISO8601") from exc


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _fit_section(rows, start_at, end_at):
    """rows: 该断面全部已办结读数（按 processed_at 升序）。返回 fit/line/sample 标记。"""
    sample_idx = [i for i, r in enumerate(rows) if in_window(r.processed_at, start_at, end_at)]
    sample_rows = [rows[i] for i in sample_idx]
    sample_set = set(sample_idx)

    points = [
        {
            "id": r.id,
            "x": _iso(to_utc(r.processed_at)),
            "y": float(r.delta_mm),
            "in_sample": i in sample_set,
        }
        for i, r in enumerate(rows)
    ]

    fit_out = None
    line_out = None
    if sample_rows:
        fit = fit_weekly_line([(r.processed_at, float(r.delta_mm)) for r in sample_rows])
        if fit is not None:
            slope = fit["slope"]
            t0 = fit["t0"]
            xs = [(to_utc(r.processed_at) - t0).total_seconds() / 604800.0 for r in sample_rows]
            ys = [float(r.delta_mm) for r in sample_rows]
            x_min, x_max = min(xs), max(xs)
            y_at = lambda x: fit["intercept"] + slope * x  # noqa: E731
            # 判定系数（窗口内直线对样本的解释度），无法计算时给 None。
            r2 = None
            if len(ys) >= 2:
                mean_y = sum(ys) / len(ys)
                ss_tot = sum((y - mean_y) ** 2 for y in ys)
                ss_res = sum((y - y_at(x)) ** 2 for x, y in zip(xs, ys))
                if ss_tot > 0:
                    r2 = max(0.0, 1.0 - ss_res / ss_tot)
            fit_out = {
                "slope": slope,
                "n": fit["n"],
                "state": classify(slope, FLAT_BAND),
                "r2": r2,
                "t0": _iso(t0),
            }
            line_out = {
                "x0": _iso(to_utc(sample_rows[0].processed_at)),
                "y0": y_at(x_min),
                "x1": _iso(to_utc(sample_rows[-1].processed_at)),
                "y1": y_at(x_max),
            }

    return points, len(sample_rows), fit_out, line_out


def collect_sections(db) -> list[dict]:
    """按断面返回域、窗口、拟合速率、样本点。只把已办结读数喂给拟合。"""
    logs = (
        db.query(ConvergenceLog)
        .filter(ConvergenceLog.status == "done", ConvergenceLog.processed_at.isnot(None))
        .order_by(ConvergenceLog.processed_at)
        .all()
    )

    by_chain: dict[str, list] = {}
    order: list[str] = []
    # 断面名以"全部读数"为准：只有 pending 的断面也列出（样本为 0、速率为空）。
    all_chain_rows = db.query(ConvergenceLog.chainage).distinct().all()
    for (ch,) in all_chain_rows:
        if ch not in by_chain:
            by_chain[ch] = []
            order.append(ch)
    for r in logs:
        if r.chainage not in by_chain:
            by_chain[r.chainage] = []
            order.append(r.chainage)
        by_chain[r.chainage].append(r)

    windows = {w.chainage: w for w in db.query(RateWindow).all()}

    sections = []
    for ch in sorted(order):
        rows = by_chain.get(ch, [])
        win = windows.get(ch)
        start_at = to_utc(win.start_at) if (win and win.start_at) else None
        end_at = to_utc(win.end_at) if (win and win.end_at) else None

        domain = None
        if rows:
            domain = {"start_at": _iso(to_utc(rows[0].processed_at)),
                      "end_at": _iso(to_utc(rows[-1].processed_at))}

        points, n_sample, fit_out, line_out = _fit_section(rows, start_at, end_at)
        sections.append({
            "chainage": ch,
            "domain": domain,
            "window": {
                "start_at": _iso(start_at),
                "end_at": _iso(end_at),
                "version": win.version,
                "updated_by": win.updated_by,
                "updated_at": _iso(to_utc(win.updated_at)) if win.updated_at else None,
            } if win else None,
            "total_done": len(rows),
            "sample_count": n_sample,
            "fit": fit_out,
            "line": line_out,
            "points": points,
        })
    return sections


def upsert_window(db, chainage: str, start_raw, end_raw, expected_version, username: str):
    """测量员拖拽后落库。返回 (window_dict, error)；error 为 None 表示成功。

    并发约束：行级锁 + version 乐观锁。两人几乎同时改同一断面时，
    先提交的写入并 version+1，后提交的 version 不匹配返回 ("conflict", 当前窗口)。
    """
    chainage = (chainage or "").strip()
    if not chainage:
        return None, ("bad_request", "桩号不能为空")
    try:
        start_at = parse_dt(start_raw)
        end_at = parse_dt(end_raw)
    except ValueError as exc:
        return None, ("bad_request", str(exc))
    if start_at and end_at and start_at > end_at:
        return None, ("bad_request", "窗口起点不能晚于终点")

    try:
        expected = None if expected_version in (None, "") else int(expected_version)
    except (TypeError, ValueError):
        return None, ("bad_request", "version 必须是整数")

    row = db.query(RateWindow).filter(RateWindow.chainage == chainage).with_for_update().first()
    now = datetime.now(timezone.utc)

    if row is None:
        if expected not in (None, 0):
            return None, ("conflict", None)
        row = RateWindow(
            chainage=chainage, start_at=start_at, end_at=end_at,
            version=1, updated_by=username, updated_at=now,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # 两人并发首建同一断面：唯一约束只放一笔，另一笔转成冲突。
            db.rollback()
            current = db.query(RateWindow).filter(RateWindow.chainage == chainage).first()
            return None, ("conflict", _window_public(current) if current else None)
        db.refresh(row)
        return _window_public(row), None

    if expected != row.version:
        db.rollback()
        return None, ("conflict", _window_public(row))

    # 原子条件更新：仅当 version 仍是读取时的值才写入并 +1。
    # 即使行锁因隔离级别被穿透，rowcount=0 也会把落败那笔拒掉，库里只留一笔。
    result = db.execute(
        update(RateWindow)
        .where(RateWindow.id == row.id, RateWindow.version == expected)
        .values(
            start_at=start_at,
            end_at=end_at,
            version=expected + 1,
            updated_by=username,
            updated_at=now,
        )
    )
    db.commit()
    current = db.query(RateWindow).filter(RateWindow.id == row.id).first()
    if result.rowcount == 0:
        return None, ("conflict", _window_public(current) if current else None)
    return _window_public(current), None


def _window_public(row: RateWindow) -> dict:
    return {
        "chainage": row.chainage,
        "start_at": _iso(to_utc(row.start_at)) if row.start_at else None,
        "end_at": _iso(to_utc(row.end_at)) if row.end_at else None,
        "version": row.version,
        "updated_by": row.updated_by,
        "updated_at": _iso(to_utc(row.updated_at)) if row.updated_at else None,
    }
