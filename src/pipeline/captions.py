"""`captions` table helpers (Phase G): the caption source of truth, cost accounting and stats.

Writers: the caption job (`source='vlm'`, always wins), reconcile/exporter (`'imported'` for pre-existing
VLM `.txt` files, never replacing a VLM row) and the exporter (`'stub'` tag placeholders, only when the
frame has no caption row at all). The `.txt` files under `finished/` are written from these rows.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from pipeline.db import DB

CAPTION_SOURCES = ("vlm", "imported", "stub")
# Rows that count as a real caption (a stub is only a placeholder the caption job replaces)
ACCEPTED_SOURCES = ("vlm", "imported")

_COLUMNS = (
    "frame_id", "video_id", "text", "model", "layer", "mode", "prompt_version", "trigger", "word_count",
    "cost_usd", "prompt_tokens", "completion_tokens", "warnings", "finish_reason", "job_id", "created_at", "source",
)


def _first_token(text: str) -> str | None:
    words = (text or "").split()
    return words[0].strip(",.:;") if words else None


def _row_values(frame_id: int, video_id: str | None, text: str, source: str, **meta: Any) -> tuple:
    warnings = meta.get("warnings")
    values = {
        "frame_id": frame_id,
        "video_id": video_id,
        "text": text,
        "model": meta.get("model"),
        "layer": meta.get("layer"),
        "mode": meta.get("mode"),
        "prompt_version": meta.get("prompt_version"),
        "trigger": meta.get("trigger") or _first_token(text),
        "word_count": len((text or "").split()),
        "cost_usd": meta.get("cost_usd"),
        "prompt_tokens": meta.get("prompt_tokens"),
        "completion_tokens": meta.get("completion_tokens"),
        "warnings": json.dumps(list(warnings)) if warnings is not None else None,
        "finish_reason": meta.get("finish_reason"),
        "job_id": meta.get("job_id"),
        "created_at": meta.get("created_at") or time.time(),
        "source": source,
    }
    return tuple(values[c] for c in _COLUMNS)


_INSERT = f"INSERT INTO captions ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' for _ in _COLUMNS)})"
_UPDATE_ALL = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "frame_id")


def record_caption(conn: sqlite3.Connection, frame_id: int, video_id: str | None, text: str, **meta: Any) -> None:
    """Upsert a VLM caption (source 'vlm'): replaces whatever the frame had."""
    conn.execute(f"{_INSERT} ON CONFLICT(frame_id) DO UPDATE SET {_UPDATE_ALL}",
                 _row_values(frame_id, video_id, text, "vlm", **meta))


def import_caption(
    conn: sqlite3.Connection, frame_id: int, video_id: str | None, text: str, source: str = "imported", **meta: Any
) -> bool:
    """Idempotent import. 'imported' fills an empty slot or replaces a stub; 'stub' only fills an empty slot.

    Returns True when a row was inserted or changed.
    """
    if source not in ("imported", "stub"):
        raise ValueError(f"import_caption source must be 'imported' or 'stub', not {source!r}")
    text = (text or "").strip()
    if not text:
        return False
    if source == "stub":
        cur = conn.execute(f"{_INSERT} ON CONFLICT(frame_id) DO NOTHING", _row_values(frame_id, video_id, text, "stub", **meta))
    else:
        cur = conn.execute(
            f"{_INSERT} ON CONFLICT(frame_id) DO UPDATE SET {_UPDATE_ALL} WHERE captions.source = 'stub'",
            _row_values(frame_id, video_id, text, "imported", **meta),
        )
    return cur.rowcount > 0


def delete_stub(conn: sqlite3.Connection, frame_id: int) -> None:
    conn.execute("DELETE FROM captions WHERE frame_id=? AND source='stub'", (frame_id,))


def _decode(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    raw = out.get("warnings")
    if isinstance(raw, str):
        try:
            out["warnings"] = json.loads(raw)
        except ValueError:
            out["warnings"] = [raw]
    elif raw is None:
        out["warnings"] = []
    return out


def get_caption(db: DB, frame_id: int) -> dict | None:
    return _decode(db.one("SELECT * FROM captions WHERE frame_id=?", (frame_id,)))


def get_accepted_caption(db: DB, frame_id: int) -> dict | None:
    """The frame's real caption (vlm or imported), or None when it has none or only a stub."""
    return _decode(db.one(
        f"SELECT * FROM captions WHERE frame_id=? AND source IN ({', '.join('?' for _ in ACCEPTED_SOURCES)})",
        (frame_id, *ACCEPTED_SOURCES),
    ))


def caption_stats(db: DB, video_id: str | None = None, job_id: int | None = None) -> dict[str, Any]:
    """Totals by source, by model (+layer) and by prompt version, with token and cost sums."""
    where, args = [], []
    if video_id:
        where.append("video_id=?")
        args.append(video_id)
    if job_id is not None:
        where.append("job_id=?")
        args.append(job_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    targs = tuple(args)

    totals = db.one(
        f"""
        SELECT COUNT(*) AS total_captions,
               COALESCE(SUM(cost_usd), 0) AS total_cost_usd,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               AVG(CASE WHEN source != 'stub' THEN word_count END) AS avg_words
        FROM captions {clause}
        """,
        targs,
    ) or {}
    by_source = {s: 0 for s in CAPTION_SOURCES}
    for r in db.all(f"SELECT source, COUNT(*) AS n FROM captions {clause} GROUP BY source", targs):
        by_source[r["source"]] = r["n"]
    by_model = db.all(
        f"""
        SELECT COALESCE(model, '') AS model, COALESCE(layer, '') AS layer, source, COUNT(*) AS count,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
               AVG(word_count) AS avg_words
        FROM captions {clause}
        GROUP BY COALESCE(model, ''), COALESCE(layer, ''), source
        ORDER BY count DESC
        """,
        targs,
    )
    by_prompt = db.all(
        f"""
        SELECT prompt_version, COUNT(*) AS count FROM captions
        {clause + ' AND' if clause else 'WHERE'} prompt_version IS NOT NULL
        GROUP BY prompt_version ORDER BY count DESC
        """,
        targs,
    )
    avg = totals.get("avg_words")
    return {
        "total_captions": int(totals.get("total_captions") or 0),
        "total_cost_usd": round(float(totals.get("total_cost_usd") or 0.0), 6),
        "prompt_tokens": int(totals.get("prompt_tokens") or 0),
        "completion_tokens": int(totals.get("completion_tokens") or 0),
        "avg_words": round(float(avg), 1) if avg is not None else None,
        "by_source": by_source,
        "by_model": [
            {**r, "cost_usd": round(float(r["cost_usd"] or 0.0), 6),
             "avg_words": round(float(r["avg_words"]), 1) if r["avg_words"] is not None else None}
            for r in by_model
        ],
        "by_prompt_version": by_prompt,
    }
