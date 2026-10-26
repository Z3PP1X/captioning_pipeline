"""SQLite FTS5 RAG and MOSS Prompt-Expander Engine.

Provides mathematically deterministic, ML-free BM25 full-text indexing and retrieval
over multi-tier MOSS captions, optional NumPy cosine similarity re-ranking, and
3-tier prompt expansion (short, medium, long) adhering to visual disentanglement rules.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
import numpy as np

from pipeline.db import DB
from pipeline.moss_validator import (
    extract_json_object,
    sanitize_leaks,
    validate_and_inject_triggers,
)
from pipeline.prompts.moss_prompts import normalize_moss_mode

logger = logging.getLogger(__name__)

MOSS_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS moss_captions_fts USING fts5(
    frame_id UNINDEXED,
    video_id,
    trigger,
    mode,
    long,
    medium,
    short,
    tags,
    tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS moss_captions_meta (
    frame_id INTEGER PRIMARY KEY,
    video_id TEXT,
    trigger TEXT,
    mode TEXT,
    long TEXT,
    medium TEXT,
    short TEXT,
    tags TEXT,
    embedding BLOB,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_moss_captions_meta_video ON moss_captions_meta(video_id);
CREATE INDEX IF NOT EXISTS ix_moss_captions_meta_mode ON moss_captions_meta(mode);
"""


def ensure_moss_tables(db_or_conn: DB | Any) -> None:
    """Ensures SQLite FTS5 and metadata tables for MOSS captions exist."""
    if hasattr(db_or_conn, "write"):
        with db_or_conn.write() as conn:
            conn.executescript(MOSS_FTS_SCHEMA)
    elif hasattr(db_or_conn, "executescript"):
        db_or_conn.executescript(MOSS_FTS_SCHEMA)


def sanitize_fts5_query(query: str, connector: str = "OR") -> str:
    """Extracts alphanumeric tokens and quotes them to safely prevent FTS5 syntax errors."""
    tokens = re.findall(r"\w+", query or "")
    if not tokens:
        return ""
    safe_tokens = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return f" {connector} ".join(safe_tokens)


def _serialize_embedding(embedding: bytes | np.ndarray | None) -> bytes | None:
    if embedding is None:
        return None
    if isinstance(embedding, np.ndarray):
        return embedding.astype(np.float32).tobytes()
    return bytes(embedding)


