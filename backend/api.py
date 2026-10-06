import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from claimer import start as start_claimer
from models import (
    Base,
    ConvergenceLog,
    SectionWindow,
    SessionLocal,
    engine,
    row_dict,
    window_dict,
)
from rates import fit_rate, trend_of

SECRET = os.environ.get("JWT_SECRET", "tunnelconv-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "surveyor": {"role": "writer", "password_hash": pwd.hash("surv123456")},
    "inspector": {"role": "reader", "password_hash": pwd.hash("insp123456")},
}

app = Flask(__name__)


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(ConvergenceLog).count() > 0:
            return
        now = datetime.now(timezone.utc)
        for chainage, delta, expect in (("K12+180", 1.2, "合格"), ("K18+040", 5.6, "超限")):
            from rules import judge

            verdict, reason = judge(delta)
            assert verdict == expect
            db.add(
                ConvergenceLog(
                    chainage=chainage,
                    delta_mm=delta,
                    status="done",
                    verdict=verdict,
                    reason=reason,
                    created_by="surveyor",
                    created_at=now,
                    processed_at=now,
                )
            )
        db.commit()
    finally:
        db.close()


seed()
start_claimer()


def current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth[7:].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


def require_writer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"detail": "未登录"}), 401
        if user["role"] != "writer":
            return jsonify({"detail": "仅测量员可提交收敛读数"}), 403
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "tunnel-convergence-desk"})


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        return jsonify({"detail": "用户名或密码错误"}), 401
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return jsonify({"access_token": token, "username": username, "role": user["role"]})


@app.get("/api/logs")
@require_login
def list_logs():
    db = SessionLocal()
    try:
        rows = db.query(ConvergenceLog).order_by(ConvergenceLog.id.desc()).all()
        return jsonify([row_dict(r) for r in rows])
    finally:
        db.close()


@app.post("/api/logs")
@require_writer
def create_log():
    body = request.get_json(silent=True) or {}
    chainage = (body.get("chainage") or "").strip()
    if not chainage:
        return jsonify({"detail": "桩号不能为空"}), 400
    try:
        delta_mm = float(body.get("delta_mm"))
    except (TypeError, ValueError):
        return jsonify({"detail": "收敛值必须是数字"}), 400
    db = SessionLocal()
    try:
        row = ConvergenceLog(
            chainage=chainage,
            delta_mm=delta_mm,
            status="pending",
            created_by=g.user["username"],
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return jsonify(row_dict(row)), 201
    finally:
        db.close()


def parse_dt(value):
    """解析 ISO 时间；缺时区按 UTC。非法返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@app.get("/api/rates")
@require_login
def list_rates():
    """速率专页数据：按断面列出拟合速率与样本点。

    只取已办结（done）点，按办结时刻落在窗口内（含端点）过滤后做最小二乘拟合。
    """
    db = SessionLocal()
    try:
        done_rows = (
            db.query(ConvergenceLog)
            .filter(ConvergenceLog.status == "done")
            .order_by(ConvergenceLog.chainage, ConvergenceLog.processed_at, ConvergenceLog.id)
            .all()
        )
        windows = {w.chainage: w for w in db.query(SectionWindow).all()}
        by_chainage: dict[str, list] = {}
        for r in done_rows:
            by_chainage.setdefault(r.chainage, []).append(r)
        for c in windows:  # 有窗口但还没有办结点的断面也要列出
            by_chainage.setdefault(c, [])
        sections = []
        for chainage in sorted(by_chainage):
            rows = by_chainage[chainage]
            w = windows.get(chainage)
            done_points = [
                {"id": r.id, "processed_at": r.processed_at.isoformat(), "delta_mm": r.delta_mm}
                for r in rows
                if r.processed_at is not None
            ]
            if w is not None:
                sample = [
                    r for r in rows
                    if r.processed_at is not None and w.start_at <= r.processed_at <= w.end_at
                ]
            else:
                sample = [r for r in rows if r.processed_at is not None]
            rate = fit_rate([(r.processed_at, r.delta_mm) for r in sample])
            sections.append(
                {
                    "chainage": chainage,
                    "window": window_dict(w) if w is not None else None,
                    "points": [
                        {"id": r.id, "processed_at": r.processed_at.isoformat(), "delta_mm": r.delta_mm}
                        for r in sample
                    ],
                    "done_points": done_points,
                    "point_count": len(sample),
                    "rate_mm_per_week": rate,
                    "trend": trend_of(rate),
                }
            )
        return jsonify({"unit": "mm/week", "sections": sections})
    finally:
        db.close()


@app.put("/api/sections/<path:chainage>/window")
@require_login
def put_section_window(chainage):
    """保存断面取点窗口。测量员专属；version 乐观锁，并发只留一笔。"""
    if g.user["role"] != "writer":
        return jsonify({"detail": "仅测量员可调整断面窗口，巡检员只读"}), 403
    body = request.get_json(silent=True) or {}
    start_at = parse_dt(body.get("start_at"))
    end_at = parse_dt(body.get("end_at"))
    if start_at is None or end_at is None:
        return jsonify({"detail": "窗口起止时刻不能为空，需为 ISO 时间"}), 400
    if not start_at < end_at:
        return jsonify({"detail": "窗口起点必须早于终点"}), 400
    try:
        version = int(body.get("version", 0))
    except (TypeError, ValueError):
        return jsonify({"detail": "版本号必须是整数"}), 400
    if version < 0:
        return jsonify({"detail": "版本号不能为负"}), 400
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        if version == 0:
            # 新建窗口；chainage 唯一约束兜住并发建窗，只留一笔
            w = SectionWindow(
                chainage=chainage,
                start_at=start_at,
                end_at=end_at,
                version=1,
                updated_by=g.user["username"],
                updated_at=now,
            )
            db.add(w)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return jsonify({"detail": "该断面窗口已被他人保存，请刷新后重试", "code": "version_conflict"}), 409
            db.refresh(w)
            return jsonify(window_dict(w)), 201
        # 条件更新：版本不匹配一行都改不到，后写者只能拿到 409
        res = db.execute(
            update(SectionWindow)
            .where(SectionWindow.chainage == chainage, SectionWindow.version == version)
            .values(
                start_at=start_at,
                end_at=end_at,
                version=version + 1,
                updated_by=g.user["username"],
                updated_at=now,
            )
        )
        if res.rowcount == 0:
            db.rollback()
            return jsonify({"detail": "窗口版本已过期，他人已抢先保存，请刷新后重试", "code": "version_conflict"}), 409
        db.commit()
        w = db.query(SectionWindow).filter(SectionWindow.chainage == chainage).first()
        return jsonify(window_dict(w))
    finally:
        db.close()
