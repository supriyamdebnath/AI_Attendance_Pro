from __future__ import annotations

import io
import base64
import json
import os
import secrets
import shutil
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import wraps
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
from time import perf_counter
from typing import Callable

import cv2
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_sock import Sock
from sqlalchemy import func, or_
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from flask_mail import Mail, Message
except ImportError:  # Keeps local tools usable until requirements are installed.
    Mail = None
    Message = None

from capture import capture_faces, save_face_sample_from_frame
from database import ATTENDANCE_CSV, DB_PATH, Attendance, SystemLog, User, configure_database, db, ensure_csv_header, migrate_schema
from recognize import annotate_frame, last_event, save_snapshot
from train import TRAINER_PATH, train_model

BASE_DIR = Path(__file__).resolve().parent
AVATAR_DIR = BASE_DIR / "static" / "images" / "avatars"
ATTENDANCE_REPORT_DIR = BASE_DIR / "Attendance" / "reports"
SNAPSHOT_DIR = BASE_DIR / "static" / "snapshots"
HAAR_SOURCE = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
HAAR_TARGET = BASE_DIR / "haarcascade_frontalface_default.xml"
ALLOWED_AVATARS = {"png", "jpg", "jpeg", "webp"}

load_dotenv(BASE_DIR / ".env")
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "replace-this-secret-before-production")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", os.getenv("SMTP_HOST", "smtp.gmail.com"))
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", os.getenv("SMTP_PORT", "587")))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() in {"1", "true", "yes"}
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", os.getenv("SMTP_USERNAME"))
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", os.getenv("SMTP_PASSWORD"))
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER", os.getenv("SMTP_FROM", app.config["MAIL_USERNAME"]))
configure_database(app)
mail = Mail(app) if Mail else None
serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
sock = Sock(app)

camera_lock = Lock()
latest_frame_lock = Lock()
live_clients_lock = Lock()
# Browser clients own camera hardware through getUserMedia. Render receives
# sampled frames only, which keeps recognition cloud-compatible and avoids
# requiring a physical webcam inside the hosted container.
camera_enabled = False
recognition_status = "Camera idle"
latest_browser_frame = None
latest_frame_at = 0.0
live_clients: set[Queue[str]] = set()


def broadcast_live(payload: dict) -> None:
    message = json.dumps(payload, default=str)
    stale: list[Queue[str]] = []
    with live_clients_lock:
        clients = list(live_clients)
    for client in clients:
        try:
            client.put_nowait(message)
        except Exception:
            stale.append(client)
    if stale:
        with live_clients_lock:
            for client in stale:
                live_clients.discard(client)


def bootstrap_runtime() -> None:
    # Render free tier filesystems are ephemeral between deploys/restarts.
    # Keep generated snapshots, reports, SQLite data, and trainer files out of Git;
    # this bootstrap recreates the required folders so gunicorn can start cleanly.
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    ATTENDANCE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_csv_header()
    if not HAAR_TARGET.exists() and HAAR_SOURCE.exists():
        shutil.copyfile(HAAR_SOURCE, HAAR_TARGET)
    with app.app_context():
        migrate_schema()
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            db.session.add(
                User(
                    username="admin",
                    password=generate_password_hash("Admin@12345"),
                    role="admin",
                    email="admin@example.com",
                    avatar="default_admin.png",
                    full_name="System Administrator",
                    id_number="ADMIN-001",
                )
            )
            db.session.commit()
        for user in User.query.filter(or_(User.id_number == None, User.id_number == "")).all():
            user.id_number = f"USR-{user.id:04d}"
        for user in User.query.filter_by(role="user").all():
            user.role = "student"
        for record in Attendance.query.filter(or_(Attendance.role == None, Attendance.role == "")).all():
            record.role = record.user.role if record.user else "student"
        for record in Attendance.query.filter(or_(Attendance.id_number == None, Attendance.id_number == "")).all():
            record.id_number = record.user.id_number if record.user else str(record.user_id)
        for record in Attendance.query.filter(Attendance.created_at == None).all():
            record.created_at = datetime.combine(record.date, record.time)
        db.session.commit()


bootstrap_runtime()


def decode_browser_frame(image_data: str | None):
    if not image_data:
        return None
    if "," in image_data:
        _prefix, image_data = image_data.split(",", 1)
    try:
        raw = base64.b64decode(image_data, validate=True)
    except (ValueError, TypeError):
        return None
    array = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def camera_event_payload(event: dict | None = None) -> dict:
    payload = dict(event or last_event)
    payload["enabled"] = camera_enabled
    payload["camera"] = "Camera ON" if camera_enabled else "Camera OFF"
    payload["message"] = payload.get("message") or (recognition_status if camera_enabled else "Camera idle")
    return payload


