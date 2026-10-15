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
    target_person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL,
    target_person_name TEXT,
    autopilot TEXT,
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
    dirty INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal',
    person_presence REAL,
    face_presence REAL
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
    native INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    exemplar_count INTEGER NOT NULL DEFAULT 1,
    embedding BLOB NOT NULL,
    face_crop_path TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

JSON_COLS = {"watermark_rect", "letterbox", "tags", "metrics", "zs_scores", "crop", "autopilot"}


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            # Safe migrations: ensure required columns exist in chunks, frames, and videos
            cols = [r["name"] for r in c.execute("PRAGMA table_info(chunks)").fetchall()]
            if "budget_pct" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN budget_pct REAL NOT NULL DEFAULT 0.0")
            if "caption_model" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN caption_model TEXT NOT NULL DEFAULT 'google/gemma-4-31b-it'")
            if "priority" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
            if "person_presence" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN person_presence REAL")
            if "face_presence" not in cols:
                c.execute("ALTER TABLE chunks ADD COLUMN face_presence REAL")
            frame_cols = [r["name"] for r in c.execute("PRAGMA table_info(frames)").fetchall()]
            if "native" not in frame_cols:
                c.execute("ALTER TABLE frames ADD COLUMN native INTEGER NOT NULL DEFAULT 0")
            video_cols = [r["name"] for r in c.execute("PRAGMA table_info(videos)").fetchall()]
            if "target_person_id" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN target_person_id INTEGER REFERENCES persons(id) ON DELETE SET NULL")
            if "target_person_name" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN target_person_name TEXT")
            if "autopilot" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN autopilot TEXT")

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

    def create_or_update_person(
        self,
        name: str,
        embedding: Any,
        face_crop_path: str | None = None,
        person_id: int | None = None,
    ) -> dict:
        import re
        import time
        import numpy as np

        clean_name = name.strip()
        slug = re.sub(r"[^\w\s-]", "", clean_name).strip().lower()
        slug = re.sub(r"[-\s]+", "_", slug)[:64] or "person"
        now = time.time()

        if isinstance(embedding, np.ndarray):
            emb_blob = embedding.astype(np.float32).tobytes()
        else:
            emb_blob = bytes(embedding)

        existing = None
        if person_id is not None:
            existing = self.one("SELECT * FROM persons WHERE id=?", (person_id,))
        if not existing:
            existing = self.one("SELECT * FROM persons WHERE slug=? OR name=?", (slug, clean_name))
        with self.write() as c:
            if existing:
                # Merge running centroid embedding
                ex_count = int(existing.get("exemplar_count", 1))
                old_vec = np.frombuffer(existing["embedding"], dtype=np.float32)
                new_vec = np.frombuffer(emb_blob, dtype=np.float32)
                if float(np.linalg.norm(old_vec)) < 1e-6:
                    # A person created by name only holds a zero placeholder: it is not an exemplar
                    ex_count = 0
                merged_vec = (old_vec * ex_count + new_vec) / (ex_count + 1)
                norm = np.linalg.norm(merged_vec)
                if norm > 1e-6:
                    merged_vec /= norm
                updated_blob = merged_vec.astype(np.float32).tobytes()

                crop_path = face_crop_path or existing.get("face_crop_path")
                c.execute(
                    """
                    UPDATE persons
                    SET exemplar_count = ?,
                        embedding = ?,
                        face_crop_path = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (ex_count + 1, updated_blob, crop_path, now, existing["id"]),
                )
                pid = existing["id"]
            else:
                cur = c.execute(
                    """
                    INSERT INTO persons (name, slug, exemplar_count, embedding, face_crop_path, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                    """,
                    (clean_name, slug, emb_blob, face_crop_path, now, now),
                )
                pid = cur.lastrowid

        return self.get_person(pid)  # type: ignore

    def list_persons(self) -> list[dict]:
        persons = self.all(
            """
            SELECT p.id, p.name, p.slug, p.exemplar_count, p.face_crop_path, p.created_at, p.updated_at,
                   COUNT(v.id) as video_count
            FROM persons p
            LEFT JOIN videos v ON v.target_person_id = p.id
            GROUP BY p.id
            ORDER BY p.name ASC
            """
        )
        return persons

    def get_person(self, person_id: int) -> dict | None:
        return self.one("SELECT * FROM persons WHERE id=?", (person_id,))

    def delete_person(self, person_id: int) -> bool:
        with self.write() as c:
            c.execute("UPDATE videos SET target_person_id=NULL, target_person_name=NULL WHERE target_person_id=?", (person_id,))
            cur = c.execute("DELETE FROM persons WHERE id=?", (person_id,))
            return cur.rowcount > 0

    def set_video_target_person(self, video_id: str, person_id: int | None) -> dict | None:
        with self.write() as c:
            if person_id is not None:
                person = self.one("SELECT id, name FROM persons WHERE id=?", (person_id,))
                if not person:
                    raise ValueError(f"Person {person_id} not found")
                c.execute(
                    "UPDATE videos SET target_person_id=?, target_person_name=? WHERE id=?",
                    (person["id"], person["name"], video_id),
                )
            else:
                c.execute("UPDATE videos SET target_person_id=NULL, target_person_name=NULL WHERE id=?", (video_id,))
        return self.one("SELECT * FROM videos WHERE id=?", (video_id,))
