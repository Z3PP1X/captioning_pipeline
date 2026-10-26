"""Local Non-ML LoRA-RAG Engine: SQLite FTS5 BM25 + TF-IDF/TruncatedSVD LSA + OpenRouter Embeddings (Module D.2).

Implements:
- Stage 1 (Lexical & Structured):
  - 4-domain sentence segmentation (`equipment_text`, `surroundings_text`, `position_text`, `lighting_text`)
  - SQLite FTS5 virtual table (`rag_fts`) with Porter stemming (`tokenize='porter unicode61'`)
  - Weighted `bm25(rag_fts, 1.0, 0.6, 0.6, 0.6, 0.6, 0.8)` ranking + metadata & tag category filters
- Stage 2 (Semantic Dense / Latent Retrieval):
  - Mode 2A (`tfidf_svd_128`, 100% Local Non-ML Default): `scikit-learn` word+bigram TF-IDF +
    TruncatedSVD (LSA) L2-normalized vectors with exact cosine similarity cos(theta) = (A . B) / (||A|| ||B||)
  - Mode 2B (`openrouter_embedding`, Optional API): OpenRouter `/embeddings` endpoint with local SQLite
    `rag_vectors` caching
- Hybrid Fusion & Diversification:
  - Reciprocal Rank Fusion (RRF, k=60) combining Tag Overlap + FTS5 BM25 + Dense Cosine Similarity
  - Maximal Marginal Relevance (MMR) diversification
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Literal

import httpx
import numpy as np

try:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    _HAS_SKLEARN = True
except ImportError:
    TruncatedSVD = None  # type: ignore[assignment,misc]
    TfidfVectorizer = None  # type: ignore[assignment,misc]
    _HAS_SKLEARN = False

from backend.app.config import get_app_settings
from backend.app.db.session import DatabaseManager, get_db

DenseBackend = Literal["tfidf_svd_128", "openrouter_embedding"]
RetrievalMode = Literal["hybrid", "fts5_only", "dense_only"]

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
TOKEN_RE = re.compile(r"[a-z0-9]+")
RRF_K = 60

FTS_STOPWORDS = frozenset(
    "a an and are as at be by for from her his in into is it its of on or she that the their them this to "
    "with without while very".split()
)

DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "equipment": (
        "restrain",
        "restrained",
        "restraint",
        "bound",
        "rope",
        "hemp",
        "jute",
        "cordage",
        "chain",
        "chained",
        "cuff",
        "handcuff",
        "collar",
        "gag",
        "harness",
        "tie",
        "tied",
        "rigging",
        "rigged",
        "shackle",
        "spreader",
        "leather",
        "dress",
        "outfit",
        "jacket",
        "clothing",
        "fabric",
        "silk",
    ),
    "surroundings": (
        "set in",
        "setting",
        "scene is set",
        "basement",
        "cellar",
        "brick",
        "concrete",
        "wood",
        "wooden",
        "wall",
        "floor",
        "dungeon",
        "studio",
        "void",
        "loft",
        "room",
        "background",
        "backdrop",
        "environment",
        "interior",
        "exterior",
        "street",
        "forest",
    ),
    "position": (
        "stand",
        "standing",
        "stands",
        "kneel",
        "kneeling",
        "seiza",
        "tabletop",
        "all-fours",
        "quadruped",
        "arched",
        "lordosis",
        "prone",
        "supine",
        "posture",
        "stance",
        "torso",
        "arms",
        "seated",
        "sitting",
        "leaning",
        "pose",
    ),
    "lighting": (
        "light",
        "lighting",
        "lit",
        "spotlight",
        "backlight",
        "backlit",
        "chiaroscuro",
        "shadow",
        "shadows",
        "lens",
        "f/2.8",
        "depth of field",
        "bokeh",
        "specular",
        "reflections",
        "highlights",
        "optics",
        "camera",
        "angle",
        "framing",
        "warm",
        "cool",
    ),
}


def _compile_domain_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alts = [re.sub(r"(?:\\ |\\-)+", r"[\\s-]+", re.escape(w)) for w in sorted(words, key=len, reverse=True)]
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?:e?s)?(?!\w)", re.IGNORECASE)


DOMAIN_PATTERNS = {k: _compile_domain_pattern(v) for k, v in DOMAIN_KEYWORDS.items()}


def segment_caption_domains(caption: str) -> dict[str, str]:
    """Segment a training caption into 4 semantic domains (`equipment`, `surroundings`, `position`, `lighting`)."""
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split((caption or "").strip()) if s.strip()]
    buckets: dict[str, list[str]] = {
        "equipment": [],
        "surroundings": [],
        "position": [],
        "lighting": [],
    }
    for s in sentences:
        if DOMAIN_PATTERNS["lighting"].search(s):
            buckets["lighting"].append(s)
        elif DOMAIN_PATTERNS["surroundings"].search(s):
            buckets["surroundings"].append(s)
        elif DOMAIN_PATTERNS["equipment"].search(s):
            buckets["equipment"].append(s)
        elif DOMAIN_PATTERNS["position"].search(s):
            buckets["position"].append(s)
        else:
            if not buckets["equipment"]:
                buckets["equipment"].append(s)
            else:
                buckets["position"].append(s)
    return {k: " ".join(v) for k, v in buckets.items()}


def build_fts5_match_query(text: str, max_terms: int = 24) -> str:
    """Build a safe OR-joined FTS5 MATCH query from natural language text."""
    terms: list[str] = []
    for tok in TOKEN_RE.findall((text or "").lower()):
        if len(tok) < 2 or tok in FTS_STOPWORDS or tok in terms:
            continue
        terms.append(tok)
        if len(terms) >= max_terms:
            break
    return " OR ".join(f'"{t}"' for t in terms)


def l2_normalize_rows(mat: np.ndarray) -> np.ndarray:
    """L2-normalize rows of a 2D float32 matrix so dot product equals cosine similarity."""
    arr = np.asarray(mat, dtype=np.float32)
    if arr.ndim == 1:
        norm = float(np.linalg.norm(arr))
        return (arr / norm).astype(np.float32) if norm > 1e-12 else np.zeros_like(arr, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms > 1e-12, norms, 1.0)
    return (arr / norms).astype(np.float32)


def cosine_similarity_vec(a: np.ndarray, b: np.ndarray) -> float:
    """Compute exact cosine similarity cos(theta) = (A . B) / (||A|| ||B||) between two 1D vectors."""
    va = np.asarray(a, dtype=np.float32).ravel()
    vb = np.asarray(b, dtype=np.float32).ravel()
    if va.size == 0 or vb.size == 0 or va.size != vb.size:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def competition_ranks(scores: np.ndarray) -> np.ndarray:
    """Compute 1-based competition ranks where ties share the best rank."""
    neg = -np.asarray(scores, dtype=np.float64)
    return np.searchsorted(np.sort(neg), neg, side="left") + 1


class LocalRagEngine:
    """Hybrid SQLite FTS5 BM25 + Local TF-IDF/SVD (or OpenRouter Embedding) RAG Engine."""

    def __init__(
        self,
        db: DatabaseManager | None = None,
        *,
        embedding_fn: Callable[[list[str], str], list[list[float]]] | None = None,
    ) -> None:
        self.db = db or get_db()
        self.app_settings = get_app_settings()
        self.embedding_fn = embedding_fn
        self._vectorizer: Any = None
        self._svd: Any = None
        self._np_vocab: dict[str, int] = {}
        self._np_idf: np.ndarray | None = None
        self._np_vt: np.ndarray | None = None
        self._lsa_dim: int = 128

    @staticmethod
    def _extract_ngrams(text: str) -> list[str]:
        tokens = [t for t in TOKEN_RE.findall((text or "").lower()) if t not in FTS_STOPWORDS]
        bigrams = [f"{tokens[i]}_{tokens[i + 1]}" for i in range(len(tokens) - 1)]
        return tokens + bigrams

    def _fit_tfidf_svd(self, texts: list[str], max_dim: int = 128) -> tuple[np.ndarray, int]:
        """Fit local word+bigram TF-IDF + TruncatedSVD LSA over `texts` and return L2-normalized float32 matrix.

        Uses `scikit-learn` when installed, or falls back to a pure-NumPy TF-IDF + `np.linalg.svd` implementation.
        """
        if not texts:
            self._vectorizer = None
            self._svd = None
            self._np_vocab = {}
            self._np_idf = None
            self._np_vt = None
            return np.zeros((0, max_dim), dtype=np.float32), max_dim

        if _HAS_SKLEARN and TfidfVectorizer is not None and TruncatedSVD is not None:
            vec = TfidfVectorizer(
                ngram_range=(1, 2),
                sublinear_tf=True,
                max_features=4096,
                token_pattern=r"(?u)\b\w+\b",
            )
            tfidf_mat = vec.fit_transform(texts)
            n_samples, n_features = tfidf_mat.shape

            n_comp = min(max_dim, n_samples - 1, n_features - 1)
            if n_comp >= 2:
                svd = TruncatedSVD(n_components=n_comp, random_state=42)
                dense = svd.fit_transform(tfidf_mat)
                self._svd = svd
            else:
                dense = tfidf_mat.toarray()
                self._svd = None

            self._vectorizer = vec
            normed = l2_normalize_rows(dense)
            self._lsa_dim = int(normed.shape[1]) if normed.ndim == 2 and normed.shape[1] > 0 else 1
            return normed, self._lsa_dim

        # Pure-NumPy TF-IDF + SVD fallback
        doc_tokens = [self._extract_ngrams(t) for t in texts]
        df_counts: Counter[str] = Counter()
        for toks in doc_tokens:
            for term in set(toks):
                df_counts[term] += 1

        most_common = [term for term, _ in df_counts.most_common(4096)]
        if not most_common:
            most_common = ["_empty_"]
        vocab = {term: idx for idx, term in enumerate(most_common)}
        n_docs = len(texts)
        n_feat = len(vocab)
        idf = np.ones(n_feat, dtype=np.float32)
        for term, idx in vocab.items():
            idf[idx] = float(math.log((1.0 + n_docs) / (1.0 + df_counts.get(term, 0))) + 1.0)

        tfidf = np.zeros((n_docs, n_feat), dtype=np.float32)
        for d_idx, toks in enumerate(doc_tokens):
            tf_c = Counter(t for t in toks if t in vocab)
            for term, cnt in tf_c.items():
                tfidf[d_idx, vocab[term]] = (1.0 + math.log(float(cnt))) * idf[vocab[term]]
        tfidf = l2_normalize_rows(tfidf)

        n_comp = min(max_dim, n_docs, n_feat)
        if n_comp >= 2 and min(n_docs, n_feat) >= 2:
            u, s, vt = np.linalg.svd(tfidf, full_matrices=False)
            k = min(n_comp, u.shape[1])
            dense = (u[:, :k] * s[:k]).astype(np.float32)
            self._np_vt = vt[:k, :].astype(np.float32)
        else:
            dense = tfidf
            self._np_vt = None

        self._np_vocab = vocab
        self._np_idf = idf
        self._vectorizer = "numpy_lsa"
        normed = l2_normalize_rows(dense)
        self._lsa_dim = int(normed.shape[1]) if normed.ndim == 2 and normed.shape[1] > 0 else 1
        return normed, self._lsa_dim

    def _encode_query_tfidf_svd(self, query_text: str, docs_texts: list[str]) -> np.ndarray:
        """Project `query_text` into the active TF-IDF + SVD latent space."""
        if self._vectorizer is None and docs_texts:
            self._fit_tfidf_svd(docs_texts)
        if self._vectorizer is None:
            return np.zeros(1, dtype=np.float32)

        if self._vectorizer != "numpy_lsa":
            q_tfidf = self._vectorizer.transform([query_text or ""])
            if self._svd is not None:
                q_dense = self._svd.transform(q_tfidf)[0]
            else:
                q_dense = q_tfidf.toarray()[0]
            return l2_normalize_rows(q_dense)

        # Pure-NumPy query projection
        if not self._np_vocab or self._np_idf is None:
            return np.zeros(1, dtype=np.float32)
        q_vec = np.zeros(len(self._np_vocab), dtype=np.float32)
        toks = self._extract_ngrams(query_text or "")
        tf_c = Counter(t for t in toks if t in self._np_vocab)
        for term, cnt in tf_c.items():
            idx = self._np_vocab[term]
            q_vec[idx] = (1.0 + math.log(float(cnt))) * float(self._np_idf[idx])
        q_vec = l2_normalize_rows(q_vec)
        if self._np_vt is not None:
            q_dense = np.dot(self._np_vt, q_vec)
        else:
            q_dense = q_vec
        return l2_normalize_rows(q_dense)

    async def _fetch_openrouter_embeddings(
        self,
        texts: list[str],
        model_name: str = "openai/text-embedding-3-small",
        api_key_override: str | None = None,
    ) -> np.ndarray:
        """Fetch dense embeddings from OpenRouter `/embeddings` endpoint (or `self.embedding_fn`)."""
        if not texts:
            return np.zeros((0, 128), dtype=np.float32)

        if self.embedding_fn is not None:
            raw_vecs = self.embedding_fn(texts, model_name)
            return l2_normalize_rows(np.asarray(raw_vecs, dtype=np.float32))

        api_key = self.db.resolve_openrouter_api_key(api_key_override)
        if not api_key:
            # Fallback to local TF-IDF/SVD if no API key is configured
            mat, _ = self._fit_tfidf_svd(texts)
            return mat

        url = f"{self.app_settings.openrouter_base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model_name, "input": texts}
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenRouter embeddings error {resp.status_code}: {resp.text[:250]}")
        data = resp.json().get("data") or []
        vecs = [row["embedding"] for row in sorted(data, key=lambda r: int(r.get("index", 0)))]
        return l2_normalize_rows(np.asarray(vecs, dtype=np.float32))

    async def build_index(
        self,
        *,
        dataset_id: str | None = None,
        dense_backend: DenseBackend = "tfidf_svd_128",
        embedding_model: str | None = None,
        api_key_override: str | None = None,
    ) -> dict[str, Any]:
        """Index captioned media items into `rag_documents`, `rag_fts` (FTS5), and `rag_vectors`."""
        t0 = time.perf_counter()
        clauses = [
            "m.active_caption IS NOT NULL",
            "TRIM(m.active_caption) != ''",
            "m.curation_state NOT IN ('reject', 'auto_rejected')",
            "m.caption_status != 'needs_manual_caption'",
        ]
        params: list[Any] = []
        if dataset_id and dataset_id.strip():
            clauses.append("m.dataset_id = ?")
            params.append(dataset_id.strip())

        where_sql = " AND ".join(clauses)
        rows = self.db.all(
            f"""
            SELECT m.id AS media_id, m.dataset_id, m.source_filename, m.active_caption,
                   d.slug AS dataset_slug, d.trigger_word AS dataset_trigger
            FROM media_items m
            JOIN datasets d ON d.id = m.dataset_id
            WHERE {where_sql}
            ORDER BY m.id ASC
            """,
            params,
        )

        media_ids = [int(r["media_id"]) for r in rows]
        tags_by_media = self.db.get_tags_for_media_items(media_ids)

        now = time.time()
        doc_ids: list[int] = []
        corpus_texts: list[str] = []

        with self.db.write() as conn:
            if dataset_id and dataset_id.strip():
                conn.execute("DELETE FROM rag_documents WHERE dataset_id = ?", (dataset_id.strip(),))
            else:
                conn.execute("DELETE FROM rag_documents")

            for r in rows:
                mid = int(r["media_id"])
                ds_id = str(r["dataset_id"])
                caption = str(r["active_caption"]).strip()
                stem = f"{r['dataset_slug']}_{mid:04d}"
                trig = str(r.get("dataset_trigger") or "ohwx_subject")

                domains = segment_caption_domains(caption)
                m_tags = tags_by_media.get(mid, [])
                tags_by_cat: dict[str, list[str]] = defaultdict(list)
                flat_tag_names: list[str] = []
                for t in m_tags:
                    tags_by_cat[t.category].append(t.slug)
                    flat_tag_names.append(f"{t.name} {t.slug}")

                tags_text = ", ".join(dict.fromkeys(flat_tag_names))
                c_hash = hashlib.sha1(caption.encode("utf-8")).hexdigest()[:16]
                bucket = int(hashlib.sha1(f"media:{mid}".encode("utf-8")).hexdigest()[:8], 16) % 100
                split = "val" if bucket < 10 else "train"

                cur = conn.execute(
                    """
                    INSERT INTO rag_documents (
                        dataset_id, media_id, stem, trigger_word, full_caption,
                        equipment_text, surroundings_text, position_text, lighting_text,
                        tags_text, tags_json, word_count, content_hash, split, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ds_id,
                        mid,
                        stem,
                        trig,
                        caption,
                        domains["equipment"],
                        domains["surroundings"],
                        domains["position"],
                        domains["lighting"],
                        tags_text,
                        json.dumps(dict(tags_by_cat)),
                        len(caption.split()),
                        c_hash,
                        split,
                        now,
                    ),
                )
                doc_ids.append(int(cur.lastrowid))
                corpus_texts.append(f"{caption} {tags_text}".strip())

        # Compute & store Tier-2 dense vectors
        all_docs = self.db.all("SELECT id, full_caption, tags_text FROM rag_documents ORDER BY id ASC")
        all_doc_ids = [int(d["id"]) for d in all_docs]
        all_texts = [f"{d['full_caption']} {d['tags_text']}".strip() for d in all_docs]

        backend: DenseBackend = (
            dense_backend
            if dense_backend in ("tfidf_svd_128", "openrouter_embedding")
            else "tfidf_svd_128"
        )
        eff_model_name = (
            (embedding_model or "openai/text-embedding-3-small")
            if backend == "openrouter_embedding"
            else "sklearn-lsa-128"
        )

        if all_texts:
            if backend == "openrouter_embedding":
                matrix = await self._fetch_openrouter_embeddings(
                    all_texts,
                    model_name=eff_model_name,
                    api_key_override=api_key_override,
                )
            else:
                matrix, _ = self._fit_tfidf_svd(all_texts, max_dim=128)

            dim = int(matrix.shape[1]) if matrix.ndim == 2 else 1
            with self.db.write() as conn:
                for did, vec in zip(all_doc_ids, matrix):
                    vec_f32 = np.asarray(vec, dtype=np.float32)
                    conn.execute(
                        """
                        INSERT INTO rag_vectors (doc_id, backend, model_name, dim, vector, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(doc_id, backend) DO UPDATE SET
                            model_name = excluded.model_name,
                            dim = excluded.dim,
                            vector = excluded.vector,
                            updated_at = excluded.updated_at
                        """,
                        (did, backend, eff_model_name, dim, vec_f32.tobytes(), now),
                    )

        _, fts_ok = self.db.check_wal_and_fts5()
        elapsed = round(time.perf_counter() - t0, 4)
        return {
            "indexed_documents": len(doc_ids),
            "total_documents": len(all_doc_ids),
            "fts5_ready": fts_ok,
            "dense_backend": backend,
            "model_name": eff_model_name,
            "duration_s": elapsed,
        }

    @staticmethod
    def _mmr_diversify(
        candidate_indices: list[int],
        relevance_scores: np.ndarray,
        vectors: np.ndarray,
        top_k: int,
        diversity: float = 0.7,
        dup_threshold: float = 0.98,
    ) -> list[int]:
        """Maximal Marginal Relevance (MMR) selection balancing relevance and cosine diversity."""
        selected: list[int] = []
        remaining = list(candidate_indices)
        duplicates: list[int] = []
        lam = float(np.clip(diversity, 0.0, 1.0))

        while remaining and len(selected) < top_k:
            if not selected or vectors.shape[1] == 0:
                best = remaining[0]
            else:
                sel_vecs = vectors[selected]
                rem_vecs = vectors[remaining]
                sims = rem_vecs @ sel_vecs.T
                max_sim = sims.max(axis=1)
                is_dup = max_sim > dup_threshold
                duplicates.extend(i for i, d in zip(remaining, is_dup) if d)
                remaining = [i for i, d in zip(remaining, is_dup) if not d]
                if not remaining:
                    break
                max_sim = max_sim[~is_dup]
                mmr_score = lam * relevance_scores[remaining] - (1.0 - lam) * max_sim
                best = remaining[int(np.argmax(mmr_score))]

            selected.append(best)
            if best in remaining:
                remaining.remove(best)

        for d_idx in sorted(duplicates, key=lambda j: -float(relevance_scores[j])):
            if len(selected) >= top_k:
                break
            selected.append(d_idx)
        return selected

    async def retrieve(
        self,
        query: str = "",
        *,
        dataset_id: str | None = None,
        trigger_word: str | None = None,
        training_type: str | None = None,
        tag_filters: dict[str, list[str]] | None = None,
        top_k: int = 4,
        diversity: float = 0.7,
        retrieval_mode: RetrievalMode = "hybrid",
        dense_backend: DenseBackend = "tfidf_svd_128",
        api_key_override: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute Stage-1 FTS5 BM25 + Tag Filtering and Stage-2 Dense Cosine Similarity with RRF + MMR."""
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if dataset_id and dataset_id.strip():
            clauses.append("rd.dataset_id = ?")
            params.append(dataset_id.strip())
        if trigger_word and trigger_word.strip():
            clauses.append("LOWER(rd.trigger_word) = LOWER(?)")
            params.append(trigger_word.strip())
        if training_type and training_type.strip():
            clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM datasets d
                    JOIN skills s ON s.id = d.active_skill_id
                    WHERE d.id = rd.dataset_id AND s.training_type = ?
                )
                """
            )
            params.append(training_type.strip())

        where_sql = " AND ".join(clauses)
        docs = self.db.all(
            f"""
            SELECT rd.*, rv.vector AS dense_blob, rv.dim AS dense_dim
            FROM rag_documents rd
            LEFT JOIN rag_vectors rv ON rv.doc_id = rd.id AND rv.backend = ?
            WHERE {where_sql}
            ORDER BY rd.id ASC
            """,
            [dense_backend, *params],
        )

        # Auto-index if rag_documents is empty but captioned media_items exist
        if not docs:
            captioned_n = int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM media_items WHERE active_caption IS NOT NULL AND TRIM(active_caption) != ''",
                    default=0,
                )
            )
            if captioned_n > 0:
                await self.build_index(dataset_id=dataset_id, dense_backend=dense_backend)
                docs = self.db.all(
                    f"""
                    SELECT rd.*, rv.vector AS dense_blob, rv.dim AS dense_dim
                    FROM rag_documents rd
                    LEFT JOIN rag_vectors rv ON rv.doc_id = rd.id AND rv.backend = ?
                    WHERE {where_sql}
                    ORDER BY rd.id ASC
                    """,
                    [dense_backend, *params],
                )

        if not docs:
            return []

        n_docs = len(docs)

        # ------------------------------------------------------------------
        # 1. Tag Filter & Overlap Scoring
        # ------------------------------------------------------------------
        requested_tags: set[str] = set()
        for _, t_list in (tag_filters or {}).items():
            for t_item in t_list or []:
                if t_item and str(t_item).strip():
                    requested_tags.add(str(t_item).strip().lower())

        tag_scores = np.zeros(n_docs, dtype=np.float32)
        matched_tags_per_doc: list[list[str]] = []

        for i, doc in enumerate(docs):
            try:
                doc_tag_map = json.loads(doc.get("tags_json") or "{}")
            except Exception:
                doc_tag_map = {}
            doc_tags_set = {
                str(slug).lower()
                for slug_list in doc_tag_map.values()
                for slug in (slug_list or [])
            }
            # Also check tags_text and full_caption for tag keyword matches
            caption_lower = f"{doc.get('full_caption', '')} {doc.get('tags_text', '')}".lower()
            matched: list[str] = []
            for req_t in requested_tags:
                if req_t in doc_tags_set or req_t in caption_lower:
                    matched.append(req_t)
            matched_tags_per_doc.append(matched)
            if requested_tags:
                tag_scores[i] = float(len(matched)) / float(len(requested_tags))

        # ------------------------------------------------------------------
        # 2. Stage 1: SQLite FTS5 BM25 Ranking
        # ------------------------------------------------------------------
        bm25_by_doc_id: dict[int, float] = {}
        full_query_str = " ".join([query.strip(), *requested_tags]).strip()
        fts_match = build_fts5_match_query(full_query_str)
        if fts_match and retrieval_mode in ("hybrid", "fts5_only"):
            try:
                fts_rows = self.db.all(
                    """
                    SELECT rowid AS doc_id,
                           bm25(rag_fts, 1.0, 0.6, 0.6, 0.6, 0.6, 0.8) AS raw_bm25
                    FROM rag_fts
                    WHERE rag_fts MATCH ?
                    ORDER BY raw_bm25 ASC
                    LIMIT 250
                    """,
                    (fts_match,),
                )
                for fr in fts_rows:
                    # FTS5 bm25() returns negative numbers where more negative = higher relevance
                    bm25_by_doc_id[int(fr["doc_id"])] = max(0.0001, -float(fr["raw_bm25"]))
            except Exception:
                bm25_by_doc_id = {}

        bm25_scores = np.array(
            [bm25_by_doc_id.get(int(d["id"]), 0.0) for d in docs],
            dtype=np.float32,
        )

        # ------------------------------------------------------------------
        # 3. Stage 2: Dense Cosine Similarity (TF-IDF/SVD LSA or OpenRouter)
        # ------------------------------------------------------------------
        cosine_scores = np.zeros(n_docs, dtype=np.float32)
        doc_texts = [f"{d['full_caption']} {d.get('tags_text', '')}".strip() for d in docs]

        if dense_backend == "openrouter_embedding" and all(d.get("dense_blob") for d in docs):
            dim = int(docs[0]["dense_dim"])
            doc_matrix = np.vstack(
                [np.frombuffer(d["dense_blob"], dtype=np.float32)[:dim] for d in docs]
            )
            if full_query_str and retrieval_mode in ("hybrid", "dense_only"):
                q_mat = await self._fetch_openrouter_embeddings(
                    [full_query_str], api_key_override=api_key_override
                )
                q_vec = q_mat[0]
                if q_vec.shape[0] == doc_matrix.shape[1]:
                    cosine_scores = np.clip(doc_matrix @ q_vec, 0.0, 1.0)
        else:
            # Fit / transform local TF-IDF + TruncatedSVD LSA over candidate corpus
            doc_matrix, _ = self._fit_tfidf_svd(doc_texts, max_dim=128)
            if full_query_str and retrieval_mode in ("hybrid", "dense_only"):
                q_vec = self._encode_query_tfidf_svd(full_query_str, doc_texts)
                if q_vec.shape[0] == doc_matrix.shape[1]:
                    dots = np.array(
                        [cosine_similarity_vec(doc_matrix[i], q_vec) for i in range(n_docs)],
                        dtype=np.float32,
                    )
                    cosine_scores = np.clip(dots, 0.0, 1.0)

        # ------------------------------------------------------------------
        # 4. Reciprocal Rank Fusion (RRF, k=60)
        # ------------------------------------------------------------------
        if retrieval_mode == "fts5_only":
            fused_scores = bm25_scores + 0.25 * tag_scores
        elif retrieval_mode == "dense_only":
            fused_scores = cosine_scores + 0.25 * tag_scores
        else:
            fused = np.zeros(n_docs, dtype=np.float64)
            active_lists = 0
            if requested_tags and np.any(tag_scores > 0):
                active_lists += 1
                t_ranks = competition_ranks(tag_scores)
                fused += np.where(tag_scores > 0, 1.0 / (RRF_K + t_ranks), 0.0)
            if np.any(bm25_scores > 0):
                active_lists += 1
                b_ranks = competition_ranks(bm25_scores)
                fused += np.where(bm25_scores > 0, 1.0 / (RRF_K + b_ranks), 0.0)
            if full_query_str:
                active_lists += 1
                d_ranks = competition_ranks(cosine_scores)
                fused += 1.0 / (RRF_K + d_ranks)

            if active_lists > 0:
                fused_scores = (fused * (RRF_K + 1) / float(active_lists)).astype(np.float32)
            else:
                fused_scores = np.full(n_docs, 0.5, dtype=np.float32)

        # ------------------------------------------------------------------
        # 5. Top-K Selection with MMR Diversification
        # ------------------------------------------------------------------
        k = max(1, min(int(top_k), n_docs))
        pool_indices = list(np.argsort(-fused_scores, kind="stable")[: max(k * 6, 24)])
        if 0.0 < diversity < 1.0 and len(pool_indices) > k:
            chosen_indices = self._mmr_diversify(
                pool_indices,
                fused_scores,
                doc_matrix,
                top_k=k,
                diversity=diversity,
            )
        else:
            chosen_indices = pool_indices[:k]

        results: list[dict[str, Any]] = []
        for idx in chosen_indices:
            doc = docs[idx]
            mid = int(doc["media_id"]) if doc.get("media_id") is not None else None
            results.append(
                {
                    "doc_id": int(doc["id"]),
                    "media_id": mid,
                    "dataset_id": doc.get("dataset_id"),
                    "stem": doc.get("stem"),
                    "trigger_word": doc.get("trigger_word"),
                    "score": round(float(fused_scores[idx]), 4),
                    "bm25_score": round(float(bm25_scores[idx]), 4),
                    "cosine_score": round(float(cosine_scores[idx]), 4),
                    "matched_tags": matched_tags_per_doc[idx],
                    "full_caption": doc["full_caption"],
                    "equipment_text": doc.get("equipment_text", ""),
                    "surroundings_text": doc.get("surroundings_text", ""),
                    "position_text": doc.get("position_text", ""),
                    "lighting_text": doc.get("lighting_text", ""),
                    "thumbnail_url": f"/api/v1/media/{mid}/thumbnail" if mid else None,
                }
            )
        return results
