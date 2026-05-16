from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATASET_DIR = BASE_DIR / "dataset"
TRAINER_DIR = BASE_DIR / "trainer"
TRAINER_PATH = TRAINER_DIR / "trainer.yml"


def train_model() -> dict[str, object]:
    recognizer_factory = getattr(cv2, "face", None)
    if recognizer_factory is None or not hasattr(recognizer_factory, "LBPHFaceRecognizer_create"):
        return {"ok": False, "message": "opencv-contrib-python is required for LBPH training."}

    faces: list[np.ndarray] = []
    ids: list[int] = []
    for user_dir in DATASET_DIR.glob("user_*"):
        try:
            user_id = int(user_dir.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        for image_path in user_dir.glob("*.jpg"):
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is not None:
                faces.append(image)
                ids.append(user_id)

    if not faces:
        return {"ok": False, "message": "No captured face images found in dataset/user_* directories."}

    TRAINER_DIR.mkdir(parents=True, exist_ok=True)
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces, np.array(ids))
    recognizer.write(str(TRAINER_PATH))
    return {"ok": True, "message": f"Model trained with {len(faces)} face samples.", "samples": len(faces)}


if __name__ == "__main__":
    result = train_model()
    print(result["message"])
