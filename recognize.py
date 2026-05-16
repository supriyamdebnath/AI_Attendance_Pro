from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

import cv2

from database import ATTENDANCE_CSV, Attendance, SystemLog, User, db

BASE_DIR = Path(__file__).resolve().parent
CASCADE_PATH = BASE_DIR / "haarcascade_frontalface_default.xml"
TRAINER_PATH = BASE_DIR / "trainer" / "trainer.yml"
YEARLY_ATTENDANCE_DIR = BASE_DIR / "Attendance"
SNAPSHOT_DIR = BASE_DIR / "static" / "snapshots"
attendance_lock = Lock()
recognition_lock = Lock()
_cascade = None
_recognizer = None
_recognizer_mtime = None
RECOGNITION_THRESHOLD = 85
_last_unknown_log_at: datetime | None = None
last_event: dict[str, Any] = {
    "state": "idle",
    "message": "Camera idle",
    "camera": "Camera OFF",
    "fps": 0,
}


def resolve_cascade() -> str:
    if CASCADE_PATH.exists():
        return str(CASCADE_PATH)
    return cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def build_recognizer():
    global _recognizer, _recognizer_mtime
    recognizer_factory = getattr(cv2, "face", None)
    if recognizer_factory is None or not hasattr(recognizer_factory, "LBPHFaceRecognizer_create"):
        return None
    if not TRAINER_PATH.exists():
        return None
    mtime = TRAINER_PATH.stat().st_mtime
    if _recognizer is not None and _recognizer_mtime == mtime:
        return _recognizer
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    try:
        recognizer.read(str(TRAINER_PATH))
    except cv2.error:
        return None
    _recognizer = recognizer
    _recognizer_mtime = mtime
    return recognizer


def build_cascade():
    global _cascade
    if _cascade is None:
        _cascade = cv2.CascadeClassifier(resolve_cascade())
    return _cascade


def public_snapshot_path(path: Path | None) -> str:
    if not path:
        return ""
    return path.relative_to(BASE_DIR).as_posix()


def save_snapshot(frame, user: User | None, face_box: tuple[int, int, int, int] | None = None) -> str:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    identity = user.id_number if user and user.id_number else f"user-{user.id}" if user else "unknown"
    filename = f"{identity}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.jpg"
    path = SNAPSHOT_DIR / filename
    image = frame
    if face_box:
        x, y, w, h = face_box
        pad = 42
        y1, y2 = max(y - pad, 0), min(y + h + pad, frame.shape[0])
        x1, x2 = max(x - pad, 0), min(x + w + pad, frame.shape[1])
        image = frame[y1:y2, x1:x2]
    cv2.imwrite(str(path), image)
    return public_snapshot_path(path)


def normalize_face(face_gray):
    face = cv2.resize(face_gray, (220, 220), interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    face = clahe.apply(face)
    return cv2.GaussianBlur(face, (3, 3), 0)


def recognition_accuracy(raw_confidence: float) -> int:
    # LBPH confidence is a distance where lower is better. This maps useful
    # distances into a user-facing accuracy score without calling it confidence.
    return max(0, min(100, int(100 - (raw_confidence * 0.72))))


def mark_attendance(user: User, snapshot_path: str = "", recognition_accuracy: int = 0) -> tuple[bool, Attendance | None]:
    today = date.today()
    with attendance_lock:
        existing = Attendance.query.filter_by(user_id=user.id, date=today).first()
        if existing:
            if snapshot_path and not existing.snapshot_path:
                existing.snapshot_path = snapshot_path
            if recognition_accuracy and not existing.recognition_accuracy:
                existing.recognition_accuracy = recognition_accuracy
            db.session.commit()
            return False, existing
        now = datetime.now()
        record = Attendance(
            user_id=user.id,
            name=user.full_name or user.username,
            id_number=user.id_number,
            role=user.role,
            date=today,
            time=now.time(),
            status="Present",
            recognition_accuracy=recognition_accuracy,
            snapshot_path=snapshot_path,
        )
        db.session.add(record)
        db.session.add(SystemLog(action=f"Attendance marked for {record.name}", edited_by=user.id))
        db.session.commit()
        with ATTENDANCE_CSV.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                [
                    user.id_number,
                    record.name,
                    record.role,
                    record.date.isoformat(),
                    record.time.strftime("%H:%M:%S"),
                    record.status,
                    f"{record.recognition_accuracy}%",
                    record.snapshot_path,
                ]
            )
        YEARLY_ATTENDANCE_DIR.mkdir(parents=True, exist_ok=True)
        yearly_csv = YEARLY_ATTENDANCE_DIR / f"attendance_{record.date.year}.csv"
        if not yearly_csv.exists():
            yearly_csv.write_text("ID Number,Name,Role,Date,Time,Status,Recognition Accuracy,Snapshot Image Path\n", encoding="utf-8")
        with yearly_csv.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                [
                    user.id_number,
                    record.name,
                    record.role,
                    record.date.isoformat(),
                    record.time.strftime("%H:%M:%S"),
                    record.status,
                    f"{record.recognition_accuracy}%",
                    record.snapshot_path,
                ]
            )
        return True, record


