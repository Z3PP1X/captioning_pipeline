"""R10: retrieval eval on the held-out val split, so retrieval changes can be compared with numbers.

Queries are the index's ``val`` captions (deterministic ~10% hash split, see ``indexer.split_for_key``; the
curated_2500 folders keep their own split). Each query's description is the first ``query_words`` words of
its own caption, sent like a user description (no selected tags), and ranked with ``RagRetriever.score``,
i.e. the fused tag/dense/BM25 ranking *before* MMR re-ranking and source capping (those deliberately trade
relevance for variety). Candidates are the non-val captions only; the query itself is never a candidate.

Metrics (means over the queries that have relevant items, all in 0..1):
- ``siblings``: relevant = train captions from the query's source video (captions-table video id, else the
  file-stem prefix). ``recall_at_k`` = |relevant in top k| / min(k, |relevant|); ``hit_at_k`` = at least one
  in the top k; ``mrr`` = mean 1/rank of the first relevant caption over the full ranking;
  ``chance_recall_at_k`` = the expected recall@k of a random ranking (for scale).
- ``tags``: the query's own taxonomy tags (all 4 categories). ``recall_at_k`` = share of them carried by at
  least one top-k caption; ``precision_at_k`` = mean share of a top-k caption's tags that the query has.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from pipeline.rag.retriever import RagRetriever

TAG_KEYS = ("equipment_tags", "surroundings_tags", "position_tags", "camera_lighting_tags")
EVAL_SPLIT = "val"


def _tags(caption: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in TAG_KEYS:
        out |= set(caption.get(key) or ())
    return out


def query_text(caption: str, query_words: int) -> str:
    words = caption.split()
    return " ".join(words[:query_words]) if query_words > 0 else caption


def _mean(values: list[float]) -> float | None:
    return round(float(np.mean(values)), 4) if values else None


def evaluate_retrieval(
    retriever: RagRetriever,
    k: int = 4,
    max_queries: int | None = 200,
    fusion: str = "rrf",
    query_words: int = 40,
    dense_mode: str | None = None,
) -> dict[str, Any]:
    """Retrieval metrics of the loaded index (raises RagIndexNotReady when there is none).

    ``dense_mode`` (R1): ``whole`` | ``domain`` | None (retriever default); the result reports the mode that
    actually ran (``domain`` falls back to ``whole`` for an index without domain vectors).
    """
    start = time.time()
    k = max(1, int(k))
    captions, _ = retriever.load()
    dense_mode = retriever.effective_dense_mode(dense_mode)
    is_candidate = np.array([c["split"] != EVAL_SPLIT for c in captions], dtype=bool)
    query_idx = [i for i, c in enumerate(captions) if c["split"] == EVAL_SPLIT]  # id order = deterministic
    if max_queries is not None and max_queries > 0:
        query_idx = query_idx[:max_queries]
    sources = np.array([c["source_id"] or "" for c in captions], dtype=object)
    cand_tags = [_tags(c) for c in captions]

    sib_recall: list[float] = []
    sib_hit: list[float] = []
    sib_rr: list[float] = []
    sib_chance: list[float] = []
    tag_recall: list[float] = []
    tag_precision: list[float] = []

    n_candidates = int(is_candidate.sum())
    for qi in query_idx:
        q = captions[qi]
        _, _, scores, _ = retriever.score(
            {}, query_text(q["full_caption"], query_words), fusion=fusion, dense_mode=dense_mode
        )
        mask = is_candidate.copy()
        mask[qi] = False
        cand = np.flatnonzero(mask)
        if cand.size == 0:
            continue
        ranked = cand[np.argsort(-scores[cand], kind="stable")]
        top = ranked[:k]

        relevant = (sources[ranked] == (q["source_id"] or "")) if q["source_id"] else np.zeros(len(ranked), bool)
        n_rel = int(relevant.sum())
        if n_rel:
            hits = int(relevant[:k].sum())
            sib_recall.append(hits / min(k, n_rel))
            sib_hit.append(1.0 if hits else 0.0)
            sib_rr.append(1.0 / (int(np.argmax(relevant)) + 1))
            sib_chance.append(min(k, len(ranked)) * n_rel / len(ranked) / min(k, n_rel))

        q_tags = _tags(q)
        if q_tags:
            covered: set[str] = set()
            precisions: list[float] = []
            for i in top:
                covered |= cand_tags[i] & q_tags
                precisions.append(len(cand_tags[i] & q_tags) / len(cand_tags[i]) if cand_tags[i] else 0.0)
            tag_recall.append(len(covered) / len(q_tags))
            tag_precision.append(float(np.mean(precisions)) if precisions else 0.0)

    return {
        "k": k,
        "fusion": fusion,
        "dense_mode": dense_mode,
        "query_words": query_words,
        "stage": "ranking (before MMR / max_per_source)",
        "split": {"val": int((~is_candidate).sum()), "candidates": n_candidates},
        "queries": len(query_idx),
        "siblings": {
            "queries": len(sib_recall),
            "recall_at_k": _mean(sib_recall),
            "hit_at_k": _mean(sib_hit),
            "mrr": _mean(sib_rr),
            "chance_recall_at_k": _mean(sib_chance),
        },
        "tags": {
            "queries": len(tag_recall),
            "recall_at_k": _mean(tag_recall),
            "precision_at_k": _mean(tag_precision),
        },
        "duration": round(time.time() - start, 3),
    }
