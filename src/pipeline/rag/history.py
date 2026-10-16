"""Prompt Director history + favourites (``rag_history`` table in the main state DB).

Lives in ``state.db`` (``Settings.db_path``) rather than the RAG index DB because it is user work that must
survive deleting or rebuilding the index. It uses its own short-lived connections (WAL-safe alongside
``pipeline.db.DB``) and creates its table lazily, so ``db.py`` needs no change.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pipeline.rag.linter import lint_prompt

ARCHETYPE_LINT_MODES = {"a": "tags", "b": "short", "c": "dense"}
# Non-favourite entries beyond this many (newest first) are pruned on insert; favourites are kept forever.
DEFAULT_KEEP = 500


class RagHistory:
    """CRUD for generated prompt sets (inputs + result) with a favourite flag."""

    def __init__(self, db_path: Path, keep: int = DEFAULT_KEEP):
        self.db_path = Path(db_path)
        self.keep = keep
        self._init_lock = threading.Lock()
        self._initialised = False

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Connection that commits on success, rolls back on error and is always closed."""
        conn = self._open()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _open(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        if not self._initialised:
            with self._init_lock:
                if not self._initialised:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS rag_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            created_at REAL NOT NULL,
                            favorite INTEGER NOT NULL DEFAULT 0,
                            batch_id TEXT,
                            batch_index INTEGER NOT NULL DEFAULT 0,
                            inputs_json TEXT NOT NULL,
                            result_json TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_history_created ON rag_history(created_at)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_rag_history_fav ON rag_history(favorite, created_at)")
                    conn.commit()
                    self._initialised = True
        return conn

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "favorite": bool(row["favorite"]),
            "batch_id": row["batch_id"],
            "batch_index": row["batch_index"],
            "inputs": json.loads(row["inputs_json"]),
            "result": json.loads(row["result_json"]),
        }

    # ------------------------------------------------------------------ API
    def add(
        self,
        inputs: dict[str, Any],
        result: dict[str, Any],
        batch_id: str | None = None,
        batch_index: int = 0,
    ) -> dict[str, Any]:
        """Store one generated prompt set and return it as a history entry."""
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO rag_history (created_at, favorite, batch_id, batch_index, inputs_json, result_json) "
                "VALUES (?, 0, ?, ?, ?, ?)",
                (now, batch_id, batch_index, json.dumps(inputs), json.dumps(result)),
            )
            entry_id = cur.lastrowid
            if self.keep > 0:
                conn.execute(
                    "DELETE FROM rag_history WHERE favorite=0 AND id NOT IN "
                    "(SELECT id FROM rag_history WHERE favorite=0 ORDER BY created_at DESC, id DESC LIMIT ?)",
                    (self.keep,),
                )
        return {
            "id": entry_id,
            "created_at": now,
            "favorite": False,
            "batch_id": batch_id,
            "batch_index": batch_index,
            "inputs": inputs,
            "result": result,
        }

    def list(self, favorites: bool = False, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Newest first. ``favorites`` restricts to starred entries. Returns ``{items, total}``."""
        where = "WHERE favorite=1" if favorites else ""
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM rag_history {where}").fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM rag_history {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {"items": [self._row_to_entry(r) for r in rows], "total": int(total)}

    def get(self, entry_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rag_history WHERE id=?", (entry_id,)).fetchone()
        return self._row_to_entry(row) if row else None

    def update(
        self,
        entry_id: int,
        favorite: bool | None = None,
        prompts: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Toggle the favourite flag and/or replace archetype prompts (re-linted, e.g. after an inline edit)."""
        entry = self.get(entry_id)
        if entry is None:
            return None
        result = entry["result"]
        if prompts:
            trigger = result.get("trigger") or entry["inputs"].get("trigger") or ""
            character = result.get("character")
            archetypes = result.setdefault("archetypes", {})
            for key, text in prompts.items():
                mode = ARCHETYPE_LINT_MODES.get(key)
                if mode is None:
                    raise ValueError(f"Unknown archetype '{key}' (expected a, b or c)")
                lint = lint_prompt(text, archetype=mode, trigger=trigger, character=character)
                arch = archetypes.setdefault(key, {"name": key.upper()})
                arch.update({
                    "prompt": lint["clean_prompt"],
                    "token_count": lint["token_count"],
                    "warnings": lint["warnings"],
                    "is_valid": lint["is_valid"],
                    "edited": True,
                })
        with self._connect() as conn:
            if favorite is not None:
                conn.execute("UPDATE rag_history SET favorite=? WHERE id=?", (int(favorite), entry_id))
            if prompts:
                conn.execute("UPDATE rag_history SET result_json=? WHERE id=?", (json.dumps(result), entry_id))
        return self.get(entry_id)

    def delete(self, entry_id: int) -> bool:
        with self._connect() as conn:
            return conn.execute("DELETE FROM rag_history WHERE id=?", (entry_id,)).rowcount > 0
