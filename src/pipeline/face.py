"""High-performance face detection (YuNet) and recognition (SFace) using OpenCV DNN.

Enables tagging target persons and prioritizing matching frames during extraction,
classification, and autonomous curation.
"""
from __future__ import annotations

import os
import threading
import urllib.request
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline.config import Settings, get_settings

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"

# OpenCV SFace standard cosine verification threshold: >0.363 indicates same identity
DEFAULT_FACE_MATCH_THRESHOLD = 0.363


def person_embedding(person: dict[str, Any] | None) -> np.ndarray | None:
    """Unit 128-dim target embedding of a stored person, or None for a missing or placeholder (all-zero) one.

    A person created by name only is stored with a zero vector until a face is enrolled; matching against it
    would score every frame 0 and silently disable the prioritisation.
    """
    if not person or not person.get("embedding"):
        return None
    vec = np.frombuffer(person["embedding"], dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if vec.size == 0 or norm < 1e-6:
        return None
    return (vec / norm).astype(np.float32)


class FaceEngine:
    """Thread-safe face detection & recognition engine powered by OpenCV YuNet and SFace."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.cache_face_dir = self.settings.cache_dir / "models" / "face"
        self.builtin_face_dir = Path("/app/models/face")
        models_env = os.getenv("CP_MODELS_DIR")
        self.env_face_dir = Path(models_env) / "face" if models_env else None

        self._detector: cv2.FaceDetectorYN | None = None
        self._recognizer: cv2.FaceRecognizerSF | None = None
        self._lock = threading.RLock()

    def _resolve_model_file(self, filename: str, download_url: str) -> Path:
        """Finds model in env dir, cache dir, or builtin dir; downloads to cache if missing."""
        search_dirs = [d for d in (self.env_face_dir, self.cache_face_dir, self.builtin_face_dir) if d is not None]
        for d in search_dirs:
            p = d / filename
            if p.is_file():
                return p

        # If not found, download to cache_face_dir
        self.cache_face_dir.mkdir(parents=True, exist_ok=True)
        dest = self.cache_face_dir / filename
        urllib.request.urlretrieve(download_url, dest)
        return dest

    def _ensure_models(self) -> tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
        with self._lock:
            if self._detector is None or self._recognizer is None:
                yunet_path = self._resolve_model_file("face_detection_yunet_2023mar.onnx", YUNET_URL)
                sface_path = self._resolve_model_file("face_recognition_sface_2021dec.onnx", SFACE_URL)

                self._detector = cv2.FaceDetectorYN.create(
                    str(yunet_path),
                    "",
                    (320, 320),
                    score_threshold=0.60,
                    nms_threshold=0.30,
                    top_k=5000,
                )
                self._recognizer = cv2.FaceRecognizerSF.create(str(sface_path), "")

            return self._detector, self._recognizer

    def detect_faces(
        self,
        img_bgr: np.ndarray,
        score_threshold: float = 0.60,
    ) -> list[dict[str, Any]]:
        """Detects faces in BGR image using YuNet. Returns bounding boxes and landmarks."""
        if img_bgr is None or img_bgr.size == 0:
            return []

        h, w = img_bgr.shape[:2]
        detector, _ = self._ensure_models()

        with self._lock:
            detector.setInputSize((w, h))
            detector.setScoreThreshold(score_threshold)
            _, faces = detector.detect(img_bgr)

        if faces is None or len(faces) == 0:
            return []

        out = []
        for f in faces:
            x, y, fw, fh = int(f[0]), int(f[1]), int(f[2]), int(f[3])
            # Clamp to frame boundary
            x = max(0, min(x, w - 1))
            y = max(0, min(y, h - 1))
            fw = max(1, min(fw, w - x))
            fh = max(1, min(fh, h - y))

            score = float(f[14])
            landmarks = [[float(f[4 + 2 * i]), float(f[5 + 2 * i])] for i in range(5)]
            out.append({
                "box": [x, y, fw, fh],
                "score": round(score, 3),
                "landmarks": landmarks,
                "raw": f,
            })

        return out

    def extract_feature(self, img_bgr: np.ndarray, raw_face: Any) -> np.ndarray:
        """Aligns, crops, and computes 128-dim SFace feature vector (normalized L2)."""
        if isinstance(raw_face, dict) and "raw" in raw_face:
            raw_face = raw_face["raw"]
        elif isinstance(raw_face, dict) and "box" in raw_face:
            # Synthetic or fallback dict without raw
            raw_face = np.array(raw_face["box"] + [0] * 10 + [raw_face.get("score", 1.0)], dtype=np.float32)

        _, recognizer = self._ensure_models()
        with self._lock:
            aligned = recognizer.alignCrop(img_bgr, raw_face)
            feat = recognizer.feature(aligned)

        vec = feat.flatten().astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 1e-6:
            vec /= norm
        return vec

    def extract_primary_face(
        self,
        img_bgr: np.ndarray,
        score_threshold: float = 0.60,
    ) -> tuple[np.ndarray | None, dict[str, Any] | None]:
        """Finds the most prominent face in the frame and extracts its 128-dim embedding."""
        faces = self.detect_faces(img_bgr, score_threshold=score_threshold)
        if not faces:
            return None, None

        # Sort by bounding box area * confidence score descending
        faces.sort(key=lambda f: f["box"][2] * f["box"][3] * f["score"], reverse=True)
        primary = faces[0]
        feat = self.extract_feature(img_bgr, primary["raw"])
        primary_info = {
            "box": primary["box"],
            "score": primary["score"],
            "landmarks": primary["landmarks"],
        }
        return feat, primary_info

    @staticmethod
    def compute_similarity(feat1: np.ndarray, feat2: np.ndarray) -> float:
        """Cosine similarity between two 128-dim face embeddings."""
        if feat1 is None or feat2 is None:
            return 0.0
        v1 = feat1.flatten()
        v2 = feat2.flatten()
        dot = float(np.dot(v1, v2))
        n1 = max(1e-6, float(np.linalg.norm(v1)))
        n2 = max(1e-6, float(np.linalg.norm(v2)))
        return max(-1.0, min(1.0, dot / (n1 * n2)))

    def match_frame(
        self,
        img_bgr: np.ndarray,
        target_features: list[np.ndarray] | np.ndarray,
        threshold: float = DEFAULT_FACE_MATCH_THRESHOLD,
    ) -> dict[str, Any]:
        """Detects faces in frame and compares against one or more target person embeddings.

        Returns match status, max similarity, and face bounding boxes with per-face scores.
        """
        faces = self.detect_faces(img_bgr)
        if not faces:
            return {
                "matched": False,
                "max_sim": 0.0,
                "target_person_sim": 0.0,
                "face_count": 0,
                "faces": [],
            }

        targets: list[np.ndarray] = []
        if isinstance(target_features, np.ndarray):
            targets = [target_features]
        elif isinstance(target_features, list):
            targets = [t for t in target_features if t is not None]

        if not targets:
            # No targets provided: return detected faces without similarity
            return {
                "matched": False,
                "max_sim": 0.0,
                "target_person_sim": 0.0,
                "face_count": len(faces),
                "faces": [{
                    "box": f["box"],
                    "score": f["score"],
                    "sim": 0.0,
                    "matched": False,
                } for f in faces],
            }

        best_sim = -1.0
        face_results = []

        for f in faces:
            raw_target = f.get("raw") if isinstance(f, dict) else f
            feat = self.extract_feature(img_bgr, raw_target)
            # Match against all target exemplars and take max
            f_sim = max(self.compute_similarity(feat, t) for t in targets)
            best_sim = max(best_sim, f_sim)
            face_box = f["box"] if isinstance(f, dict) else [int(f[0]), int(f[1]), int(f[2]), int(f[3])]
            face_score = f["score"] if isinstance(f, dict) else float(f[14]) if len(f) > 14 else 1.0
            face_results.append({
                "box": face_box,
                "score": face_score,
                "sim": round(f_sim, 4),
                "matched": bool(f_sim >= threshold),
            })

        best_sim_val = max(0.0, best_sim)
        return {
            "matched": bool(best_sim_val >= threshold),
            "max_sim": round(best_sim_val, 4),
            "target_person_sim": round(best_sim_val, 4),
            "face_count": len(faces),
            "faces": face_results,
        }

    @staticmethod
    def crop_face(img_bgr: np.ndarray, box_or_face: Any, margin: float = 0.30) -> np.ndarray:
        """Crops a square region around face bounding box with margin for avatar/preview."""
        if isinstance(box_or_face, dict):
            box = box_or_face.get("box", [0, 0, img_bgr.shape[1], img_bgr.shape[0]])
        elif isinstance(box_or_face, (list, tuple, np.ndarray)):
            box = box_or_face[:4]
        else:
            box = [0, 0, img_bgr.shape[1], img_bgr.shape[0]]

        h, w = img_bgr.shape[:2]
        bx, by, bw, bh = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        cx = bx + bw / 2.0
        cy = by + bh / 2.0
        size = max(bw, bh) * (1.0 + margin)

        x0 = int(round(max(0, cx - size / 2.0)))
        y0 = int(round(max(0, cy - size / 2.0)))
        x1 = int(round(min(w, cx + size / 2.0)))
        y1 = int(round(min(h, cy + size / 2.0)))

        crop = img_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            return img_bgr
        return crop
