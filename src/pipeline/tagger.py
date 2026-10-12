"""Local high-efficiency zero-shot tagger for fine-art restraint and bondage datasets."""
from __future__ import annotations

import threading
from typing import Any

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings

# Domain vocabulary organized into descriptive categories
TAG_TAXONOMY: dict[str, list[tuple[str, str]]] = {
    # (tag_label, detailed_prompt_for_clip)
    "discipline": [
        ("japanese rope bondage", "japanese rope bondage shibari kinbaku"),
        ("rope harness", "decorative rope chest harness on woman"),
        ("strict immobilizing bondage", "strict tight immobilizing restraint bondage"),
        ("chain restraint", "heavy steel link chain restraint"),
        ("leather restraint", "leather strap and cuff bondage restraint"),
        ("suspension", "partial suspension bondage off the ground"),
        ("spreader bar restraint", "metal spreader bar holding legs or wrists apart"),
    ],
    "equipment": [
        ("hemp rope", "unbleached twisted japanese hemp rope cordage"),
        ("jute cordage", "natural oiled jute cordage fiber rope"),
        ("metal chains", "welded steel link chains with padlocks"),
        ("metal handcuffs", "polished chrome steel handcuffs locked on wrists"),
        ("leather cuffs", "padded leather wrist cuffs with roller buckles"),
        ("posture collar", "wide leather posture collar around neck"),
        ("leather collar and leash", "leather collar with attached tether leash"),
        ("ball gag", "silicone or leather strap ball gag in mouth"),
        ("nipple clamps", "metallic clover nipple clamps attached"),
        ("steel carabiners", "forged steel carabiners and welded o-rings"),
    ],
    "posture": [
        ("kneeling in seiza", "woman kneeling in traditional japanese seiza posture"),
        ("standing upright", "woman standing upright on tiptoes"),
        ("arched lumbar spine", "woman kneeling with deeply arched back"),
        ("on all fours", "woman restrained on hands and knees on the floor"),
        ("lying on back", "woman lying on her back on the floor"),
        ("hands tied behind back", "wrists bound firmly behind back at lumbar spine"),
        ("takate-kote box tie", "takate-kote box tie binding elbows behind back"),
        ("frog tie", "frog tie position with knees bent and ankles bound"),
        ("legs spread wide", "legs spread wide apart held by ropes or spreader bar"),
        ("head tilted upward", "head tilted back with exposed throat"),
    ],
    "staging": [
        ("minimalist dark studio", "minimalist dark studio void aesthetic"),
        ("reflective black floor", "black high-gloss reflective mirror floor"),
        ("chiaroscuro lighting", "dramatic high contrast chiaroscuro studio lighting"),
        ("focused directional spotlight", "focused overhead spotlight beam on subject"),
        ("specular highlights", "specular highlights skimming along skin and metallic hardware"),
    ],
    "subject": [
        ("solo woman", "solo adult woman alone"),
        ("two women", "two adult women together"),
    ],
}


class LocalTagger:
    """Fast local zero-shot tagger running via ClipClassifier ONNX session."""

    def __init__(self, classifier: ClipClassifier | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.classifier = classifier or ClipClassifier(self.settings)
        self._lock = threading.RLock()
        self._tag_definitions: list[tuple[str, str, str]] = []  # (category, tag_label, prompt)
        self._tag_labels: list[str] = []
        self._tag_matrix: np.ndarray | None = None

    def _ensure_tag_matrix(self) -> np.ndarray:
        with self._lock:
            if self._tag_matrix is None:
                self._build_tag_matrix()
            return self._tag_matrix

    def _build_tag_matrix(self) -> None:
        """Pre-embed all tag text prompts once into a normalized matrix."""
        with self._lock:
            prompts = []
            self._tag_definitions.clear()
            self._tag_labels.clear()

            for category, items in TAG_TAXONOMY.items():
                for tag_label, prompt in items:
                    self._tag_definitions.append((category, tag_label, prompt))
                    self._tag_labels.append(tag_label)
                    prompts.append(prompt)

            # Embed all prompts in a single batch
            text_vecs = self.classifier.embed_texts(prompts)
            # Normalize rows
            norms = np.linalg.norm(text_vecs, axis=-1, keepdims=True)
            norms[norms == 0] = 1e-12
            self._tag_matrix = (text_vecs / norms).astype(np.float32)

    def tag_frame(self, image_embedding: np.ndarray, top_k_per_category: int = 2, threshold: float = 0.22) -> list[str]:
        """Compute matching tags for a single image embedding (vector of shape (512,))."""
        mat = self._ensure_tag_matrix()
        if mat is None or len(image_embedding) == 0:
            return []

        # Cosine similarities via single dot product: (N_tags,)
        sims = np.dot(mat, image_embedding)

        chosen_tags: list[str] = []
        categorized_scores: dict[str, list[tuple[str, float]]] = {}

        for (category, tag_label, _), score in zip(self._tag_definitions, sims):
            categorized_scores.setdefault(category, []).append((tag_label, float(score)))

        for category, items in categorized_scores.items():
            # Sort by score descending
            items.sort(key=lambda x: x[1], reverse=True)
            added = 0
            for tag_label, score in items:
                if score >= threshold and added < top_k_per_category:
                    chosen_tags.append(tag_label)
                    added += 1

        return chosen_tags

    def tag_chunk_frames(self, frames: list[dict[str, Any]]) -> tuple[list[str], list[list[str]]]:
        """Tag all frames in a chunk, returning (chunk_summary_tags, list_of_per_frame_tags)."""
        if not frames:
            return [], []

        per_frame_tags: list[list[str]] = []
        tag_counts: dict[str, int] = {}

        for f in frames:
            emb = f.get("embedding")
            if emb is not None:
                tags = self.tag_frame(emb)
            else:
                tags = []
            per_frame_tags.append(tags)
            for t in tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        # Chunk summary tags: tags appearing in at least 30% of frames in the chunk,
        # or the top 6 most frequent tags
        min_freq = max(1, int(len(frames) * 0.30))
        sorted_by_freq = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)

        chunk_summary = [t for t, count in sorted_by_freq if count >= min_freq]
        if len(chunk_summary) < 4:
            chunk_summary = [t for t, _ in sorted_by_freq[:6]]

        return chunk_summary, per_frame_tags
