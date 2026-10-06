import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, request
from jose import JWTError, jwt
from passlib.context import CryptContext

from claimer import start as start_claimer
from models import Base, ConvergenceLog, SessionLocal, engine, row_dict
import rate_service

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
        # 连续三周抬高、且都已办结的断面：最小二乘斜率应为正（0.6 mm/周）。
        for weeks_ago, delta in ((2, 0.6), (1, 1.2), (0, 1.8)):
            ts = now - timedelta(weeks=weeks_ago)
            verdict, reason = judge(delta)
            db.add(
                ConvergenceLog(
                    chainage="K30+010",
                    delta_mm=delta,
                    status="done",
                    verdict=verdict,
                    reason=reason,
                    created_by="surveyor",
                    created_at=ts,
                    processed_at=ts,
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


@app.get("/api/rate/sections")
@require_login
def rate_sections():
    """速率专页中块：按断面列出拟合速率与样本点（pending 点不在其中）。"""
    db = SessionLocal()
    try:
        return jsonify(rate_service.collect_sections(db))
    finally:
        db.close()


@app.put("/api/rate/window")
@require_writer
def update_rate_window():
    """上块拖拽窗口落库。仅测量员；带 version 乐观锁，并发落败返回 409。

    巡检员（reader）在 require_writer 即被拦下（403），既改不了也无法借接口"上报"。
    """
    body = request.get_json(silent=True) or {}
    chainage = (body.get("chainage") or "").strip()
    start_at = body.get("start_at")
    end_at = body.get("end_at")
    expected_version = body.get("version")

    db = SessionLocal()
    try:
        window, error = rate_service.upsert_window(
            db, chainage, start_at, end_at, expected_version, g.user["username"]
        )
        if error is None:
            return jsonify(window), 200
        code, payload = error
        if code == "bad_request":
            return jsonify({"detail": payload}), 400
        # conflict：409 并回带服务器现存的那一笔，前端据此对齐。
        return jsonify(
            {"detail": "该断面窗口刚被他人更新，已为你加载最新一笔", "current": payload}
        ), 409
    finally:
        db.close()
