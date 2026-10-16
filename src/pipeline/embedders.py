"""Optional selection embedder: DINOv2-S ONNX on letterboxed frames (Phase F2, review §6.2).

`onnx-community/dinov2-small` (`onnx/model_quantized.onnx`, ~24 MB, downloaded on first use from Hugging Face into
`<cache>/models` like the CLIP model, or found offline under `CP_MODELS_DIR` / `/app/models`). Input: the whole
frame letterboxed (padded with the ImageNet mean, never cropped) to 224x224, ImageNet normalisation; output: the
CLS token of `last_hidden_state` (384-d), L2-normalised. DINOv2 is strong on composition, pose and near
duplicates, where CLIP-B/32 centre crops spread poorly. Any load failure makes `available()` false and callers
fall back to CLIP.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DINO_REPO = "onnx-community/dinov2-small"
DINO_FILE = "onnx/model_quantized.onnx"
DINO_MODEL_ID = f"{DINO_REPO}:quantized"
DINO_DIM = 384
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def letterbox_preprocess(img_bgr: np.ndarray, size: int = 224) -> np.ndarray:
    """Fit the whole frame into size x size (aspect kept, mean-colour padding) -> 3 x size x size float32."""
    h, w = img_bgr.shape[:2]
    s = size / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    canvas = np.empty((size, size, 3), dtype=np.float32)
    canvas[:] = IMAGENET_MEAN  # normalises to 0
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = resized[:, :, ::-1].astype(np.float32) / 255.0
    return ((canvas - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)


class Dinov2Embedder:
    model_id = DINO_MODEL_ID
    dim = DINO_DIM

    def __init__(self, settings: Any):
        self.settings = settings
        self.models_dir = Path(os.getenv("CP_MODELS_DIR", Path(settings.cache_dir) / "models"))
        self.builtin_models_dir = Path("/app/models")
        self._sess = None
        self._lock = threading.RLock()
        self.error: str | None = None

    def _resolve(self) -> str:
        from huggingface_hub import hf_hub_download

        for d in (self.models_dir, self.builtin_models_dir):
            if d.is_dir():
                try:
                    return hf_hub_download(DINO_REPO, DINO_FILE, cache_dir=str(d), local_files_only=True)
                except Exception:
                    pass
        self.models_dir.mkdir(parents=True, exist_ok=True)
        return hf_hub_download(DINO_REPO, DINO_FILE, cache_dir=str(self.models_dir))

    def _ensure(self):
        with self._lock:
            if self._sess is None and self.error is None:
                try:
                    import onnxruntime as ort

                    opts = ort.SessionOptions()
                    opts.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
                    self._sess = ort.InferenceSession(self._resolve(), opts, providers=["CPUExecutionProvider"])
                except Exception as exc:  # offline / missing model -> CLIP fallback
                    self.error = f"DINOv2 unavailable: {exc}"
            return self._sess

    def available(self) -> bool:
        return self._ensure() is not None

    def embed_images(self, frames_bgr: list[np.ndarray], batch_size: int = 16) -> np.ndarray:
        if not frames_bgr:
            return np.zeros((0, self.dim), dtype=np.float32)
        sess = self._ensure()
        if sess is None:
            raise RuntimeError(self.error or "DINOv2 unavailable")
        name = sess.get_inputs()[0].name
        out: list[np.ndarray] = []
        for i in range(0, len(frames_bgr), batch_size):
            batch = np.stack([letterbox_preprocess(f) for f in frames_bgr[i:i + batch_size]]).astype(np.float32)
            with self._lock:
                hidden = sess.run(None, {name: batch})[0]
            cls = hidden[:, 0, :].astype(np.float32)
            norms = np.linalg.norm(cls, axis=1, keepdims=True)
            out.append(cls / np.where(norms < 1e-12, 1.0, norms))
        return np.concatenate(out, axis=0)


def selection_embeddings_for_rows(
    rows: list[dict[str, Any]],
    *,
    embedder: Any,
    cache: Any,
    crop: str,
) -> tuple[np.ndarray | None, str | None]:
    """DINOv2 vectors aligned with reselect's frame rows: from the analysis cache, the missing ones computed from
    the frames' stored previews (and cached). Returns (matrix or None, fallback reason or None)."""
    if embedder is None:
        return None, "no DINOv2 embedder"
    ts = [round(float(r["t"]), 3) for r in rows]
    found = cache.get_embeddings("dinov2", embedder.model_id, crop, ts) if cache is not None else {}
    missing = [j for j in range(len(rows)) if j not in found]
    if missing:
        if not embedder.available():
            return None, embedder.error or "DINOv2 unavailable"
        imgs: list[np.ndarray] = []
        for j in missing:
            r = rows[j]
            img = None
            for key in ("preview_path", "full_path"):
                p = r.get(key)
                if p and Path(p).is_file():
                    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                    if img is not None:
                        break
            if img is None:
                return None, f"frame {r.get('id')} has no stored image for DINOv2"
            imgs.append(img)
        vecs = embedder.embed_images(imgs)
        for j, v in zip(missing, vecs):
            found[j] = v
        if cache is not None:
            cache.put_embeddings("dinov2", embedder.model_id, crop, [ts[j] for j in missing], vecs)
            cache.save()
    return np.stack([np.asarray(found[j], dtype=np.float64) for j in range(len(rows))]), None
