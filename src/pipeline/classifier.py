"""Tier-2 multimodal classification & embedding using ONNX Runtime (CPU) with Xenova/clip-vit-base-patch32."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from pipeline.config import Settings, get_settings

CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def clip_preprocess_bgr(img_bgr: np.ndarray, size: int = 224) -> np.ndarray:
    """Resize, center-crop, and normalize BGR image for CLIP (returns 3x224x224 float32)."""
    h, w = img_bgr.shape[:2]
    s = size / min(h, w)
    resized = cv2.resize(img_bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_CUBIC)
    rh, rw = resized.shape[:2]
    y0, x0 = (rh - size) // 2, (rw - size) // 2
    crop = resized[y0:y0 + size, x0:x0 + size, ::-1].astype(np.float32) / 255.0
    crop = (crop - CLIP_MEAN) / CLIP_STD
    return crop.transpose(2, 0, 1)


class ClipClassifier:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.models_dir = Path(os.getenv("CP_MODELS_DIR", self.settings.cache_dir / "models"))
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.builtin_models_dir = Path("/app/models")
        self.repo = "Xenova/clip-vit-base-patch32"

        # Session options
        self.opts = ort.SessionOptions()
        self.opts.intra_op_num_threads = min(8, os.cpu_count() or 4)

        self._vision_sess: ort.InferenceSession | None = None
        self._text_sess: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None
        self._cached_prompt_embeddings: dict[str, np.ndarray] = {}
        self._lock = threading.RLock()

    def _resolve_model_path(self, filename: str) -> str:
        for m_dir in (self.models_dir, self.builtin_models_dir):
            if m_dir.is_dir():
                try:
                    return hf_hub_download(self.repo, filename, cache_dir=str(m_dir), local_files_only=True)
                except Exception:
                    pass
        return hf_hub_download(self.repo, filename, cache_dir=str(self.models_dir))

    def _ensure_vision_model(self) -> ort.InferenceSession:
        with self._lock:
            if self._vision_sess is None:
                v_path = self._resolve_model_path("onnx/vision_model_quantized.onnx")
                self._vision_sess = ort.InferenceSession(v_path, self.opts, providers=["CPUExecutionProvider"])
            return self._vision_sess

    def _ensure_text_model(self) -> tuple[ort.InferenceSession, Tokenizer]:
        with self._lock:
            if self._text_sess is None or self._tokenizer is None:
                t_path = self._resolve_model_path("onnx/text_model_quantized.onnx")
                tok_path = self._resolve_model_path("tokenizer.json")
                self._text_sess = ort.InferenceSession(t_path, self.opts, providers=["CPUExecutionProvider"])
                self._tokenizer = Tokenizer.from_file(tok_path)
                # Enable padding to 77 tokens (CLIP standard)
                self._tokenizer.enable_padding(length=77, pad_id=0, pad_token="<|endoftext|>")
                self._tokenizer.enable_truncation(max_length=77)
            return self._text_sess, self._tokenizer

    def embed_images(self, frames_bgr: list[np.ndarray], batch_size: int = 16) -> np.ndarray:
        """Compute normalized 512-dim visual embeddings for a list of BGR images."""
        if not frames_bgr:
            return np.zeros((0, 512), dtype=np.float32)

        sess = self._ensure_vision_model()
        inp_name = sess.get_inputs()[0].name
        embeddings: list[np.ndarray] = []

        for i in range(0, len(frames_bgr), batch_size):
            chunk = frames_bgr[i:i + batch_size]
            batch_tensor = np.stack([clip_preprocess_bgr(f) for f in chunk]).astype(np.float32)
            out = sess.run(None, {inp_name: batch_tensor})[0]
            # Normalize vectors
            norms = np.linalg.norm(out, axis=-1, keepdims=True)
            norms[norms == 0] = 1e-12
            embeddings.append(out / norms)

        return np.concatenate(embeddings, axis=0)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Compute normalized 512-dim embeddings for a list of text prompts."""
        if not texts:
            return np.zeros((0, 512), dtype=np.float32)

        sess, tok = self._ensure_text_model()
        encoded = tok.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)

        inp_name = sess.get_inputs()[0].name
        out = sess.run(None, {inp_name: input_ids})[0]
        # Normalize vectors
        norms = np.linalg.norm(out, axis=-1, keepdims=True)
        norms[norms == 0] = 1e-12
        return out / norms

    def classify_and_score(
        self,
        img_embeddings: np.ndarray,
        prompts: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
        """Compute independent zero-shot probabilities for person, equipment, negative per image."""
        if len(img_embeddings) == 0:
            return []

        # Gather prompt categories with sensible defaults
        person_prompts = prompts.get("person", [
            "a photo of a person", "a woman", "a photo of a girl", "a portrait of a woman", "a human"
        ])
        equip_prompts = prompts.get("equipment", [
            "ropes, restraints, bondage equipment, cuffs, harness, apparatus"
        ])
        neg_prompts = prompts.get("negative", [
            "empty room without people", "nobody", "scenery without people", "blurry empty background"
        ])
        equip_neg_prompts = prompts.get("equip_negative", [
            "no ropes, no restraints, no equipment, plain scene without props"
        ])

        cache_key = str((person_prompts, equip_prompts, neg_prompts, equip_neg_prompts))
        with self._lock:
            if cache_key not in self._cached_prompt_embeddings:
                p_pos = self.embed_texts(person_prompts)
                p_neg = self.embed_texts(neg_prompts)
                e_pos = self.embed_texts(equip_prompts)
                e_neg = self.embed_texts(equip_neg_prompts)

                p_pos_vec = np.mean(p_pos, axis=0); p_pos_vec /= max(1e-6, np.linalg.norm(p_pos_vec))
                p_neg_vec = np.mean(p_neg, axis=0); p_neg_vec /= max(1e-6, np.linalg.norm(p_neg_vec))
                e_pos_vec = np.mean(e_pos, axis=0); e_pos_vec /= max(1e-6, np.linalg.norm(e_pos_vec))
                e_neg_vec = np.mean(e_neg, axis=0); e_neg_vec /= max(1e-6, np.linalg.norm(e_neg_vec))

                self._cached_prompt_embeddings[cache_key] = {
                    "p_probe": np.vstack([p_pos_vec, p_neg_vec]),
                    "e_probe": np.vstack([e_pos_vec, e_neg_vec]),
                }
            cached = self._cached_prompt_embeddings[cache_key]

        p_probe = cached["p_probe"]  # (2, 512)
        e_probe = cached["e_probe"]  # (2, 512)

        # Independent cosine difference with sigmoid scaling
        p_sims = np.dot(img_embeddings, p_probe.T)  # (N, 2)
        p_diff = p_sims[:, 0] - p_sims[:, 1]
        p_scores = 1.0 / (1.0 + np.exp(-15.0 * p_diff))

        e_sims = np.dot(img_embeddings, e_probe.T)  # (N, 2)
        e_diff = e_sims[:, 0] - e_sims[:, 1]
        e_scores = 1.0 / (1.0 + np.exp(-15.0 * e_diff))

        results: list[dict[str, Any]] = []
        for i in range(len(img_embeddings)):
            person_score = float(p_scores[i])
            equip_score = float(e_scores[i])
            neg_score = float(max(0.0, 1.0 - max(person_score, equip_score)))

            results.append({
                "person": round(person_score, 3),
                "equipment": round(equip_score, 3),
                "negative": round(neg_score, 3),
                "best_label": "person" if person_score >= equip_score else "equipment",
            })

        return results


def check_rule(rule: str, scores: dict[str, float]) -> bool:
    """Evaluate whether candidate passes content filter rule."""
    p_person = scores.get("person", 0.5)
    p_equip = scores.get("equipment", 0.5)

    has_person = p_person >= 0.35
    has_equip = p_equip >= 0.35

    if rule == "person":
        return has_person
    elif rule == "equipment":
        return has_equip
    elif rule == "both":
        return has_person and has_equip
    elif rule == "either":
        return has_person or has_equip
    elif rule == "none":
        return True
    return True
