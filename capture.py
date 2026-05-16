from __future__ import annotations

import os
from pathlib import Path

import cv2

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
CASCADE_PATH = BASE_DIR / "haarcascade_frontalface_default.xml"


def resolve_cascade() -> str:
    if CASCADE_PATH.exists():
        return str(CASCADE_PATH)
    return cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def capture_faces(user_id: int, limit: int = 40) -> dict[str, object]:
    target_dir = DATASET_DIR / f"user_{user_id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    cascade = cv2.CascadeClassifier(resolve_cascade())
    # Hosted Render containers do not provide a physical webcam. This keeps
    # local Windows capture optimized and lets Linux hosts fail cleanly.
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    camera = cv2.VideoCapture(0, backend)
    if not camera.isOpened():
        return {"ok": False, "message": "Webcam unavailable."}

    captured = 0
    try:
        while captured < limit:
            ok, frame = camera.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80, 80))
            for x, y, w, h in faces[:1]:
                face = cv2.resize(gray[y : y + h, x : x + w], (220, 220))
                captured += 1
                cv2.imwrite(str(target_dir / f"{captured:03d}.jpg"), face)
            if captured >= limit:
                break
    finally:
        camera.release()
    return {"ok": captured > 0, "message": f"Captured {captured} face samples.", "captured": captured}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("user_id", type=int)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    print(capture_faces(args.user_id, args.limit)["message"])