def current_user() -> User | None:
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        if user.role != "admin":
            flash("Administrator access is required.", "danger")
            return redirect(url_for("user_dashboard"))
        return view(*args, **kwargs)

    return wrapped


def attendance_percent(user_id: int) -> int:
    days_elapsed = max((date.today() - date(date.today().year, 1, 1)).days + 1, 1)
    attended = Attendance.query.filter_by(user_id=user_id).count()
    return min(round((attended / days_elapsed) * 100), 100)


def dashboard_period_bounds(period: str = "daily") -> tuple[date, date]:
    today = date.today()
    if period == "weekly":
        return today - timedelta(days=6), today
    if period == "monthly":
        return today.replace(day=1), today
    return today, today


def period_attendance_query(period: str = "daily"):
    start, end = dashboard_period_bounds(period)
    return Attendance.query.filter(Attendance.date >= start, Attendance.date <= end)


def dashboard_metrics(period: str = "daily") -> dict[str, object]:
    today = date.today()
    total_attendance = period_attendance_query(period).count()
    successful_accuracy = period_attendance_query(period).with_entities(func.avg(Attendance.recognition_accuracy)).scalar() or 0
    total_people = max(User.query.filter(User.role.in_(["student", "employee"])).count(), 1)
    present_today = Attendance.query.filter_by(date=today).count()
    late_after = time(9, 30)
    late_checkins = Attendance.query.filter(Attendance.date == today, Attendance.time > late_after).count()
    attendance_rate = min(round((present_today / total_people) * 100), 100)
    return {
        "users": User.query.count(),
        "active_users": db.session.query(Attendance.user_id).filter_by(date=today).distinct().count(),
        "admins": User.query.filter_by(role="admin").count(),
        "students": User.query.filter_by(role="student").count(),
        "employees": User.query.filter_by(role="employee").count(),
        "today_present": present_today,
        "attendance_rate": attendance_rate,
        "late_checkins": late_checkins,
        "success_rate": round(successful_accuracy or (100 if total_attendance else 0)),
        "camera": "Camera ON" if camera_enabled else "Camera OFF",
        "trained": TRAINER_PATH.exists(),
        "period": period,
    }


def attendance_chart_payload(user_id: int | None = None, period: str = "monthly") -> dict[str, list]:
    query = Attendance.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    else:
        start, end = dashboard_period_bounds(period)
        query = query.filter(Attendance.date >= start, Attendance.date <= end)
    rows = query.order_by(Attendance.date.asc()).all()
    counter = Counter(row.date.isoformat() for row in rows)
    return {"labels": list(counter.keys()), "values": list(counter.values())}


def role_chart_payload(period: str = "monthly") -> dict[str, list]:
    start, end = dashboard_period_bounds(period)
    rows = (
        db.session.query(Attendance.role, func.count(Attendance.id))
        .filter(Attendance.date >= start, Attendance.date <= end)
        .group_by(Attendance.role)
        .all()
    )
    return {"labels": [role.title() for role, _count in rows], "values": [count for _role, count in rows]}


def accuracy_chart_payload(period: str = "monthly") -> dict[str, list]:
    start, end = dashboard_period_bounds(period)
    rows = Attendance.query.filter(Attendance.date >= start, Attendance.date <= end).order_by(Attendance.created_at.asc()).limit(30).all()
    return {
        "labels": [row.date.isoformat() for row in rows],
        "values": [row.recognition_accuracy for row in rows],
    }


def ai_insights(period: str = "daily") -> list[dict[str, str]]:
    metrics = dashboard_metrics(period)
    insights: list[dict[str, str]] = []
    if not metrics["trained"]:
        insights.append({"title": "Model training needed", "detail": "Train the recognition model after enrolling face samples."})
    if metrics["attendance_rate"] < 75:
        insights.append({"title": "Attendance below target", "detail": f"{metrics['attendance_rate']}% attendance today. Review absentees and late arrivals."})
    if metrics["late_checkins"]:
        insights.append({"title": "Late arrivals detected", "detail": f"{metrics['late_checkins']} late check-ins after 09:30 today."})
    if metrics["success_rate"] >= 85:
        insights.append({"title": "Recognition healthy", "detail": f"Average recognition accuracy is {metrics['success_rate']}% for the selected period."})
    if not insights:
        insights.append({"title": "System stable", "detail": "Attendance, camera, and recognition metrics are operating normally."})
    return insights[:4]


