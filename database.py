from __future__ import annotations

import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "users.db"
ATTENDANCE_CSV = BASE_DIR / "attendance.csv"
YEARLY_ATTENDANCE_DIR = BASE_DIR / "Attendance"

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="user")
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    avatar = db.Column(db.String(255), nullable=False, default="default_user.png")
    full_name = db.Column(db.String(160), nullable=False, default="")
    id_number = db.Column(db.String(80), unique=True, nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    edited_by = db.Column(db.Integer, nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)
    reset_otp_hash = db.Column(db.String(255), nullable=True)
    reset_otp_expires = db.Column(db.DateTime, nullable=True)

    attendance = db.relationship("Attendance", back_populates="user", cascade="all, delete-orphan")


class Attendance(db.Model):
    __tablename__ = "attendance"
    __table_args__ = (
        db.UniqueConstraint("user_id", "date", name="uq_attendance_user_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    id_number = db.Column(db.String(80), nullable=False, default="")
    role = db.Column(db.String(32), nullable=False, default="student")
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    time = db.Column(db.Time, nullable=False, default=lambda: datetime.now().time())
    status = db.Column(db.String(24), nullable=False, default="Present")
    recognition_accuracy = db.Column(db.Integer, nullable=False, default=0)
    snapshot_path = db.Column(db.String(255), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="attendance")


class SystemLog(db.Model):
    __tablename__ = "system_logs"

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(255), nullable=False)
    edited_by = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


def configure_database(app: Flask) -> None:
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH.as_posix()}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)


def ensure_csv_header() -> None:
    header = ["ID Number", "Name", "Role", "Date", "Time", "Status", "Recognition Accuracy", "Snapshot Image Path"]
    if not ATTENDANCE_CSV.exists():
        ATTENDANCE_CSV.write_text(",".join(header) + "\n", encoding="utf-8")
        return
    try:
        first_line = ATTENDANCE_CSV.read_text(encoding="utf-8").splitlines()[0]
    except IndexError:
        first_line = ""
    if first_line != ",".join(header):
        old_rows = list(csv.reader(ATTENDANCE_CSV.open(newline="", encoding="utf-8")))
        old_header = old_rows[0] if old_rows else []
        with ATTENDANCE_CSV.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for row in old_rows[1:]:
                if old_header[:6] == ["ID Number", "Name", "Role", "Date", "Time", "Status"] and len(row) >= 6:
                    writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], "", row[6] if len(row) > 6 else ""])
                elif len(row) >= 5:
                    writer.writerow([row[1], row[0], "", row[2], row[3], row[4], "", ""])


def migrate_schema() -> None:
    """Apply lightweight SQLite migrations for existing local installs."""
    if not DB_PATH.exists():
        return
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.cursor()
        user_columns = {row[1] for row in cursor.execute("PRAGMA table_info(users)")}
        attendance_columns = {row[1] for row in cursor.execute("PRAGMA table_info(attendance)")}
        log_columns = set()
        if cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_logs'").fetchone():
            log_columns = {row[1] for row in cursor.execute("PRAGMA table_info(system_logs)")}
        if "id_number" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN id_number VARCHAR(80) NOT NULL DEFAULT ''")
        if "updated_at" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN updated_at DATETIME")
        if "edited_by" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN edited_by INTEGER")
        if "edited_at" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN edited_at DATETIME")
        if "reset_otp_hash" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN reset_otp_hash VARCHAR(255)")
        if "reset_otp_expires" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN reset_otp_expires DATETIME")
        if "role" not in attendance_columns:
            cursor.execute("ALTER TABLE attendance ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'student'")
        if "id_number" not in attendance_columns:
            cursor.execute("ALTER TABLE attendance ADD COLUMN id_number VARCHAR(80) NOT NULL DEFAULT ''")
        if "recognition_accuracy" not in attendance_columns:
            cursor.execute("ALTER TABLE attendance ADD COLUMN recognition_accuracy INTEGER NOT NULL DEFAULT 0")
        if "snapshot_path" not in attendance_columns:
            cursor.execute("ALTER TABLE attendance ADD COLUMN snapshot_path VARCHAR(255) NOT NULL DEFAULT ''")
        if "created_at" not in attendance_columns:
            cursor.execute("ALTER TABLE attendance ADD COLUMN created_at DATETIME")
        if not log_columns:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS system_logs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "action VARCHAR(255) NOT NULL, "
                "edited_by INTEGER, "
                "timestamp DATETIME NOT NULL)"
            )
        connection.commit()
    finally:
        connection.close()


def init_database(app: Flask | None = None) -> Flask:
    flask_app = app or Flask(__name__)
    configure_database(flask_app)
    with flask_app.app_context():
        migrate_schema()
        db.create_all()
        ensure_csv_header()
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
        for user in User.query.filter((User.id_number == None) | (User.id_number == "")).all():
            user.id_number = f"USR-{user.id:04d}"
        for user in User.query.filter_by(role="user").all():
            user.role = "student"
        for user in User.query.filter(User.updated_at == None).all():
            user.updated_at = user.created_at or datetime.utcnow()
        for record in Attendance.query.filter(Attendance.created_at == None).all():
            record.created_at = datetime.combine(record.date, record.time)
        for record in Attendance.query.filter((Attendance.id_number == None) | (Attendance.id_number == "")).all():
            record.id_number = record.user.id_number if record.user else str(record.user_id)
        db.session.commit()
    return flask_app


if __name__ == "__main__":
    init_database()
    print("Database initialized. Default admin: admin / Admin@12345")
