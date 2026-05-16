from __future__ import annotations

from pathlib import Path

import cv2

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
CASCADE_PATH = BASE_DIR / "haarcascade_frontalface_default.xml"


def resolve_cascade() -> str:
    if CASCADE_PATH.exists():
        return str(CASCADE_PATH)
    return cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def save_face_sample_from_frame(user_id: int, frame, limit: int = 40) -> dict[str, object]:
    """Store one browser-uploaded face sample for training."""
    target_dir = DATASET_DIR / f"user_{user_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(target_dir.glob("*.jpg"))
    if len(existing) >= limit:
        return {
            "ok": True,
            "message": f"Face sample target already has {len(existing)} images.",
            "captured": len(existing),
            "complete": True,
        }

    cascade = cv2.CascadeClassifier(resolve_cascade())
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        return {"ok": False, "message": "No face detected. Keep the face centered and well lit.", "captured": len(existing), "complete": False}

    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    face = cv2.resize(gray[y : y + h, x : x + w], (220, 220), interpolation=cv2.INTER_AREA)
    next_index = len(existing) + 1
    cv2.imwrite(str(target_dir / f"{next_index:03d}.jpg"), face)
    captured = next_index
    return {
        "ok": True,
        "message": f"Captured {captured}/{limit} face samples.",
        "captured": captured,
        "complete": captured >= limit,
    }


def capture_faces(user_id: int, limit: int = 40) -> dict[str, object]:
    return {
        "ok": False,
        "message": "Server webcam capture has been replaced by browser-based capture from the Users page.",
        "captured": 0,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("user_id", type=int)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    print(capture_faces(args.user_id, args.limit)["message"])