def avatar_filename(user: User | None) -> str:
    if not user:
        return "placeholder.png"
    candidate = AVATAR_DIR / (user.avatar or "")
    if candidate.exists():
        return candidate.name
    return "default_admin.png" if user.role == "admin" else "default_user.png"


@app.context_processor
def inject_globals():
    user = current_user()
    session.setdefault("csrf_token", secrets.token_urlsafe(32))
    total_logs = SystemLog.query.count() if user else 0
    unseen = max(total_logs - session.get("notification_seen_count", 0), 0)
    return {
        "auth_user": user,
        "avatar_for": avatar_filename,
        "csrf_token": session["csrf_token"],
        "notification_count": "99+" if unseen > 99 else unseen,
        "notifications": notification_items(8) if user else [],
        "system_status": "Ready" if TRAINER_PATH.exists() else "Training Required",
    }


@app.before_request
def protect_state_changes():
    if request.method != "POST":
        return None
    if request.headers.get("X-Requested-With") == "fetch":
        return None
    token = request.form.get("csrf_token")
    if token != session.get("csrf_token"):
        flash("Security token expired. Please try again.", "warning")
        return redirect(request.referrer or url_for("index"))
    return None


def write_log(action: str, edited_by: int | None = None) -> None:
    db.session.add(SystemLog(action=action, edited_by=edited_by))


def notification_items(limit: int = 12) -> list[dict[str, str]]:
    return [
        {
            "action": log.action,
            "time": log.timestamp.strftime("%H:%M:%S"),
            "date": log.timestamp.strftime("%Y-%m-%d"),
        }
        for log in SystemLog.query.order_by(SystemLog.timestamp.desc()).limit(limit).all()
    ]