def attendance_event(record: Attendance, user: User, confidence_pct: int, created: bool) -> dict[str, Any]:
    return {
        "state": "recognized",
        "message": "Attendance Marked Successfully" if created else "Attendance already marked today",
        "name": record.name,
        "role": record.role,
        "id_number": user.id_number,
        "date": record.date.isoformat(),
        "time": record.time.strftime("%H:%M:%S"),
        "status": record.status,
        "recognition_accuracy": record.recognition_accuracy or confidence_pct,
        "snapshot_path": record.snapshot_path,
        "camera": "Camera ON",
    }


def annotate_frame(frame, app_context, fps: float = 0) -> tuple[object, str, dict[str, Any]]:
    global _last_unknown_log_at
    with recognition_lock:
        cascade = build_cascade()
        recognizer = build_recognizer()
    _display_height, display_width = frame.shape[:2]
    scale = 0.55 if display_width > 900 else 0.75
    small = cv2.resize(frame, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(48, 48))
    status = "No face detected"
    event = {"state": "scanning", "message": status, "camera": "Camera ON", "fps": round(fps, 1)}
    for sx, sy, sw, sh in faces:
        x, y, w, h = [int(value / scale) for value in (sx, sy, sw, sh)]
        x, y = max(x, 0), max(y, 0)
        w, h = min(w, frame.shape[1] - x), min(h, frame.shape[0] - y)
        label = "Unknown"
        color = (60, 90, 245)
        confidence_pct = 0
        if recognizer is not None:
            face_gray = normalize_face(cv2.cvtColor(frame[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY))
            predicted_id, confidence = recognizer.predict(face_gray)
            confidence_pct = recognition_accuracy(confidence)
            if confidence < RECOGNITION_THRESHOLD:
                with app_context():
                    user = db.session.get(User, int(predicted_id))
                    if user:
                        label = user.full_name or user.username
                        existing = Attendance.query.filter_by(user_id=user.id, date=date.today()).first()
                        snapshot_path = existing.snapshot_path if existing and existing.snapshot_path else save_snapshot(frame, user, (x, y, w, h))
                        created, record = mark_attendance(user, snapshot_path, confidence_pct)
                        color = (30, 196, 120)
                        status = f"Recognized {label} ({confidence_pct}%)"
                        if record:
                            event = attendance_event(record, user, confidence_pct, created)
                            event["fps"] = round(fps, 1)
        if label == "Unknown":
            now = datetime.now()
            if _last_unknown_log_at is None or (now - _last_unknown_log_at).total_seconds() > 30:
                with app_context():
                    db.session.add(SystemLog(action="Recognition failed: unknown user", edited_by=None))
                    db.session.commit()
                _last_unknown_log_at = now
            event = {
                "state": "unknown",
                "message": "Unknown User",
                "name": "Unknown User",
                "role": "Unmatched",
                "id_number": "-",
                "date": date.today().isoformat(),
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "Not marked",
                "recognition_accuracy": confidence_pct,
                "snapshot_path": "",
                "camera": "Camera ON",
                "fps": round(fps, 1),
            }
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        cv2.putText(frame, f"{label} {confidence_pct}%", (x, max(28, y - 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
    last_event.clear()
    last_event.update(event)
    return frame, status, event
