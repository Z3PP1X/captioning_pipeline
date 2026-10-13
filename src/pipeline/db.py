"""SQLite state store (WAL mode, lives on ext4 under the cache dir)."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    size INTEGER, mtime REAL,
    duration REAL, fps REAL, width INTEGER, height INTEGER, codec TEXT,
    broken INTEGER NOT NULL DEFAULT 0, broken_reason TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    watermark_rect TEXT, letterbox TEXT,
    filmstrip_ready INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    start_s REAL NOT NULL, end_s REAL NOT NULL,
    k INTEGER NOT NULL DEFAULT 5,
    budget_pct REAL NOT NULL DEFAULT 0.0,
    caption_model TEXT NOT NULL DEFAULT 'google/gemma-4-31b-it',
    rule TEXT NOT NULL DEFAULT 'person',
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_chunks_video ON chunks(video_id);
CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    chunk_id INTEGER REFERENCES chunks(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    metrics TEXT, rejected_reason TEXT, phash TEXT,
    embedding BLOB, zs_scores TEXT,
    preview_path TEXT, full_path TEXT,
    UNIQUE(video_id, t)
);
CREATE INDEX IF NOT EXISTS ix_frames_chunk ON frames(chunk_id);
CREATE TABLE IF NOT EXISTS picks (
    frame_id INTEGER PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    role TEXT NOT NULL, rank INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'keep', crop TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, video_id TEXT, chunk_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
    error TEXT, created_at REAL, started_at REAL, finished_at REAL
);
CREATE TABLE IF NOT EXISTS exports (
    frame_id INTEGER PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    per_video_path TEXT, merged_path TEXT, hash TEXT, exported_at REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

JSON_COLS = {"watermark_rect", "letterbox", "tags", "metrics", "zs_scores", "crop"}


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # Safe migrations: ensure required columns exist in chunks
            cols = [r["name"] for r in c.execute("PRAGMA table_info(chunks)").fetchall()]
            if "budget_pct" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN budget_pct REAL NOT NULL DEFAULT 0.0")
            if "caption_model" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN caption_model TEXT NOT NULL DEFAULT 'google/gemma-4-31b-it'")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction (SQLite allows one writer anyway)."""
        with self._write_lock, self.conn() as c:
            yield c

    # ---- helpers
    @staticmethod
    def row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        return d

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.conn() as c:
            return self.row(c.execute(sql, args).fetchone())

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn() as c:
            return [self.row(r) for r in c.execute(sql, args).fetchall()]

    def get_setting(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.write() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
