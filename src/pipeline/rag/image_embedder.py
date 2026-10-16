"""R3 image embedders for "more like this frame" (``GET /api/rag/similar``).

SigLIP (``Xenova/siglip-base-patch16-224``, quantized ONNX vision tower, 768-d) is preferred: it is a stronger
image model than CLIP ViT-B/32. It is fetched with ``huggingface_hub`` like the CLIP model in
``pipeline/classifier.py`` (local model dirs first, then a download). When it cannot be obtained (offline,
no disk, broken file) the existing CLIP vision model (``ClipClassifier.embed_images``, 512-d) is used instead.

The index records which embedder built it (``rag_image_embeddings.npz``: ``embedder``, ``model``); a query
must be embedded by the same one (``AutoImageEmbedder.for_name``), vectors of different models never mix.
``CP_RAG_IMAGE_EMBEDDER=clip`` skips SigLIP entirely, ``=off`` disables image embeddings.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from pipeline.config import Settings, get_settings

logger = logging.getLogger(__name__)

SIGLIP_REPO = "Xenova/siglip-base-patch16-224"
SIGLIP_VISION_FILE = "onnx/vision_model_quantized.onnx"
SIGLIP_SIZE = 224
EMBEDDER_NAMES = ("siglip", "clip")


class ImageEmbedder(Protocol):
    name: str  # 'siglip' | 'clip'

    @property
    def model_id(self) -> str: ...

    def embed_images(self, frames_bgr: list[np.ndarray]) -> np.ndarray: ...


def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms[norms == 0] = 1e-12
    return vectors / norms


def siglip_preprocess_bgr(img_bgr: np.ndarray, size: int = SIGLIP_SIZE) -> np.ndarray:
    """SigLIP image processor: bicubic resize to size x size (no crop), RGB, /255, mean 0.5 / std 0.5 (3xHxW)."""
    resized = cv2.resize(img_bgr, (size, size), interpolation=cv2.INTER_CUBIC)
    rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
    return ((rgb - 0.5) / 0.5).transpose(2, 0, 1)


def load_image_bgr(path: Path) -> np.ndarray | None:
    """BGR image of a file, None when it is missing or cannot be decoded."""
    if not path.is_file():
        return None
    try:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    except cv2.error:
        return None
    return img if img is not None and img.size else None


class SiglipImageEmbedder:
    """SigLIP vision tower (ONNX Runtime, CPU); ``load`` raises when the model cannot be obtained."""

    name = "siglip"

    def __init__(self, settings: Settings | None = None, repo: str = SIGLIP_REPO, filename: str = SIGLIP_VISION_FILE,
                 allow_download: bool = True):
        self.settings = settings or get_settings()
        self.repo = repo
        self.filename = filename
        self.allow_download = allow_download
        self.models_dir = Path(os.getenv("CP_MODELS_DIR", self.settings.cache_dir / "models"))
        self.builtin_models_dir = Path("/app/models")
        self._sess: Any = None
        self._lock = threading.Lock()

    @property
    def model_id(self) -> str:
        return f"{self.repo}:{self.filename}"

    def _resolve_model_path(self) -> str:
        from huggingface_hub import hf_hub_download

        for m_dir in (self.models_dir, self.builtin_models_dir):
            if m_dir.is_dir():
                try:
                    return hf_hub_download(self.repo, self.filename, cache_dir=str(m_dir), local_files_only=True)
                except Exception:
                    pass
        if not self.allow_download:
            raise FileNotFoundError(f"{self.repo}/{self.filename} not in the local model cache")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        return hf_hub_download(self.repo, self.filename, cache_dir=str(self.models_dir))

    def load(self) -> None:
        with self._lock:
            if self._sess is None:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = min(8, os.cpu_count() or 4)
                self._sess = ort.InferenceSession(self._resolve_model_path(), opts, providers=["CPUExecutionProvider"])

    def embed_images(self, frames_bgr: list[np.ndarray], batch_size: int = 8) -> np.ndarray:
        if not frames_bgr:
            return np.zeros((0, 768), dtype=np.float32)
        self.load()
        inputs = {i.name for i in self._sess.get_inputs()}
        inp_name = "pixel_values" if "pixel_values" in inputs else next(iter(inputs))
        out_names = [o.name for o in self._sess.get_outputs()]
        # The vision tower's pooled (attention-pooling head) output is SigLIP's image embedding
        out_name = next((n for n in ("image_embeds", "pooler_output") if n in out_names), out_names[-1])
        parts: list[np.ndarray] = []
        for i in range(0, len(frames_bgr), batch_size):
            batch = np.stack([siglip_preprocess_bgr(f) for f in frames_bgr[i:i + batch_size]]).astype(np.float32)
            with self._lock:
                out = self._sess.run([out_name], {inp_name: batch})[0]
            parts.append(_normalize(out))
        return np.concatenate(parts, axis=0)


class ClipImageEmbedder:
    """The CLIP vision model already used for scoring (``ClipClassifier.embed_images``, centre crop, 512-d)."""

    name = "clip"

    def __init__(self, classifier: Any):
        self.classifier = classifier

    @property
    def model_id(self) -> str:
        return str(getattr(self.classifier, "repo", "clip"))

    def load(self) -> None:
        loader = getattr(self.classifier, "_ensure_vision_model", None)
        if callable(loader):
            loader()

    def embed_images(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        if not frames_bgr:
            return np.zeros((0, 512), dtype=np.float32)
        return _normalize(self.classifier.embed_images(frames_bgr))


class AutoImageEmbedder:
    """SigLIP when obtainable, else CLIP; resolved once on first use (thread-safe)."""

    def __init__(self, settings: Settings | None, classifier: Any, prefer: str | None = None):
        self.settings = settings or get_settings()
        self.prefer = (prefer or os.getenv("CP_RAG_IMAGE_EMBEDDER") or "siglip").strip().lower()
        self.clip = ClipImageEmbedder(classifier)
        self.siglip = SiglipImageEmbedder(self.settings)
        self._resolved: Any = None
        self._lock = threading.Lock()
        self.fallback_reason: str | None = None

    @property
    def name(self) -> str:
        """The embedder in use, or the one that will be tried first before the first use."""
        if self._resolved is not None:
            return self._resolved.name
        return "clip" if self.prefer == "clip" else "siglip"

    @property
    def model_id(self) -> str:
        return self.resolve().model_id

    def resolve(self) -> Any:
        with self._lock:
            if self._resolved is None:
                if self.prefer != "clip":
                    try:
                        self.siglip.load()
                        self._resolved = self.siglip
                    except Exception as err:  # offline / missing file: the CLIP vision model is always there
                        self.fallback_reason = f"{type(err).__name__}: {err}"
                        logger.warning("SigLIP unavailable, RAG image index uses CLIP instead: %s", err)
                if self._resolved is None:
                    self._resolved = self.clip
            return self._resolved

    def for_name(self, name: str) -> Any:
        """The embedder that matches an existing index (raises when it cannot be loaded)."""
        if name == "clip":
            return self.clip
        if name == "siglip":
            self.siglip.load()
            return self.siglip
        raise ValueError(f"Unknown image embedder {name!r}")

    def embed_images(self, frames_bgr: list[np.ndarray]) -> np.ndarray:
        return self.resolve().embed_images(frames_bgr)


def default_image_embedder(settings: Settings | None, classifier: Any) -> Any | None:
    """Auto (SigLIP, else CLIP) for the real CLIP classifier; the classifier's own ``embed_images`` for
    stand-ins (tests); None when image embeddings are off (``CP_RAG_IMAGE_EMBEDDER=off``) or impossible."""
    if (os.getenv("CP_RAG_IMAGE_EMBEDDER") or "").strip().lower() == "off":
        return None
    from pipeline.classifier import ClipClassifier

    if isinstance(classifier, ClipClassifier):
        return AutoImageEmbedder(settings, classifier)
    if classifier is not None and callable(getattr(classifier, "embed_images", None)):
        return ClipImageEmbedder(classifier)
    return None


def embedder_for_index(embedder: Any, index_embedder: str) -> Any:
    """The embedder to embed a query for an index built by ``index_embedder`` ('siglip' | 'clip')."""
    if isinstance(embedder, AutoImageEmbedder):
        return embedder.for_name(index_embedder)
    if getattr(embedder, "name", None) != index_embedder:
        raise RuntimeError(f"image index was built with {index_embedder}, the configured embedder is "
                           f"{getattr(embedder, 'name', None)}; rebuild the RAG index")
    return embedder
