"""Hybrid RAG retriever combining category tag overlap and dense vector similarity."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings
from pipeline.rag.indexer import rag_index_paths
from pipeline.rag.taxonomy import TAXONOMY_CATEGORIES

logger = logging.getLogger(__name__)


class RagIndexNotReady(FileNotFoundError):
    """Raised when the RAG index has not been built (or is empty)."""


class RagRetriever:
    """Retrieves grounded dataset captions and domain excerpts using tag scoring and vector similarity."""

    def __init__(self, settings: Settings | None = None, classifier: ClipClassifier | None = None):
        self.settings = settings or get_settings()
        self.db_path, self.npz_path = rag_index_paths(self.settings)
        self.classifier = classifier or ClipClassifier(self.settings)

        self._captions_cache: list[dict[str, Any]] | None = None
        self._embeddings_cache: np.ndarray | None = None
        self._id_to_idx: dict[int, int] = {}
        self._loaded_mtime: float | None = None

    def is_ready(self) -> bool:
        """True when both index files exist (cheap check, does not load anything)."""
        return self.db_path.exists() and self.npz_path.exists()

    def invalidate(self) -> None:
        """Drop the in-memory cache so the next query reloads the index from disk."""
        self._captions_cache = None
        self._embeddings_cache = None
        self._id_to_idx = {}
        self._loaded_mtime = None

    def _ensure_loaded(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        """Loads metadata from SQLite and dense embeddings from NPZ into memory (reloads after a rebuild)."""
        if not self.is_ready():
            raise RagIndexNotReady(
                f"RAG index files not found at {self.db_path} or {self.npz_path}. Build the index first."
            )

        npz_mtime = self.npz_path.stat().st_mtime
        if (
            self._captions_cache is not None
            and self._embeddings_cache is not None
            and self._loaded_mtime == npz_mtime
        ):
            return self._captions_cache, self._embeddings_cache

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, split, stem, img_rel_path, txt_rel_path, full_caption,
                       equipment_text, surroundings_text, position_text, lighting_text,
                       equipment_tags, surroundings_tags, position_tags, camera_lighting_tags,
                       word_count
                FROM rag_captions
                ORDER BY id ASC
                """
            ).fetchall()

        captions: list[dict[str, Any]] = []
        for r in rows:
            captions.append({
                "id": r["id"],
                "split": r["split"],
                "stem": r["stem"],
                "img_rel_path": r["img_rel_path"],
                "txt_rel_path": r["txt_rel_path"],
                "full_caption": r["full_caption"],
                "equipment_text": r["equipment_text"],
                "surroundings_text": r["surroundings_text"],
                "position_text": r["position_text"],
                "lighting_text": r["lighting_text"],
                "equipment_tags": set(json.loads(r["equipment_tags"])),
                "surroundings_tags": set(json.loads(r["surroundings_tags"])),
                "position_tags": set(json.loads(r["position_tags"])),
                "camera_lighting_tags": set(json.loads(r["camera_lighting_tags"])),
                "word_count": r["word_count"],
            })

        with np.load(self.npz_path) as npz_data:
            npz_ids = npz_data["ids"]
            npz_embeddings = npz_data["embeddings"].astype(np.float32)

        # Align embedding rows to caption rows by id (drop rows missing on either side)
        npz_pos = {int(row_id): i for i, row_id in enumerate(npz_ids)}
        captions = [c for c in captions if c["id"] in npz_pos]
        if captions:
            embeddings = npz_embeddings[[npz_pos[c["id"]] for c in captions]]
        else:
            embeddings = np.zeros((0, npz_embeddings.shape[1] if npz_embeddings.ndim == 2 else 512), dtype=np.float32)

        self._captions_cache = captions
        self._embeddings_cache = embeddings
        self._id_to_idx = {c["id"]: i for i, c in enumerate(captions)}
        self._loaded_mtime = npz_mtime
        return captions, embeddings

    def count(self) -> int:
        """Number of indexed captions (0 when the index is missing)."""
        if not self.is_ready():
            return 0
        try:
            captions, _ = self._ensure_loaded()
        except Exception as err:  # corrupt / half-written index
            logger.warning("Failed to load RAG index: %s", err)
            return 0
        return len(captions)

    @staticmethod
    def _mmr_select(
        candidate_idx: np.ndarray,
        relevance: np.ndarray,
        embeddings: np.ndarray,
        top_k: int,
        lambda_: float,
        dup_threshold: float = 0.98,
    ) -> list[int]:
        """Maximal Marginal Relevance: trade relevance against similarity to already chosen exemplars.

        Candidates whose cosine similarity to a chosen exemplar exceeds ``dup_threshold`` are treated as
        duplicates and only used if nothing else is left (CLIP text similarities are uniformly high, so
        the MMR penalty alone does not separate identical captions).
        """
        selected: list[int] = []
        remaining = list(candidate_idx)
        duplicates: list[int] = []
        while remaining and len(selected) < top_k:
            if not selected:
                best = remaining[0]
            else:
                sel_emb = embeddings[selected]
                rem_emb = embeddings[remaining]
                max_sim = (rem_emb @ sel_emb.T).max(axis=1)
                is_dup = max_sim > dup_threshold
                duplicates.extend(i for i, d in zip(remaining, is_dup) if d)
                remaining = [i for i, d in zip(remaining, is_dup) if not d]
                if not remaining:
                    break
                max_sim = max_sim[~is_dup]
                mmr = lambda_ * relevance[remaining] - (1.0 - lambda_) * max_sim
                best = remaining[int(np.argmax(mmr))]
            selected.append(best)
            remaining.remove(best)
        # Back-fill with duplicates (by relevance) only when the pool is exhausted
        for i in sorted(duplicates, key=lambda j: -relevance[j]):
            if len(selected) >= top_k:
                break
            selected.append(i)
        return selected

    def retrieve(
        self,
        selected_tags: dict[str, list[str]],
        user_description: str = "",
        top_k: int = 4,
        diversity: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Executes hybrid retrieval over dataset captions.

        ``diversity`` is the MMR lambda (1.0 = pure relevance, lower = more varied exemplars).
        """
        captions, embeddings = self._ensure_loaded()
        total_items = len(captions)
        if total_items == 0:
            return []

        # 1. Flatten selected tags
        sel_equip = set(selected_tags.get("equipment", []))
        sel_surround = set(selected_tags.get("surroundings", []))
        sel_pos = set(selected_tags.get("position", []))
        sel_light = set(selected_tags.get("camera_lighting", []))
        total_selected = len(sel_equip) + len(sel_surround) + len(sel_pos) + len(sel_light)

        # 2. Compute tag matching score for every caption
        tag_scores = np.zeros(total_items, dtype=np.float32)
        matched_tags_list: list[list[str]] = []

        for i, c in enumerate(captions):
            m_equip = sel_equip.intersection(c["equipment_tags"])
            m_surr = sel_surround.intersection(c["surroundings_tags"])
            m_pos = sel_pos.intersection(c["position_tags"])
            m_light = sel_light.intersection(c["camera_lighting_tags"])

            matched = list(m_equip | m_surr | m_pos | m_light)
            matched_tags_list.append(matched)

            if total_selected > 0:
                # Weighted score based on matched categories
                score = (len(m_equip) * 1.5 + len(m_surr) * 1.0 + len(m_pos) * 1.2 + len(m_light) * 0.8) / max(1.0, total_selected * 1.2)
                tag_scores[i] = min(1.0, score)
            else:
                tag_scores[i] = 0.5

        # 3. Dense Vector Similarity
        dense_scores = np.zeros(total_items, dtype=np.float32)

        # Build query text combining user description and selected category terms
        query_parts: list[str] = []
        if user_description.strip():
            query_parts.append(user_description.strip())

        # Add human labels of selected tags into query string
        for cat_key, t_list in selected_tags.items():
            cat_info = TAXONOMY_CATEGORIES.get(cat_key, {})
            for _, items in cat_info.get("groups", {}).items():
                for item in items:
                    if item["id"] in t_list:
                        query_parts.append(item["label"])

        query_text = ", ".join(query_parts).strip()
        if query_text:
            query_emb = self.classifier.embed_texts([query_text])[0]  # normalized 512-dim vector
            # Dot product with normalized caption embeddings
            dot_products = np.dot(embeddings, query_emb)
            # Clip dot product into [0, 1] for scoring
            dense_scores = np.clip((dot_products + 1.0) / 2.0, 0.0, 1.0)
        else:
            dense_scores = np.full(total_items, 0.5, dtype=np.float32)

        # 4. Hybrid score combination
        if total_selected > 0 and user_description.strip():
            hybrid_scores = 0.55 * tag_scores + 0.45 * dense_scores
        elif total_selected > 0:
            hybrid_scores = 0.75 * tag_scores + 0.25 * dense_scores
        else:
            hybrid_scores = dense_scores

        # 5. Top-K ranking, re-ranked with MMR over a relevance pool so exemplars are not near-duplicates
        top_k = max(1, int(top_k))
        pool = np.argsort(-hybrid_scores)[: max(top_k * 8, 32)]
        if 0.0 < diversity < 1.0 and len(pool) > top_k:
            ranked_indices = self._mmr_select(pool, hybrid_scores, embeddings, top_k, diversity)
        else:
            ranked_indices = list(pool[:top_k])

        results: list[dict[str, Any]] = []
        for idx in ranked_indices:
            cap = captions[idx]
            results.append({
                "id": cap["id"],
                "stem": cap["stem"],
                "split": cap["split"],
                "score": round(float(hybrid_scores[idx]), 3),
                "matched_tags": matched_tags_list[idx],
                "img_rel_path": cap["img_rel_path"],
                "full_caption": cap["full_caption"],
                "equipment_text": cap["equipment_text"],
                "surroundings_text": cap["surroundings_text"],
                "position_text": cap["position_text"],
                "lighting_text": cap["lighting_text"],
            })

        return results