def index_moss_caption(  # noqa: PLR0913, PLR0917
    db: DB,
    frame_id: int,
    video_id: str,
    trigger: str,
    mode: str,
    long: str,
    medium: str,
    short: str,
    tags: list[str],
    embedding: bytes | np.ndarray | None = None,
) -> None:
    """Indexes a single MOSS multi-tier caption in SQLite FTS5 and metadata store."""
    ensure_moss_tables(db)
    tags_str = " ".join(tags) if isinstance(tags, (list, tuple, set)) else str(tags or "")
    tags_json = json.dumps(list(tags)) if isinstance(tags, (list, tuple, set)) else str(tags or "[]")
    emb_blob = _serialize_embedding(embedding)
    now = time.time()

    with db.write() as conn:
        conn.execute("DELETE FROM moss_captions_fts WHERE frame_id = ?", (frame_id,))
        conn.execute(
            """
            INSERT INTO moss_captions_fts (frame_id, video_id, trigger, mode, long, medium, short, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (frame_id, video_id, trigger, mode, long, medium, short, tags_str),
        )
        conn.execute(
            """
            INSERT INTO moss_captions_meta (frame_id, video_id, trigger, mode, long, medium, short, tags, embedding, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(frame_id) DO UPDATE SET
                video_id=excluded.video_id,
                trigger=excluded.trigger,
                mode=excluded.mode,
                long=excluded.long,
                medium=excluded.medium,
                short=excluded.short,
                tags=excluded.tags,
                embedding=coalesce(excluded.embedding, moss_captions_meta.embedding),
                updated_at=excluded.updated_at
            """,
            (frame_id, video_id, trigger, mode, long, medium, short, tags_json, emb_blob, now),
        )


def compute_cosine_similarities(query_vec: np.ndarray, doc_vectors: np.ndarray) -> np.ndarray:
    """Vectorized cosine similarity between a 1D query vector and 2D matrix of document vectors."""
    q_norm = float(np.linalg.norm(query_vec))
    if q_norm == 0.0 or doc_vectors.shape[0] == 0:
        return np.zeros(doc_vectors.shape[0], dtype=np.float32)

    q_unit = (query_vec / q_norm).astype(np.float32)
    d_norms = np.linalg.norm(doc_vectors, axis=1, keepdims=True).astype(np.float32)
    d_norms = np.where(d_norms == 0.0, 1.0, d_norms)
    d_unit = doc_vectors / d_norms
    return np.dot(d_unit, q_unit).astype(np.float32)


def _build_fts_query_sql(has_video: bool, has_mode: bool) -> str:
    clauses = ["moss_captions_fts MATCH ?"]
    if has_video:
        clauses.append("video_id = ?")
    if has_mode:
        clauses.append("mode = ?")
    where_sql = " AND ".join(clauses)
    return f"""
        SELECT frame_id, video_id, trigger, mode, long, medium, short, tags,
               bm25(moss_captions_fts) as rank
        FROM moss_captions_fts
        WHERE {where_sql}
        ORDER BY rank ASC
        LIMIT ?
    """


def _parse_tags(raw_tags: Any) -> list[str]:
    if isinstance(raw_tags, (list, tuple)):
        return [str(t) for t in raw_tags]
    if isinstance(raw_tags, str):
        try:
            parsed = json.loads(raw_tags)
            if isinstance(parsed, list):
                return [str(t) for t in parsed]
        except Exception:
            pass
        return [t.strip() for t in raw_tags.split() if t.strip()]
    return []


def _row_to_exemplar(row: dict[str, Any], default_score: float = 1.0) -> dict[str, Any]:
    score = float(abs(row["rank"])) if "rank" in row and row["rank"] is not None else default_score
    return {
        "frame_id": int(row["frame_id"]),
        "video_id": str(row.get("video_id") or ""),
        "trigger": str(row.get("trigger") or ""),
        "mode": str(row.get("mode") or ""),
        "long": str(row.get("long") or ""),
        "medium": str(row.get("medium") or ""),
        "short": str(row.get("short") or ""),
        "tags": _parse_tags(row.get("tags")),
        "score": round(score, 6),
    }


def _query_fts_candidates(
    db: DB,
    safe_query: str,
    top_k: int,
    video_id: str | None,
    mode: str | None,
) -> list[dict[str, Any]]:
    sql = _build_fts_query_sql(bool(video_id), bool(mode))
    params: list[Any] = [safe_query]
    if video_id:
        params.append(video_id)
    if mode:
        params.append(mode)
    params.append(top_k)
    return db.all(sql, tuple(params))


def _cosine_search_meta(
    db: DB,
    query_vec: np.ndarray,
    top_k: int,
    video_id: str | None,
    mode: str | None,
) -> list[dict[str, Any]]:
    where_parts = ["embedding IS NOT NULL"]
    params: list[Any] = []
    if video_id:
        where_parts.append("video_id = ?")
        params.append(video_id)
    if mode:
        where_parts.append("mode = ?")
        params.append(mode)
    where_sql = " AND ".join(where_parts)
    rows = db.all(
        f"SELECT frame_id, video_id, trigger, mode, long, medium, short, tags, embedding FROM moss_captions_meta WHERE {where_sql}",
        tuple(params),
    )
    if not rows:
        return []

    vectors: list[np.ndarray] = []
    valid_rows: list[dict[str, Any]] = []
    for r in rows:
        blob = r.get("embedding")
        if blob:
            arr = np.frombuffer(blob, dtype=np.float32)
            if arr.ndim == 1 and arr.shape[0] == query_vec.shape[0]:
                vectors.append(arr)
                valid_rows.append(r)

    if not valid_rows:
        return []

    doc_matrix = np.stack(vectors, axis=0)
    sims = compute_cosine_similarities(query_vec, doc_matrix)
    order = np.argsort(-sims)[:top_k]

    results: list[dict[str, Any]] = []
    for idx in order:
        item = _row_to_exemplar(valid_rows[int(idx)], default_score=float(sims[int(idx)]))
        item["score"] = round(float(sims[int(idx)]), 6)
        results.append(item)
    return results


def retrieve_moss_exemplars(
    db: DB,
    query_text: str = "",
    top_k: int = 3,
    video_id: str | None = None,
    mode: str | None = None,
    query_embedding: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Retrieves top-k structurally most similar captions via FTS5 BM25 with NumPy cosine fallback."""
    ensure_moss_tables(db)
    safe_query = sanitize_fts5_query(query_text)

    # 1. Primary FTS5 BM25 retrieval
    results: list[dict[str, Any]] = []
    if safe_query:
        candidates = _query_fts_candidates(db, safe_query, top_k, video_id, mode)
        results = [_row_to_exemplar(c) for c in candidates]

    # 2. Embedding enhancement / fallback
    if query_embedding is not None and query_embedding.ndim == 1:
        if not results:
            return _cosine_search_meta(db, query_embedding, top_k, video_id, mode)
        results = _rerank_with_embeddings(db, results, query_embedding)

    # 3. Final fallback: recent exemplars if BM25 and embeddings returned empty
    if not results:
        fallback_rows = db.all(
            "SELECT frame_id, video_id, trigger, mode, long, medium, short, tags FROM moss_captions_meta ORDER BY frame_id DESC LIMIT ?",
            (top_k,),
        )
        results = [_row_to_exemplar(r, default_score=0.1) for r in fallback_rows]

    return results[:top_k]


def _rerank_with_embeddings(
    db: DB,
    results: list[dict[str, Any]],
    query_embedding: np.ndarray,
) -> list[dict[str, Any]]:
    frame_ids = [r["frame_id"] for r in results]
    placeholders = ",".join("?" for _ in frame_ids)
    meta_rows = db.all(
        f"SELECT frame_id, embedding FROM moss_captions_meta WHERE frame_id IN ({placeholders})",
        tuple(frame_ids),
    )
    emb_map = {m["frame_id"]: m.get("embedding") for m in meta_rows}

    updated: list[dict[str, Any]] = []
    for r in results:
        blob = emb_map.get(r["frame_id"])
        if blob:
            arr = np.frombuffer(blob, dtype=np.float32)
            if arr.ndim == 1 and arr.shape[0] == query_embedding.shape[0]:
                cos_sim = float(compute_cosine_similarities(query_embedding, arr[np.newaxis, :])[0])
                r["score"] = round(0.5 * r["score"] + 0.5 * max(0.0, cos_sim), 6)
        updated.append(r)
    return updated


def _normalize_subject_idea(idea: str, class_noun: str) -> str:
    cleaned = (idea or "").strip()
    noun = (class_noun or "").strip().lower()
    patterns = [
        rf"^(?:a|an|the)\s+{re.escape(noun)}\s+(?:who\s+is\s+|that\s+is\s+|is\s+)?",
        rf"^{re.escape(noun)}\s+(?:who\s+is\s+|that\s+is\s+|is\s+)?",
        r"^(?:a|an|the)\s+(?:person|character|figure|subject|man|woman|girl|boy)\s+(?:who\s+is\s+|that\s+is\s+|is\s+)?",
    ]
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned.rstrip(".,; ")


def _resolve_subject_idea(clean_idea: str, class_noun: str) -> str:
    noun = (class_noun or "").strip()
    idea = clean_idea.strip()
    if noun and re.search(rf"\b{re.escape(noun)}\b", idea, re.IGNORECASE):
        return re.sub(r"^(?:a|an|the)\s+", "", idea, flags=re.IGNORECASE).strip()
    norm = _normalize_subject_idea(idea, noun)
    return f"{noun} {norm}" if noun else norm


def _extract_exemplar_cadence(top_exemplar: dict[str, Any] | None) -> dict[str, str]:
    if not top_exemplar:
        return {"lighting": "", "camera": "", "setting": ""}

    text = f"{top_exemplar.get('medium', '')} {top_exemplar.get('long', '')}"
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]

    lighting = next((s for s in sentences if re.search(r"\b(light|lighting|shadow|sunlight|illumination)\b", s, re.I)), "")
    camera = next((s for s in sentences if re.search(r"\b(angle|shot|depth of field|perspective|view|framing)\b", s, re.I)), "")
    setting = next((s for s in sentences if re.search(r"\b(background|setting|environment|room|outdoor|indoor)\b", s, re.I)), "")

    return {"lighting": lighting, "camera": camera, "setting": setting}


def _ensure_word_budget(text: str, min_words: int, max_words: int, filler: str) -> str:
    words = text.split()
    while len(words) < min_words:
        text = text.rstrip(". ") + ". " + filler
        words = text.split()
    if len(words) > max_words:
        parts = text.split(". ")
        cur: list[str] = []
        for p in parts:
            if len(" ".join(cur + [p]).split()) <= max_words:
                cur.append(p)
            else:
                break
        text = ". ".join(cur).rstrip(".") + "."
    return text


def _synthesize_character_tiers(
    subject_idea: str,
    cadence: dict[str, str],
) -> dict[str, str]:
    short_content = f"a {subject_idea}, captured in a natural setting under balanced ambient lighting."
    short_text = _ensure_word_budget(short_content, 10, 22, "The setting is clear and well-composed.")

    setting_c = cadence.get("setting") or "The backdrop reveals ambient spatial depth and architectural surfaces under soft diffused illumination."
    med_content = (
        f"A {subject_idea}. "
        f"She is styled in tailored attire featuring textured fabrics and clean seam stitching. "
        f"{setting_c} "
        f"The visual composition is framed with clarity and focal balance."
    )
    med_text = _ensure_word_budget(med_content, 40, 75, "Natural highlights outline the form with gentle tonal gradations.")

    light_c = cadence.get("lighting") or "Directional illumination produces soft highlights along the contours and gentle diffused shadows."
    cam_c = cadence.get("camera") or "Captured from an eye-level medium angle with a balanced depth of field, keeping the subject in sharp focus."
    long_content = (
        f"A {subject_idea}, presented in a thoughtfully structured visual composition. "
        f"She is wearing carefully fitted layered clothing, showcasing visible textile weave, delicate edge stitching, and natural fabric folds. "
        f"Her physical posture is composed and steady, with hands resting naturally and a focused, serene facial expression directed forward. "
        f"{setting_c} "
        f"{light_c} "
        f"{cam_c} "
        f"The surrounding scene preserves natural proportion, crisp edge fidelity, and subtle atmospheric depth across the entire frame."
    )
    long_text = _ensure_word_budget(long_content, 110, 220, "Fine surface textures and balanced tones complete the composed frame.")

    return {"short": short_text, "medium": med_text, "long": long_text}


def _synthesize_style_tiers(
    subject_idea: str,
    cadence: dict[str, str],
) -> dict[str, str]:
    short_content = f"literal physical arrangement of {subject_idea} within a defined architectural space."
    short_text = _ensure_word_budget(short_content, 10, 22, "Physical illumination illuminates the layout.")

    med_content = (
        f"A literal physical view of {subject_idea}. "
        f"Physical boundaries, solid materials, and planar surfaces are distributed across the foreground and midground geometry. "
        f"Directional physical light enters from an elevated angle, casting distinct shadows across physical ground planes with geometric clarity."
    )
    med_text = _ensure_word_budget(med_content, 40, 75, "Horizontal and vertical structural lines define spatial boundaries.")

    long_content = (
        f"A literal objective inventory of {subject_idea}, documenting solely physical subjects, geometry, and spatial distribution. "
        f"The physical environment comprises structural surfaces, concrete or wood boundaries, and planar arrangements with measurable distance. "
        f"Objects and structures occupy distinct coordinates across the floor plane, displaying literal material edges, surface textures, and mass. "
        f"Physical illumination originates from a consistent lateral source, producing measurable cast shadows and tonal gradients along edges. "
        f"Camera positioning remains perpendicular at eye level, presenting an objective focal perspective without optical distortion. "
        f"No artistic mood, rendering treatment, or stylized descriptors are included in this objective structural documentation."
    )
    long_text = _ensure_word_budget(long_content, 110, 220, "Physical objects remain situated in balanced structural orientation.")

    return {"short": short_text, "medium": med_text, "long": long_text}


def _synthesize_concept_tiers(
    subject_idea: str,
    cadence: dict[str, str],
) -> dict[str, str]:
    short_content = f"the {subject_idea}, situated prominently within an articulate environmental scene."
    short_text = _ensure_word_budget(short_content, 10, 22, "The object exhibits defined form and placement.")

    med_content = (
        f"The {subject_idea}. "
        f"The target object is positioned with defined scale and orientation, showing physical interaction with adjacent supporting surfaces. "
        f"The surrounding background features detailed environment elements illuminated by clean, balanced directional lighting."
    )
    med_text = _ensure_word_budget(med_content, 40, 75, "Surface contours and physical volume stand out distinctly against the setting.")

    long_content = (
        f"The {subject_idea}, decoupled from surrounding environmental context and stylistic rendering. "
        f"The object maintains distinct spatial orientation, displaying defined outer contours, relative scale, and physical contact with nearby surfaces. "
        f"Surrounding scenery includes structured architectural elements and background props positioned to decouple the core concept. "
        f"The scene is rendered with balanced directional illumination, casting soft contact shadows and clear edge separation. "
        f"Compositional framing captures the target object from an unobstructed three-quarter perspective with controlled depth of field. "
        f"Visual textures, material reflections, and surrounding environment details decouple intrinsic identity from transient scene context."
    )
    long_text = _ensure_word_budget(long_content, 110, 220, "Controlled lighting and clear boundaries define the complete composition.")

    return {"short": short_text, "medium": med_text, "long": long_text}


def _synthesize_tiers_by_mode(
    mode: str,
    subject_idea: str,
    cadence: dict[str, str],
) -> dict[str, str]:
    norm_m = normalize_moss_mode(mode)
    if norm_m == "aesthetic_style":
        return _synthesize_style_tiers(subject_idea, cadence)
    if norm_m == "object_concept":
        return _synthesize_concept_tiers(subject_idea, cadence)
    return _synthesize_character_tiers(subject_idea, cadence)


def _expand_deterministic(
    idea: str,
    exemplars: list[dict[str, Any]],
    mode: str,
    trigger_phrase: str,
    class_noun: str,
    locked_traits: list[str],
) -> dict[str, str]:
    clean_idea = sanitize_leaks(idea, locked_traits)
    subject_idea = _resolve_subject_idea(clean_idea, class_noun) or "in a serene and composed scene"

    top_exemplar = exemplars[0] if exemplars else None
    cadence = _extract_exemplar_cadence(top_exemplar)

    # Sanitize cadence components as well
    for k in cadence:
        cadence[k] = sanitize_leaks(cadence[k], locked_traits)

    tiers = _synthesize_tiers_by_mode(mode, subject_idea, cadence)

    # Sanitize and inject triggers
    for k in tiers:
        tiers[k] = sanitize_leaks(tiers[k], locked_traits)

    payload = {"caption": tiers}
    validated, _ = validate_and_inject_triggers(
        payload, trigger_phrase=trigger_phrase, class_noun=class_noun
    )
    cap = validated.get("caption", tiers)
    return {k: str(cap.get(k, "")) for k in ("long", "medium", "short")}


def _build_openrouter_payload(
    idea: str,
    exemplars: list[dict[str, Any]],
    mode: str,
    trigger_phrase: str,
    class_noun: str,
    locked_traits: list[str],
) -> dict[str, Any]:
    traits_desc = ", ".join(locked_traits) if locked_traits else "None specified"
    system_prompt = (
        f"You are an expert MOSS prompt expansion engine.\n"
        f"Mode: {mode}\nClass Noun: {class_noun}\nTrigger Phrase: {trigger_phrase}\n"
        f"LOCKED TRAITS (STRICT EXCLUSION): [{traits_desc}]\n"
        f"CRITICAL: Never mention any locked traits in any caption tier.\n"
        f"Expand the user's idea into a 3-tier caption matching JSON schema:\n"
        f'{{"caption": {{"long": "110-220 words", "medium": "40-75 words", "short": "10-22 words"}}}}\n'
        f"Prepend the trigger phrase '{trigger_phrase}' if provided."
    )

    exemplar_demos = [
        {
            "long": ex.get("long", ""),
            "medium": ex.get("medium", ""),
            "short": ex.get("short", ""),
        }
        for ex in exemplars[:3]
    ]

    user_msg = (
        f"Demonstration exemplars from dataset:\n{json.dumps(exemplar_demos, indent=2)}\n\n"
        f"User idea to expand: {idea}\n"
        f"Respond ONLY with the JSON object."
    )

    return {
        "model": "google/gemini-2.5-flash",
        "temperature": 0.2,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
    }


def _expand_via_openrouter(  # noqa: PLR0913, PLR0917
    idea: str,
    exemplars: list[dict[str, Any]],
    mode: str,
    trigger_phrase: str,
    class_noun: str,
    locked_traits: list[str],
    openrouter_key: str,
) -> dict[str, str]:
    payload = _build_openrouter_payload(idea, exemplars, mode, trigger_phrase, class_noun, locked_traits)
    headers = {
        "Authorization": f"Bearer {openrouter_key.strip()}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)
    validated, _ = validate_and_inject_triggers(parsed, trigger_phrase=trigger_phrase, class_noun=class_noun)
    cap = validated.get("caption", {})
    return {
        "long": sanitize_leaks(str(cap.get("long", "")), locked_traits),
        "medium": sanitize_leaks(str(cap.get("medium", "")), locked_traits),
        "short": sanitize_leaks(str(cap.get("short", "")), locked_traits),
    }


def expand_moss_prompt(  # noqa: PLR0913, PLR0917
    db: DB,
    idea: str,
    video_id_or_dataset: str | None,
    mode: str = "CHARACTER",
    trigger_phrase: str = "",
    class_noun: str = "woman",
    locked_traits: list[str] | None = None,
    use_llm: bool = False,
    openrouter_key: str | None = None,
) -> dict[str, Any]:
    """Expands a concise user idea into a 3-tier MOSS prompt via retrieval and synthesis."""
    ensure_moss_tables(db)
    traits = list(locked_traits or [])

    # Step 1: Retrieval (top 3 structurally most similar captions)
    exemplars = retrieve_moss_exemplars(
        db=db,
        query_text=idea,
        top_k=3,
        video_id=video_id_or_dataset,
        mode=mode,
    )
    if not exemplars and video_id_or_dataset:
        exemplars = retrieve_moss_exemplars(db=db, query_text=idea, top_k=3, mode=mode)

    # Step 2: Synthesis
    expansion_type = "deterministic"
    tiers: dict[str, str] | None = None

    if use_llm and openrouter_key:
        try:
            tiers = _expand_via_openrouter(
                idea=idea,
                exemplars=exemplars,
                mode=mode,
                trigger_phrase=trigger_phrase,
                class_noun=class_noun,
                locked_traits=traits,
                openrouter_key=openrouter_key,
            )
            expansion_type = "openrouter"
        except Exception as ex:
            logger.warning("OpenRouter prompt expansion failed, falling back to deterministic: %s", ex)
            tiers = None

    if tiers is None:
        tiers = _expand_deterministic(
            idea=idea,
            exemplars=exemplars,
            mode=mode,
            trigger_phrase=trigger_phrase,
            class_noun=class_noun,
            locked_traits=traits,
        )

    return {
        "prompt": tiers,
        "exemplars": [
            {
                "frame_id": ex["frame_id"],
                "long": ex["long"],
                "medium": ex["medium"],
                "short": ex["short"],
                "score": float(ex["score"]),
            }
            for ex in exemplars
        ],
        "mode": mode,
        "trigger_phrase": trigger_phrase,
        "expansion_type": expansion_type,
    }


__all__ = [
    "MOSS_FTS_SCHEMA",
    "compute_cosine_similarities",
    "ensure_moss_tables",
    "expand_moss_prompt",
    "index_moss_caption",
    "retrieve_moss_exemplars",
    "sanitize_fts5_query",
]
