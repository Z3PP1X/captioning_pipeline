"""Hybrid RAG retriever combining category tag overlap and dense vector similarity.

Dense modes (R1): ``whole`` = one CLIP text vector per caption (CLIP sees only the first ~55 words);
``domain`` = the query is split by the indexer's segmenter, each domain part is scored against the caption's
chunks of the same domain (max over chunks, a damped best other-domain chunk as fallback) and the domains are
averaged, weighted up where the user selected tags. Indexes without domain vectors fall back to ``whole``.
R3: ``similar`` ranks captions by the image embedding of their exemplar image (``rag_image_embeddings.npz``).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections import OrderedDict
from typing import Any

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings
from pipeline.rag.indexer import (
    DOMAIN_KEYS,
    FTS_TABLE,
    RagCaptionIndexer,
    build_fts,
    chunk_text,
    extract_source_id,
    image_index_path,
    load_image_index,
    rag_index_paths,
)
from pipeline.rag.taxonomy import TAXONOMY_CATEGORIES

logger = logging.getLogger(__name__)

QUERY_CACHE_SIZE = 512
RRF_K = 60  # standard Reciprocal Rank Fusion constant (Cormack et al. 2009)
BM25_LIMIT = 200
DENSE_MODES = ("whole", "domain")
# Default when a request does not choose (falls back to "whole" for an index without domain vectors)
DEFAULT_DENSE_MODE = "domain"
# Weight of a domain the user selected tags in (other domains 1.0)
DOMAIN_TAG_BOOST = 2.0
# A caption's best chunk of another domain counts, minus this, when it beats its own-domain chunks
CROSS_DOMAIN_PENALTY = 0.05
# FTS5 column weights: full caption, then the 4 domain excerpts (sentences of the same caption)
BM25_WEIGHTS = (1.0, 0.5, 0.5, 0.5, 0.5)
_FTS_STOPWORDS = frozenset(
    "a an and are as at be by for from her his in into is it its of on or she that the their them this to "
    "with without while very".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def fts_query(text: str, max_terms: int = 24) -> str:
    """OR-query of the quoted, de-duplicated content words of ``text`` ('' when nothing is left)."""
    terms: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < 2 or tok in _FTS_STOPWORDS or tok in terms:
            continue
        terms.append(tok)
        if len(terms) >= max_terms:
            break
    return " OR ".join(f'"{t}"' for t in terms)


def competition_ranks(scores: np.ndarray) -> np.ndarray:
    """1-based ranks where ties share the best rank (1, 2, 2, 4)."""
    neg = -np.asarray(scores, dtype=np.float64)
    return np.searchsorted(np.sort(neg), neg, side="left") + 1


__all__ = [
    "DEFAULT_DENSE_MODE", "DENSE_MODES", "RagIndexNotReady", "RagRetriever", "competition_ranks",
    "extract_source_id", "fts_query",
]

# Columns added with the captions-table source (R6); an index built before them still loads
_OPTIONAL_COLUMNS = ("frame_id", "source_id", "caption_source")


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
        self._fts_ready = False
        self._load_lock = threading.Lock()
        # R1 domain chunks aligned to the loaded captions (None = index without domain vectors)
        self._seg_caption_idx: np.ndarray | None = None
        self._seg_embeddings: np.ndarray | None = None
        self._seg_domain_rows: list[np.ndarray] = []
        # R3 image index aligned to the loaded captions
        self.image_path = image_index_path(self.settings)
        self._image_cache: dict[str, Any] | None = None
        self._image_cache_key: tuple | None = None
        self._image_lock = threading.Lock()

        self._query_cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._query_cache_lock = threading.Lock()
        self.query_cache_hits = 0
        self.query_cache_misses = 0
        self._warm_thread: threading.Thread | None = None

    def is_ready(self) -> bool:
        """True when both index files exist (cheap check, does not load anything)."""
        return self.db_path.exists() and self.npz_path.exists()

    def invalidate(self) -> None:
        """Drop the in-memory cache so the next query reloads the index from disk."""
        self._captions_cache = None
        self._embeddings_cache = None
        self._id_to_idx = {}
        self._loaded_mtime = None
        self._fts_ready = False
        self._seg_caption_idx = None
        self._seg_embeddings = None
        self._seg_domain_rows = []
        self._image_cache = None
        self._image_cache_key = None

    # ------------------------------------------------------------------ warm-up + query cache
    def warm(self) -> None:
        """Load the index and the text encoder (ONNX session) so the first real query is fast."""
        try:
            if self.is_ready():
                self._ensure_loaded()
            self.embed_query("photograph")
        except Exception as err:  # warm-up is best effort; the real query reports errors
            logger.warning("RAG warm-up failed: %s", err)

    def warm_async(self) -> bool:
        """Run ``warm`` once in a daemon thread. Returns True when a warm-up was started."""
        if self._warm_thread is not None:
            return False
        self._warm_thread = threading.Thread(target=self.warm, name="rag_warmup", daemon=True)
        self._warm_thread.start()
        return True

    def embed_query(self, text: str) -> np.ndarray:
        """Normalised query embedding, LRU-cached by whitespace/case-normalised text (CLIP lowercases anyway)."""
        key = " ".join(text.split()).lower()
        with self._query_cache_lock:
            cached = self._query_cache.get(key)
            if cached is not None:
                self._query_cache.move_to_end(key)
                self.query_cache_hits += 1
                return cached
        emb = np.asarray(self.classifier.embed_texts([key])[0], dtype=np.float32)
        emb.setflags(write=False)
        with self._query_cache_lock:
            self.query_cache_misses += 1
            self._query_cache[key] = emb
            self._query_cache.move_to_end(key)
            while len(self._query_cache) > QUERY_CACHE_SIZE:
                self._query_cache.popitem(last=False)
        return emb

    # ------------------------------------------------------------------ BM25 (SQLite FTS5)
    def _ensure_fts(self) -> bool:
        """Make sure the FTS5 table exists and belongs to the current build; build it lazily for old indexes."""
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name='rag_meta'").fetchone()
                meta = {k: v for k, v in conn.execute("SELECT key, value FROM rag_meta")} if has_meta else {}
                has_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (FTS_TABLE,)
                ).fetchone() is not None
                built_at = meta.get("built_at", "")
                if has_table and meta.get("fts_built_at") == built_at:
                    return True
                ok = build_fts(conn, built_at)
                conn.commit()
                if ok:
                    logger.info("Built missing/stale RAG FTS5 index")
                return ok
        except sqlite3.Error as err:
            logger.info("RAG FTS5 index unavailable: %s", err)
            return False

    @property
    def lexical_ready(self) -> bool:
        """True when BM25 search is available for the loaded index."""
        return self._fts_ready

    def _bm25_scores(self, description: str) -> dict[int, float] | None:
        """BM25 relevance (higher = better) per caption id for the free-text description; None if unavailable."""
        if not self._fts_ready:
            return None
        query = fts_query(description)
        if not query:
            return None
        weights = ", ".join(str(w) for w in BM25_WEIGHTS)
        try:
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                rows = conn.execute(
                    f"SELECT rowid, bm25({FTS_TABLE}, {weights}) AS s FROM {FTS_TABLE} "
                    f"WHERE {FTS_TABLE} MATCH ? ORDER BY s LIMIT ?",
                    (query, BM25_LIMIT),
                ).fetchall()
        except sqlite3.Error as err:
            logger.warning("BM25 query failed, falling back to dense + tags: %s", err)
            return None
        # FTS5 bm25() is negative (more negative = more relevant)
        return {int(rowid): -float(score) for rowid, score in rows}

    def _ensure_loaded(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        """Loads metadata from SQLite and dense embeddings from NPZ into memory (reloads after a rebuild)."""
        if not self.is_ready():
            raise RagIndexNotReady(
                f"RAG index files not found at {self.db_path} or {self.npz_path}. Build the index first."
            )

        with self._load_lock:
            return self._load_locked()

    def _load_locked(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        npz_mtime = self.npz_path.stat().st_mtime
        if (
            self._captions_cache is not None
            and self._embeddings_cache is not None
            and self._loaded_mtime == npz_mtime
        ):
            return self._captions_cache, self._embeddings_cache

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            present = {r[1] for r in conn.execute("PRAGMA table_info(rag_captions)")}
            optional = ", ".join(c if c in present else f"NULL AS {c}" for c in _OPTIONAL_COLUMNS)
            rows = conn.execute(
                f"""
                SELECT id, split, stem, img_rel_path, txt_rel_path, full_caption,
                       equipment_text, surroundings_text, position_text, lighting_text,
                       equipment_tags, surroundings_tags, position_tags, camera_lighting_tags,
                       word_count, {optional}
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
                "frame_id": r["frame_id"],
                "source_id": r["source_id"] or extract_source_id(r["stem"] or ""),
                "caption_source": r["caption_source"],
            })

        with np.load(self.npz_path) as npz_data:
            npz_ids = npz_data["ids"]
            npz_embeddings = npz_data["embeddings"].astype(np.float32)
            has_segments = "seg_ids" in npz_data.files
            seg_ids = npz_data["seg_ids"].astype(np.int64) if has_segments else None
            seg_domains = npz_data["seg_domains"].astype(np.int64) if has_segments else None
            seg_embeddings = npz_data["seg_embeddings"].astype(np.float32) if has_segments else None

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
        if seg_ids is not None and seg_embeddings is not None and seg_domains is not None:
            cap_idx = np.array([self._id_to_idx.get(int(i), -1) for i in seg_ids], dtype=np.int64)
            ok = cap_idx >= 0
            self._seg_caption_idx = cap_idx[ok]
            self._seg_embeddings = seg_embeddings[ok]
            domains = seg_domains[ok]
            self._seg_domain_rows = [np.flatnonzero(domains == d) for d in range(len(DOMAIN_KEYS))]
        else:
            self._seg_caption_idx = None
            self._seg_embeddings = None
            self._seg_domain_rows = []
        self._image_cache = None
        self._image_cache_key = None
        self._loaded_mtime = npz_mtime
        self._fts_ready = self._ensure_fts()
        return captions, embeddings

    def load(self) -> tuple[list[dict[str, Any]], np.ndarray]:
        """The loaded index (caption dicts + aligned embedding rows); raises RagIndexNotReady when missing."""
        return self._ensure_loaded()

    # ------------------------------------------------------------------ R1 dense modes
    @property
    def domain_ready(self) -> bool:
        """True when the loaded index has per-domain vectors."""
        return self._seg_embeddings is not None and len(self._seg_embeddings) > 0

    def has_domain_embeddings(self) -> bool:
        """Whether the index on disk has per-domain vectors (reads the NPZ member list only, no load)."""
        if self._captions_cache is not None and self._loaded_mtime is not None:
            try:
                if self.npz_path.stat().st_mtime == self._loaded_mtime:
                    return self.domain_ready
            except OSError:
                return False
        try:
            with np.load(self.npz_path) as npz:
                return "seg_ids" in npz.files and len(npz["seg_ids"]) > 0
        except Exception:
            return False

    def effective_dense_mode(self, requested: str | None = None) -> str:
        """The dense mode a query actually uses: ``domain`` only when the index has domain vectors."""
        mode = requested or DEFAULT_DENSE_MODE
        if mode not in DENSE_MODES:
            raise ValueError(f"dense_mode must be one of {DENSE_MODES}, got {mode!r}")
        if mode == "domain" and not self.has_domain_embeddings():
            return "whole"
        return mode

    @staticmethod
    def domain_query_parts(selected_tags: dict[str, list[str]], description: str) -> tuple[dict[int, str], dict[int, float]]:
        """Query text per domain: the description split by the caption segmenter, plus each selected tag's label
        in its own category; weight ``DOMAIN_TAG_BOOST`` for domains with selected tags (else 1.0)."""
        parts: dict[int, list[str]] = {}
        if description:
            segments = RagCaptionIndexer.segment_caption(description)
            for d, key in enumerate(DOMAIN_KEYS):
                if segments[key].strip():
                    parts.setdefault(d, []).append(segments[key].strip())
        weights: dict[int, float] = {}
        for d, key in enumerate(DOMAIN_KEYS):
            ids = set(selected_tags.get(key, []) or [])
            if not ids:
                continue
            labels = [
                item["label"]
                for items in TAXONOMY_CATEGORIES.get(key, {}).get("groups", {}).values()
                for item in items if item["id"] in ids
            ]
            if labels:
                parts.setdefault(d, []).append(", ".join(labels))
                weights[d] = DOMAIN_TAG_BOOST
        return {d: ". ".join(texts) for d, texts in parts.items()}, {d: weights.get(d, 1.0) for d in parts}

    def _domain_dense(self, n: int, parts: dict[int, str], weights: dict[int, float]) -> np.ndarray | None:
        """Weighted mean over the query's domains of max cosine vs the caption's chunks of that domain (-1..1)."""
        if self._seg_embeddings is None or self._seg_caption_idx is None or not parts:
            return None
        seg_emb, seg_cap = self._seg_embeddings, self._seg_caption_idx
        total = np.zeros(n, dtype=np.float64)
        weight_sum = 0.0
        for d, text in parts.items():
            chunks = chunk_text(text)
            if not chunks:
                continue
            rows = self._seg_domain_rows[d]
            per_chunk: list[np.ndarray] = []
            for chunk in chunks:
                sims = seg_emb @ self.embed_query(chunk)
                best_any = np.full(n, -1.0)
                np.maximum.at(best_any, seg_cap, sims)
                best_own = np.full(n, -1.0)
                if rows.size:
                    np.maximum.at(best_own, seg_cap[rows], sims[rows])
                per_chunk.append(np.maximum(best_own, best_any - CROSS_DOMAIN_PENALTY))
            w = float(weights.get(d, 1.0))
            total += w * np.mean(per_chunk, axis=0)
            weight_sum += w
        return total / weight_sum if weight_sum else None

    # ------------------------------------------------------------------ R3 image index
    def _image_index(self) -> dict[str, Any] | None:
        """The image index aligned to the loaded captions (valid rows only); None when there is none."""
        captions, _ = self._ensure_loaded()
        if not self.image_path.is_file():
            return None
        try:
            key = (self.image_path.stat().st_mtime, self._loaded_mtime)
        except OSError:
            return None
        with self._image_lock:
            if self._image_cache is not None and self._image_cache_key == key:
                return self._image_cache
            data = load_image_index(self.image_path)
            if data is None:
                return None
            pos = [
                (cap_idx, j) for j, row_id in enumerate(data["ids"])
                if data["valid"][j] and (cap_idx := self._id_to_idx.get(int(row_id))) is not None
            ]
            dim = data["embeddings"].shape[1] if data["embeddings"].ndim == 2 else 0
            self._image_cache = {
                "caption_idx": np.array([c for c, _ in pos], dtype=np.int64),
                "embeddings": data["embeddings"][[j for _, j in pos]] if pos else np.zeros((0, dim), np.float32),
                "embedder": data["embedder"] or None,
                "model": data["model"] or None,
                "missing": int(len(data["ids"]) - len(pos)),
            }
            self._image_cache_key = key
            return self._image_cache

    def image_index_status(self) -> dict[str, Any]:
        """``{ready, count, embedder, model}`` of the image index (never raises)."""
        empty = {"ready": False, "count": 0, "embedder": None, "model": None}
        if not self.is_ready() or not self.image_path.is_file():
            return empty
        try:
            loaded = self._captions_cache is not None and self._loaded_mtime == self.npz_path.stat().st_mtime
            if not loaded:  # cheap peek (status polls must not load the whole index)
                with np.load(self.image_path) as npz:
                    count = int(np.asarray(npz["valid"], dtype=bool).sum())
                    embedder, model = str(npz["embedder"]) or None, str(npz["model"]) or None
                return {"ready": count > 0, "count": count, "embedder": embedder, "model": model}
            img = self._image_index()
        except Exception as err:
            logger.warning("Cannot load RAG image index: %s", err)
            return empty
        if not img:
            return empty
        count = int(len(img["caption_idx"]))
        return {"ready": count > 0, "count": count, "embedder": img["embedder"], "model": img["model"]}

    def similar(self, query_embedding: np.ndarray, top_k: int = 8, exclude_frame_id: int | None = None) -> list[dict[str, Any]]:
        """Captions whose exemplar image is closest to ``query_embedding`` (cosine, normalised vectors).

        The query frame itself (``exclude_frame_id``) is skipped. ``score`` = cosine clipped to 0..1.
        Raises ValueError when the query vector does not match the index (other embedder).
        """
        captions, _ = self._ensure_loaded()
        img = self._image_index()
        if not img or len(img["caption_idx"]) == 0:
            return []
        q = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        if q.shape[0] != img["embeddings"].shape[1]:
            raise ValueError(f"query embedding has {q.shape[0]} dims, the image index {img['embeddings'].shape[1]}")
        q = q / max(1e-12, float(np.linalg.norm(q)))
        sims = img["embeddings"] @ q
        results: list[dict[str, Any]] = []
        for o in np.argsort(-sims, kind="stable"):
            cap = captions[int(img["caption_idx"][o])]
            if exclude_frame_id is not None and cap["frame_id"] == exclude_frame_id:
                continue
            results.append(self._result_item(cap, float(np.clip(sims[o], 0.0, 1.0)), []))
            if len(results) >= max(1, int(top_k)):
                break
        return results

    @staticmethod
    def _result_item(cap: dict[str, Any], score: float, matched_tags: list[str]) -> dict[str, Any]:
        """One result in the ``/api/rag/retrieve`` item shape."""
        return {
            "id": cap["id"],
            "stem": cap["stem"],
            "split": cap["split"],
            "frame_id": cap["frame_id"],
            "source_id": cap["source_id"],
            "score": round(float(score), 3),
            "matched_tags": matched_tags,
            "img_rel_path": cap["img_rel_path"],
            "full_caption": cap["full_caption"],
            "equipment_text": cap["equipment_text"],
            "surroundings_text": cap["surroundings_text"],
            "position_text": cap["position_text"],
            "lighting_text": cap["lighting_text"],
        }

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
        sources: list[str] | None = None,
        max_per_source: int | None = None,
    ) -> list[int]:
        """Maximal Marginal Relevance: trade relevance against similarity to already chosen exemplars.

        Candidates whose cosine similarity to a chosen exemplar exceeds ``dup_threshold`` are treated as
        duplicates and only used if nothing else is left. If ``max_per_source`` is set, candidates from
        the same source video are capped to promote diversity across different videos.
        """
        selected: list[int] = []
        remaining = list(candidate_idx)
        duplicates: list[int] = []
        source_counts: dict[str, int] = {}

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

                # If source capping is active, filter candidates under cap
                if sources is not None and max_per_source is not None and max_per_source > 0:
                    avail_mask = np.array([source_counts.get(sources[i], 0) < max_per_source for i in remaining])
                    if np.any(avail_mask):
                        masked_mmr = np.where(avail_mask, mmr, -np.inf)
                        best = remaining[int(np.argmax(masked_mmr))]
                    else:
                        best = remaining[int(np.argmax(mmr))]
                else:
                    best = remaining[int(np.argmax(mmr))]

            selected.append(best)
            if sources is not None:
                src = sources[best]
                source_counts[src] = source_counts.get(src, 0) + 1
            remaining.remove(best)

        # Back-fill with duplicates (by relevance) only when the pool is exhausted
        for i in sorted(duplicates, key=lambda j: -relevance[j]):
            if len(selected) >= top_k:
                break
            selected.append(i)
        return selected

    @staticmethod
    def _rrf_fuse(
        captions: list[dict[str, Any]],
        tag_scores: np.ndarray | None,
        dense_scores: np.ndarray | None,
        bm25_by_id: dict[int, float] | None,
    ) -> np.ndarray:
        """Reciprocal Rank Fusion of the available rankings, scaled so a #1 in every list scores 1.0.

        Captions without any tag match or BM25 hit get no contribution from that list (their rank there
        is meaningless); ties share the same rank. With no signal at all every caption scores 0.5.
        """
        n = len(captions)
        fused = np.zeros(n, dtype=np.float64)
        lists = 0
        if tag_scores is not None:
            lists += 1
            ranks = competition_ranks(tag_scores)
            fused += np.where(tag_scores > 0, 1.0 / (RRF_K + ranks), 0.0)
        if dense_scores is not None:
            lists += 1
            fused += 1.0 / (RRF_K + competition_ranks(dense_scores))
        if bm25_by_id:
            lists += 1
            lex = np.array([bm25_by_id.get(c["id"], 0.0) for c in captions], dtype=np.float64)
            fused += np.where(lex > 0, 1.0 / (RRF_K + competition_ranks(lex)), 0.0)
        if lists == 0:
            return np.full(n, 0.5, dtype=np.float32)
        return (fused * (RRF_K + 1) / lists).astype(np.float32)

    def score(
        self,
        selected_tags: dict[str, list[str]],
        user_description: str = "",
        fusion: str = "rrf",
        dense_mode: str | None = None,
    ) -> tuple[list[dict[str, Any]], np.ndarray, np.ndarray, list[list[str]]]:
        """Fused relevance of every indexed caption: (captions, embeddings, scores, matched tags per caption).

        The ranking stage of ``retrieve`` (before MMR re-ranking); also used by the R10 eval.
        ``dense_mode``: ``whole`` | ``domain`` (default ``DEFAULT_DENSE_MODE``; see ``effective_dense_mode``).
        """
        captions, embeddings = self._ensure_loaded()
        dense_mode = self.effective_dense_mode(dense_mode)
        total_items = len(captions)
        if total_items == 0:
            return captions, embeddings, np.zeros(0, dtype=np.float32), []

        # 1. Flatten selected tags
        sel_equip = set(selected_tags.get("equipment", []))
        sel_surround = set(selected_tags.get("surroundings", []))
        sel_pos = set(selected_tags.get("position", []))
        sel_light = set(selected_tags.get("camera_lighting", []))
        total_selected = len(sel_equip) + len(sel_surround) + len(sel_pos) + len(sel_light)
        description = user_description.strip()

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
        # Build query text combining user description and selected category terms
        query_parts: list[str] = []
        if description:
            query_parts.append(description)

        # Add human labels of selected tags into query string
        for cat_key, t_list in selected_tags.items():
            cat_info = TAXONOMY_CATEGORIES.get(cat_key, {})
            for _, items in cat_info.get("groups", {}).items():
                for item in items:
                    if item["id"] in t_list:
                        query_parts.append(item["label"])

        query_text = ", ".join(query_parts).strip()
        dot_products = None
        if query_text and dense_mode == "domain":
            parts, weights = self.domain_query_parts(selected_tags, description)
            dot_products = self._domain_dense(total_items, parts, weights)
        if query_text and dot_products is None:
            query_emb = self.embed_query(query_text)  # normalized vector, LRU-cached
            dot_products = np.dot(embeddings, query_emb)
        if dot_products is not None:
            # Clip dot product into [0, 1] for scoring
            dense_scores = np.clip((dot_products + 1.0) / 2.0, 0.0, 1.0)
        else:
            dense_scores = np.full(total_items, 0.5, dtype=np.float32)

        # 4. Score fusion
        if fusion == "linear":
            if total_selected > 0 and description:
                hybrid_scores = 0.55 * tag_scores + 0.45 * dense_scores
            elif total_selected > 0:
                hybrid_scores = 0.75 * tag_scores + 0.25 * dense_scores
            else:
                hybrid_scores = dense_scores
        else:
            hybrid_scores = self._rrf_fuse(
                captions,
                tag_scores if total_selected > 0 else None,
                dense_scores if query_text else None,
                self._bm25_scores(description) if description else None,
            )
        return captions, embeddings, np.asarray(hybrid_scores, dtype=np.float32), matched_tags_list

    def retrieve(
        self,
        selected_tags: dict[str, list[str]],
        user_description: str = "",
        top_k: int = 4,
        diversity: float = 0.7,
        max_per_source: int | None = None,
        fusion: str = "rrf",
        dense_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        """Executes hybrid retrieval over dataset captions.

        Three signals are fused: weighted taxonomy-tag overlap, dense CLIP cosine of the query text
        (description + selected tag labels) and BM25 over the captions for the free-text description
        (SQLite FTS5; skipped when unavailable). ``fusion="rrf"`` (default) combines their rankings with
        Reciprocal Rank Fusion, normalised to 0..1; ``fusion="linear"`` keeps the old fixed weights
        (tags + dense only) for comparison.
        ``diversity`` is the MMR lambda (1.0 = pure relevance, lower = more varied exemplars).
        ``max_per_source`` caps exemplars from the same source video (captions-table video id, else the stem prefix).
        ``dense_mode`` picks whole-caption or per-domain dense scoring (R1, see ``score``).
        """
        captions, embeddings, hybrid_scores, matched_tags_list = self.score(
            selected_tags, user_description, fusion, dense_mode
        )
        if len(captions) == 0:
            return []

        # 5. Top-K ranking, re-ranked with MMR over a relevance pool so exemplars are not near-duplicates
        top_k = max(1, int(top_k))
        pool = np.argsort(-hybrid_scores, kind="stable")[: max(top_k * 8, 32)]
        sources = [c["source_id"] for c in captions]
        if 0.0 < diversity < 1.0 and len(pool) > top_k:
            ranked_indices = self._mmr_select(
                pool, hybrid_scores, embeddings, top_k, diversity, sources=sources, max_per_source=max_per_source
            )
        else:
            ranked_indices = list(pool[:top_k])

        return [self._result_item(captions[i], hybrid_scores[i], matched_tags_list[i]) for i in ranked_indices]