@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if user.role == "admin" else "user_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        if not username or not password:
            flash("Username and password are required.", "warning")
            return render_template("login.html")
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password, password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user.id
        session["notification_seen_count"] = SystemLog.query.count()
        session.permanent = remember
        flash(f"Welcome back, {user.full_name or user.username}.", "success")
        return redirect(url_for("admin_dashboard" if user.role == "admin" else "user_dashboard"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    stop_camera()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            otp = f"{secrets.randbelow(900000) + 100000}"
            user.reset_otp_hash = generate_password_hash(otp)
            user.reset_otp_expires = datetime.utcnow() + timedelta(minutes=10)
            write_log(f"Password reset OTP sent for {user.username}", user.id)
            db.session.commit()
            try:
                send_otp_email(user.email, otp)
            except Exception:
                flash("Email service is unavailable. Check Gmail SMTP credentials or internet access.", "danger")
                return redirect(url_for("forgot_password"))
            session["reset_user_id"] = user.id
            flash("OTP sent to your Gmail inbox. It expires in 10 minutes.", "success")
            return redirect(url_for("verify_otp"))
        flash("If that account exists, a secure OTP has been sent.", "success")
        return redirect(url_for("forgot_password"))
    return render_template("forgot_password.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    user = db.session.get(User, session.get("reset_user_id"))
    if not user:
        flash("Start password recovery again.", "warning")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        expired = not user.reset_otp_expires or user.reset_otp_expires < datetime.utcnow()
        if expired or not user.reset_otp_hash or not check_password_hash(user.reset_otp_hash, otp):
            flash("Invalid or expired OTP.", "danger")
            return render_template("forgot_password.html", otp_mode=True)
        session["otp_verified"] = True
        flash("OTP verified. Set a new password.", "success")
        return redirect(url_for("reset_password_otp"))
    return render_template("forgot_password.html", otp_mode=True)


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password_otp():
    user = db.session.get(User, session.get("reset_user_id"))
    if not user or not session.get("otp_verified"):
        flash("Verify your OTP first.", "warning")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8 or password != confirm:
            flash("Use at least 8 characters and make both passwords match.", "warning")
            return render_template("forgot_password.html", reset_mode=True)
        user.password = generate_password_hash(password)
        user.reset_otp_hash = None
        user.reset_otp_expires = None
        write_log(f"Password reset completed for {user.username}", user.id)
        db.session.commit()
        session.pop("reset_user_id", None)
        session.pop("otp_verified", None)
        flash("Password updated. You can sign in now.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html", reset_mode=True)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    try:
        payload = serializer.loads(token, salt="password-reset", max_age=1800)
    except SignatureExpired:
        flash("That reset link expired. Request a fresh one.", "warning")
        return redirect(url_for("forgot_password"))
    except BadSignature:
        flash("That reset link is invalid.", "danger")
        return redirect(url_for("forgot_password"))
    user = db.session.get(User, payload["user_id"])
    if not user:
        flash("Account not found.", "danger")
        return redirect(url_for("forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if len(password) < 8 or password != confirm:
            flash("Use at least 8 characters and make both passwords match.", "warning")
            return render_template("forgot_password.html", reset_mode=True, token=token)
        user.password = generate_password_hash(password)
        db.session.commit()
        flash("Password updated. You can sign in now.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html", reset_mode=True, token=token)


def send_reset_email(address: str, reset_link: str) -> None:
    if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        app.logger.info("Password reset link for %s: %s", address, reset_link)
        return
    if not mail or not Message:
        app.logger.info("Install Flask-Mail to send reset email. Link for %s: %s", address, reset_link)
        return
    message = Message(
        subject="Reset your AI Attendance Pro password",
        recipients=[address],
        body=f"Use this secure reset link within 30 minutes:\n\n{reset_link}",
    )
    try:
        mail.send(message)
    except Exception as exc:
        app.logger.exception("Password reset email failed: %s", exc)
        raise


def send_otp_email(address: str, otp: str) -> None:
    if not app.config["MAIL_USERNAME"] or not app.config["MAIL_PASSWORD"]:
        app.logger.info("Password reset OTP for %s: %s", address, otp)
        return
    if not mail or not Message:
        app.logger.info("Install Flask-Mail to send OTP. OTP for %s: %s", address, otp)
        return
    message = Message(
        subject="Your AI Attendance Pro recovery OTP",
        recipients=[address],
        body=f"Your password reset OTP is {otp}. It expires in 10 minutes.",
    )
    mail.send(message)


@app.route("/admin")
@admin_required
def admin_dashboard():
    period = request.args.get("period", "daily")
    if period not in {"daily", "weekly", "monthly"}:
        period = "daily"
    recent = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).limit(8).all()
    users = User.query.order_by(User.created_at.desc()).limit(8).all()
    logs = SystemLog.query.order_by(SystemLog.timestamp.desc()).limit(6).all()
    total_possible = max(User.query.filter(User.role.in_(["student", "employee"])).count(), 1)
    today_present = Attendance.query.filter_by(date=date.today()).count()
    return render_template(
        "admin_dashboard.html",
        metrics=dashboard_metrics(period),
        recent=recent,
        users=users,
        chart=attendance_chart_payload(period=period),
        role_chart=role_chart_payload(period),
        accuracy_chart=accuracy_chart_payload(period),
        insights=ai_insights(period),
        period=period,
        logs=logs,
        attendance_percentage=min(round((today_present / total_possible) * 100), 100),
        camera_enabled=camera_enabled,
        recognition_status=recognition_status,
    )


@app.route("/dashboard")
@login_required
def user_dashboard():
    user = current_user()
    if user.role == "admin":
        return redirect(url_for("admin_dashboard"))
    history = Attendance.query.filter_by(user_id=user.id).order_by(Attendance.date.desc()).limit(12).all()
    today_attendance = Attendance.query.filter_by(user_id=user.id, date=date.today()).first()
    return render_template(
        "user_dashboard.html",
        history=history,
        percentage=attendance_percent(user.id),
        chart=attendance_chart_payload(user.id),
        last_attendance=history[0] if history else None,
        today_attendance=today_attendance,
        today_date=date.today(),
    )


@app.route("/attendance")
@login_required
def attendance_view():
    user = current_user()
    query = Attendance.query
    if user.role != "admin":
        query = query.filter_by(user_id=user.id)
    rows = query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()
    return render_template("attendance.html", rows=rows)


@app.route("/reports")
@admin_required
def reports():
    rows = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()
    return render_template("reports.html", rows=rows)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        user.full_name = request.form.get("full_name", user.full_name).strip() or user.full_name
        user.email = request.form.get("email", user.email).strip().lower() or user.email
        avatar = request.files.get("avatar")
        if avatar and "." in avatar.filename:
            extension = avatar.filename.rsplit(".", 1)[1].lower()
            if extension in ALLOWED_AVATARS:
                filename = secure_filename(f"user-{user.id}-{secrets.token_hex(4)}.{extension}")
                avatar.save(AVATAR_DIR / filename)
                user.avatar = filename
            else:
                flash("Upload PNG, JPG, JPEG, or WEBP avatars.", "warning")
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if password:
            if len(password) < 8 or password != confirm_password:
                flash("Use at least 8 characters and make both passwords match.", "warning")
                return redirect(url_for("profile"))
            user.password = generate_password_hash(password)
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", user=user, percentage=attendance_percent(user.id))


@app.route("/settings")
@admin_required
def settings():
    db_ok = DB_PATH.exists()
    storage_mb = sum(path.stat().st_size for path in SNAPSHOT_DIR.glob("*.jpg")) / (1024 * 1024) if SNAPSHOT_DIR.exists() else 0
    return render_template(
        "settings.html",
        trained=TRAINER_PATH.exists(),
        smtp_ready=bool(app.config["MAIL_USERNAME"] and app.config["MAIL_PASSWORD"]),
        camera_enabled=camera_enabled,
        db_ok=db_ok,
        storage_mb=round(storage_mb, 2),
    )


@app.post("/settings/test-smtp")
@admin_required
def test_smtp():
    user = current_user()
    if not user or not user.email:
        return jsonify({"ok": False, "message": "No admin email available for the test."}), 400
    try:
        send_otp_email(user.email, "000000")
    except Exception:
        return jsonify({"ok": False, "message": "SMTP test failed. Check Gmail credentials and app password."}), 503
    return jsonify({"ok": True, "message": "SMTP test message sent."})


@app.route("/users/new", methods=["GET", "POST"])
@admin_required
def add_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        role = request.form.get("role", "user")
        id_number = request.form.get("id_number", "").strip()
        password = request.form.get("password", "")
        if not all([username, full_name, email, password, id_number]):
            flash("All user fields are required.", "warning")
            return render_template("add_user.html")
        if role not in {"admin", "student", "employee"}:
            role = "student"
        if User.query.filter((User.username == username) | (User.email == email) | (User.id_number == id_number)).first():
            flash("Username, email, or ID number already exists.", "danger")
            return render_template("add_user.html")
        avatar = "default_admin.png" if role == "admin" else "default_user.png"
        user = User(
            username=username,
            full_name=full_name,
            email=email,
            role=role,
            avatar=avatar,
            id_number=id_number,
            password=generate_password_hash(password),
        )
        uploaded = request.files.get("avatar")
        if uploaded and "." in uploaded.filename:
            extension = uploaded.filename.rsplit(".", 1)[1].lower()
            if extension in ALLOWED_AVATARS:
                filename = secure_filename(f"pending-{secrets.token_hex(4)}.{extension}")
                uploaded.save(AVATAR_DIR / filename)
                user.avatar = filename
        db.session.add(user)
        write_log(f"User added: {user.username}", current_user().id if current_user() else None)
        db.session.commit()
        flash("User created. Capture samples or upload dataset images next.", "success")
        return redirect(url_for("add_user"))
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("add_user.html", users=users)


@app.post("/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id: int):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "warning")
    elif user.username == "admin":
        flash("The bootstrap administrator cannot be deleted.", "danger")
    else:
        write_log(f"User deleted: {user.username}", current_user().id if current_user() else None)
        db.session.delete(user)
        db.session.commit()
        flash("User deleted.", "success")
    return redirect(url_for("add_user"))


@app.post("/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id: int):
    target = db.session.get(User, user_id)
    actor = current_user()
    if not target:
        flash("User not found.", "warning")
        return redirect(url_for("add_user"))
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    id_number = request.form.get("id_number", "").strip()
    role = request.form.get("role", target.role)
    password = request.form.get("password", "")
    if not all([full_name, username, email, id_number]) or role not in {"admin", "student", "employee"}:
        flash("Complete all required fields with a valid role.", "warning")
        return redirect(url_for("add_user"))
    duplicate = User.query.filter(
        User.id != target.id,
        (User.username == username) | (User.email == email) | (User.id_number == id_number),
    ).first()
    if duplicate:
        flash("Username, email, or ID number already belongs to another user.", "danger")
        return redirect(url_for("add_user"))
    target.full_name = full_name
    target.username = username
    target.email = email
    target.id_number = id_number
    target.role = role
    target.edited_by = actor.id if actor else None
    target.edited_at = datetime.utcnow()
    if password:
        target.password = generate_password_hash(password)
    uploaded = request.files.get("avatar")
    if uploaded and "." in uploaded.filename:
        extension = uploaded.filename.rsplit(".", 1)[1].lower()
        if extension in ALLOWED_AVATARS:
            filename = secure_filename(f"user-{target.id}-{secrets.token_hex(4)}.{extension}")
            uploaded.save(AVATAR_DIR / filename)
            target.avatar = filename
    write_log(f"Edited user {target.username}", actor.id if actor else None)
    db.session.commit()
    flash("User updated.", "success")
    return redirect(url_for("add_user"))


@app.post("/users/<int:user_id>/capture")
@admin_required
def capture_user(user_id: int):
    result = capture_faces(user_id)
    if result["ok"]:
        write_log(f"Face samples captured for user #{user_id}", current_user().id if current_user() else None)
        db.session.commit()
    flash(result["message"], "success" if result["ok"] else "danger")
    return redirect(url_for("add_user"))


@app.post("/api/users/<int:user_id>/face-sample")
@admin_required
def browser_face_sample(user_id: int):
    if not db.session.get(User, user_id):
        return jsonify({"ok": False, "message": "User not found."}), 404
    payload = request.get_json(silent=True) or {}
    frame = decode_browser_frame(payload.get("image"))
    if frame is None:
        return jsonify({"ok": False, "message": "Invalid camera frame."}), 400
    result = save_face_sample_from_frame(user_id, frame)
    if result["ok"]:
        write_log(f"Browser face sample captured for user #{user_id}", current_user().id if current_user() else None)
        db.session.commit()
    return jsonify(result), 200 if result["ok"] else 422


def dashboard_payload(period: str = "daily") -> dict:
    if period not in {"daily", "weekly", "monthly"}:
        period = "daily"
    recent = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).limit(8).all()
    return {
        "metrics": dashboard_metrics(period),
        "chart": attendance_chart_payload(period=period),
        "role_chart": role_chart_payload(period),
        "accuracy_chart": accuracy_chart_payload(period),
        "insights": ai_insights(period),
        "recent": [
            {
                "name": row.name,
                "role": row.role,
                "id_number": row.id_number or (row.user.id_number if row.user else row.user_id),
                "date": row.date.isoformat(),
                "time": row.time.strftime("%H:%M:%S"),
                "status": row.status,
                "accuracy": row.recognition_accuracy,
                "snapshot_path": row.snapshot_path,
            }
            for row in recent
        ],
    }


@app.post("/train")
@admin_required
def train_from_dashboard():
    result = train_model()
    return jsonify(result), 200 if result["ok"] else 400


def stop_camera() -> None:
    global camera_enabled, recognition_status, latest_browser_frame, latest_frame_at
    with camera_lock:
        camera_enabled = False
        recognition_status = "Camera idle"
        latest_frame_at = 0.0
        with latest_frame_lock:
            latest_browser_frame = None
        last_event.clear()
        last_event.update({"state": "idle", "message": "Camera idle", "camera": "Camera OFF", "fps": 0})
    broadcast_live({"type": "camera", "event": camera_event_payload()})


@app.post("/camera/start")
@admin_required
def camera_start():
    global camera_enabled, recognition_status
    with camera_lock:
        camera_enabled = True
        recognition_status = "Waiting for browser camera"
    last_event.clear()
    last_event.update({"state": "scanning", "message": "Waiting for browser camera", "camera": "Camera ON", "fps": 0})
    write_log("Browser camera session started", current_user().id if current_user() else None)
    db.session.commit()
    event = camera_event_payload()
    broadcast_live({"type": "camera", "event": event, "summary": dashboard_payload()})
    return jsonify({"ok": True, "message": "Browser camera ready.", "event": event})


@app.post("/camera/stop")
@admin_required
def camera_stop():
    stop_camera()
    write_log("Browser camera session stopped", current_user().id if current_user() else None)
    db.session.commit()
    return jsonify({"ok": True, "message": "Camera stopped."})


@app.post("/api/camera/frame")
@admin_required
def camera_frame():
    global latest_browser_frame, latest_frame_at, recognition_status
    if not camera_enabled:
        return jsonify({"ok": False, "message": "Camera is off."}), 409
    payload = request.get_json(silent=True) or {}
    frame = decode_browser_frame(payload.get("image"))
    if frame is None:
        return jsonify({"ok": False, "message": "Invalid camera frame."}), 400

    now = perf_counter()
    fps = float(payload.get("fps") or 0)
    if not fps and latest_frame_at:
        fps = 1 / max(now - latest_frame_at, 0.001)
    latest_frame_at = now
    with latest_frame_lock:
        latest_browser_frame = frame.copy()

    _annotated, status, event = annotate_frame(frame, app.app_context, fps)
    recognition_status = event.get("message") or status or "Scanning"
    event = camera_event_payload(event)
    payload = {"type": "camera", "event": event}
    if event.get("state") == "recognized":
        payload["summary"] = dashboard_payload()
    broadcast_live(payload)
    return jsonify({"ok": True, "event": event})


@app.route("/camera")
@admin_required
def camera_page():
    return redirect(url_for("live_monitoring"))


@app.route("/live-monitoring")
@admin_required
def live_monitoring():
    return render_template("camera.html", recognition_status=recognition_status)


@app.get("/api/camera/status")
@admin_required
def camera_status():
    return jsonify(camera_event_payload())


@sock.route("/ws/live")
def live_updates(ws):
    user = current_user()
    if not user or user.role != "admin":
        ws.close()
        return
    client: Queue[str] = Queue(maxsize=20)
    with live_clients_lock:
        live_clients.add(client)
    try:
        ws.send(json.dumps({"type": "camera", "event": camera_event_payload(), "summary": dashboard_payload()}))
        while True:
            try:
                message = client.get(timeout=25)
            except Empty:
                message = json.dumps({"type": "heartbeat", "event": camera_event_payload()})
            ws.send(message)
    except Exception:
        pass
    finally:
        with live_clients_lock:
            live_clients.discard(client)


@app.post("/camera/snapshot")
@admin_required
def camera_snapshot():
    global latest_browser_frame
    payload = request.get_json(silent=True) or {}
    frame = decode_browser_frame(payload.get("image"))
    if frame is None:
        with latest_frame_lock:
            frame = latest_browser_frame.copy() if latest_browser_frame is not None else None
    if not camera_enabled or frame is None:
        return jsonify({"ok": False, "message": "Camera is off. Start live monitoring first."}), 409
    path = save_snapshot(frame, None)
    retake = request.args.get("retake") == "1" or payload.get("retake") is True
    record = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).first()
    if record and (retake or not record.snapshot_path):
        record.snapshot_path = path
    write_log("Snapshot retaken" if retake else "Snapshot captured", current_user().id if current_user() else None)
    db.session.commit()
    event = camera_event_payload({"state": "snapshot", "message": "Snapshot retaken" if retake else "Snapshot captured", "snapshot_path": path})
    broadcast_live({"type": "camera", "event": event, "summary": dashboard_payload()})
    return jsonify({"ok": True, "message": "Snapshot captured.", "snapshot_path": path, "event": event})


@app.get("/api/notifications")
@login_required
def api_notifications():
    total_logs = SystemLog.query.count()
    session["notification_seen_count"] = total_logs
    return jsonify({"count": 0, "items": notification_items(20)})


@app.post("/api/notifications/clear")
@login_required
def clear_notifications():
    session["notification_seen_count"] = SystemLog.query.count()
    return jsonify({"ok": True, "count": 0, "items": []})


@app.get("/api/dashboard/summary")
@admin_required
def dashboard_summary():
    return jsonify(dashboard_payload(request.args.get("period", "daily")))


@app.get("/api/users/search")
@admin_required
def search_users():
    term = request.args.get("q", "").strip()
    query = User.query
    if term:
        like = f"%{term}%"
        query = query.filter(
            (User.username.ilike(like))
            | (User.full_name.ilike(like))
            | (User.email.ilike(like))
            | (User.role.ilike(like))
            | (User.id_number.ilike(like))
        )
    items = [
        {"id": user.id, "name": user.full_name, "username": user.username, "role": user.role, "email": user.email, "id_number": user.id_number}
        for user in query.order_by(User.full_name.asc()).limit(20).all()
    ]
    return jsonify(items)


@app.get("/api/search")
@login_required
def global_search():
    term = request.args.get("q", "").strip()
    if len(term) < 2:
        return jsonify({"items": []})
    like = f"%{term}%"
    items: list[dict[str, str]] = []
    users = User.query.filter(
        (User.username.ilike(like))
        | (User.full_name.ilike(like))
        | (User.email.ilike(like))
        | (User.role.ilike(like))
        | (User.id_number.ilike(like))
    ).order_by(User.full_name.asc()).limit(6).all()
    for user in users:
        items.append(
            {
                "type": "User",
                "title": user.full_name or user.username,
                "detail": f"{user.role.title()} - {user.id_number}",
                "url": url_for("add_user") if current_user().role == "admin" else url_for("profile"),
            }
        )
    attendance_query = Attendance.query.filter(
        (Attendance.name.ilike(like))
        | (Attendance.role.ilike(like))
        | (Attendance.id_number.ilike(like))
        | (Attendance.status.ilike(like))
    )
    if current_user().role != "admin":
        attendance_query = attendance_query.filter_by(user_id=current_user().id)
    for row in attendance_query.order_by(Attendance.date.desc(), Attendance.time.desc()).limit(6).all():
        items.append(
            {
                "type": "Attendance",
                "title": row.name,
                "detail": f"{row.id_number} - {row.date.isoformat()} - {row.status}",
                "url": url_for("attendance_view"),
            }
        )
    if current_user().role == "admin" and any(term.lower() in word for word in ["report", "reports", "csv", "pdf", "export"]):
        items.append({"type": "Report", "title": "Attendance Reports", "detail": "CSV, Excel, and PDF exports", "url": url_for("reports")})
    return jsonify({"items": items[:10]})


def report_dataframe() -> pd.DataFrame:
    rows = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()
    return pd.DataFrame(
        [
            {
                "ID Number": row.id_number or (row.user.id_number if row.user else row.user_id),
                "Name": row.name,
                "Role": row.role,
                "Date": row.date.isoformat(),
                "Time": row.time.strftime("%H:%M:%S"),
                "Status": row.status,
                "Recognition Accuracy": f"{row.recognition_accuracy}%",
                "Snapshot Image Path": row.snapshot_path,
            }
            for row in rows
        ]
    )


@app.get("/static/snapshots/<path:filename>")
@login_required
def uploaded_snapshot(filename: str):
    user = current_user()
    path = f"static/snapshots/{filename}"
    if user and user.role != "admin":
        allowed = Attendance.query.filter_by(user_id=user.id, snapshot_path=path).first()
        if not allowed:
            flash("You can only view your own attendance snapshots.", "warning")
            return redirect(url_for("user_dashboard"))
    return send_from_directory(SNAPSHOT_DIR, filename)


@app.get("/export/csv")
@admin_required
def export_csv():
    ensure_csv_header()
    write_log("CSV report exported", current_user().id if current_user() else None)
    db.session.commit()
    return send_file(ATTENDANCE_CSV, as_attachment=True, download_name=f"attendance-{date.today().isoformat()}.csv")


@app.get("/export/excel")
@admin_required
def export_excel():
    write_log("Excel report exported", current_user().id if current_user() else None)
    db.session.commit()
    frame = report_dataframe()
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Attendance")
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"attendance-{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/export/pdf")
@admin_required
def export_pdf():
    write_log("PDF report exported", current_user().id if current_user() else None)
    db.session.commit()
    rows = Attendance.query.order_by(Attendance.date.desc(), Attendance.time.desc()).limit(45).all()
    lines = ["AI Attendance Pro Report", f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for row in rows:
        identity = row.id_number or (row.user.id_number if row.user else row.user_id)
        lines.append(f"{identity}  {row.name}  {row.role}  {row.date}  {row.time.strftime('%H:%M:%S')}  {row.status}")
    text = "\\n".join(lines).replace("(", "[").replace(")", "]")
    stream = f"BT /F1 10 Tf 48 780 Td ({text[:3000]}) Tj ET"
    pdf = (
        "%PDF-1.4\n"
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        f"5 0 obj << /Length {len(stream)} >> stream\n{stream}\nendstream endobj\n"
        "xref\n0 6\n0000000000 65535 f \n"
        "trailer << /Root 1 0 R /Size 6 >>\nstartxref\n0\n%%EOF"
    ).encode("latin-1", errors="ignore")
    return send_file(
        io.BytesIO(pdf),
        as_attachment=True,
        download_name=f"attendance-report-{date.today().isoformat()}.pdf",
        mimetype="application/pdf",
    )


@app.errorhandler(404)
def not_found(_error):
    flash("That page could not be found.", "warning")
    return redirect(url_for("index"))


@app.errorhandler(413)
def oversized(_error):
    flash("Uploaded files must be smaller than 6 MB.", "warning")
    return redirect(request.referrer or url_for("profile"))


@app.teardown_appcontext
def shutdown_session(_exception=None):
    db.session.remove()


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1", host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
