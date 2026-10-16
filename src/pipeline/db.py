"""SQLite state store (WAL mode, lives on ext4 under the cache dir)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
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
    title_hash TEXT,
    autopilot TEXT,
    profile TEXT NOT NULL DEFAULT 'concept',
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
CREATE TABLE IF NOT EXISTS video_persons (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'positive',
    created_at REAL NOT NULL,
    PRIMARY KEY (video_id, person_id)
);
CREATE TABLE IF NOT EXISTS image_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'object',
    prompt TEXT,
    color TEXT,
    threshold REAL,
    accuracy REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
-- User-marked regions are the learned exemplars. They outlive their frame (re-extraction deletes frames).
CREATE TABLE IF NOT EXISTS tag_regions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_id INTEGER NOT NULL REFERENCES image_tags(id) ON DELETE CASCADE,
    frame_id INTEGER REFERENCES frames(id) ON DELETE SET NULL,
    video_id TEXT,
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
    polarity TEXT NOT NULL DEFAULT 'positive',
    source TEXT NOT NULL DEFAULT 'manual',
    -- 'context' negatives are the rest of the frame around a marked region; they go with that region
    parent_id INTEGER REFERENCES tag_regions(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    crop_path TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tag_regions_tag ON tag_regions(tag_id);
CREATE INDEX IF NOT EXISTS ix_tag_regions_frame ON tag_regions(frame_id);
CREATE TABLE IF NOT EXISTS frame_tags (
    frame_id INTEGER NOT NULL REFERENCES frames(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES image_tags(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'suggested',
    score REAL,
    box TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (frame_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_frame_tags_tag ON frame_tags(tag_id, status);
-- Caption source of truth (Phase G). The .txt files under finished/ are export artefacts written from it.
-- source: 'vlm' (caption job), 'imported' (pre-existing .txt adopted by reconcile/exporter), 'stub' (exporter tags)
CREATE TABLE IF NOT EXISTS captions (
    frame_id INTEGER PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    video_id TEXT,
    text TEXT NOT NULL,
    model TEXT,
    layer TEXT,
    mode TEXT,
    prompt_version TEXT,
    trigger TEXT,
    word_count INTEGER,
    cost_usd REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    warnings TEXT,
    finish_reason TEXT,
    job_id INTEGER,
    created_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'vlm'
);
CREATE INDEX IF NOT EXISTS ix_captions_video ON captions(video_id);
-- Phase F: every human curation action, the training signal for learned quality weights (learned_weights.py).
-- No foreign keys on purpose: re-extraction deletes frames, the events (with their feature snapshot) must stay.
-- action: keep|reject|star|unstar|swap_in|swap_out|bulk_keep|bulk_reject; features: JSON of the frame's
-- score components at the time of the action.
CREATE TABLE IF NOT EXISTS curation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT,
    frame_id INTEGER,
    chunk_id INTEGER,
    action TEXT NOT NULL,
    features TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_curation_events_video ON curation_events(video_id);
CREATE INDEX IF NOT EXISTS ix_curation_events_frame ON curation_events(frame_id);
-- Phase F2: per sampled timestamp analysis signals (analysis.py). Keyed by (video_id, t), not frame id: rows
-- outlive re-extraction and are reused by it; the decoded samples and embeddings live in
-- <cache>/analysis/<video_id>.npz (emb_refs names the arrays). Dropped when the video file changes.
CREATE TABLE IF NOT EXISTS frame_features (
    video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    lap_var REAL,
    motion REAL,
    mean_luma REAL,
    luma_std REAL,
    clipping REAL,
    dhash TEXT,
    face_count INTEGER,
    face_summary TEXT,
    emb_refs TEXT,
    gates TEXT,
    q_components TEXT,
    updated_at REAL,
    PRIMARY KEY (video_id, t)
);
"""

JSON_COLS = {"watermark_rect", "letterbox", "tags", "metrics", "zs_scores", "crop", "autopilot", "box",
             "face_summary", "emb_refs", "gates", "q_components"}


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
            if "title_hash" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN title_hash TEXT")
                try:
                    from pipeline.indexer import compute_video_title_hash
                    for v in c.execute("SELECT id, name FROM videos WHERE title_hash IS NULL").fetchall():
                        if v["name"]:
                            c.execute("UPDATE videos SET title_hash=? WHERE id=?", (compute_video_title_hash(v["name"]), v["id"]))
                except Exception:
                    pass
            if "autopilot" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN autopilot TEXT")
            if "profile" not in video_cols:
                c.execute("ALTER TABLE videos ADD COLUMN profile TEXT NOT NULL DEFAULT 'concept'")
            region_cols = [r["name"] for r in c.execute("PRAGMA table_info(tag_regions)").fetchall()]
            if "parent_id" not in region_cols:
                c.execute("ALTER TABLE tag_regions ADD COLUMN parent_id INTEGER REFERENCES tag_regions(id) ON DELETE CASCADE")

            c.execute("""
            CREATE TABLE IF NOT EXISTS video_persons (
                video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'positive',
                created_at REAL NOT NULL,
                PRIMARY KEY (video_id, person_id)
            )
            """)
            try:
                now_ts = time.time()
                for row in c.execute("SELECT id, target_person_id FROM videos WHERE target_person_id IS NOT NULL").fetchall():
                    c.execute(
                        "INSERT OR IGNORE INTO video_persons (video_id, person_id, role, created_at) VALUES (?, ?, 'positive', ?)",
                        (row["id"], row["target_person_id"], now_ts)
                    )
            except Exception:
                pass

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
            c.execute("DELETE FROM video_persons WHERE person_id=?", (person_id,))
            c.execute("UPDATE videos SET target_person_id=NULL, target_person_name=NULL WHERE target_person_id=?", (person_id,))
            cur = c.execute("DELETE FROM persons WHERE id=?", (person_id,))
            return cur.rowcount > 0

    def set_video_target_person(self, video_id: str, person_id: int | None) -> dict | None:
        if person_id is not None:
            self.set_video_person_role(video_id, person_id, "positive")
        else:
            with self.write() as c:
                c.execute("DELETE FROM video_persons WHERE video_id=?", (video_id,))
                c.execute("UPDATE videos SET target_person_id=NULL, target_person_name=NULL WHERE id=?", (video_id,))
        return self.one("SELECT * FROM videos WHERE id=?", (video_id,))

    def get_video_persons(self, video_id: str, include_embeddings: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Returns {'positive': [person_dict...], 'negative': [person_dict...]} for the video."""
        rows = self.all(
            """
            SELECT p.id, p.name, p.slug, p.exemplar_count, p.face_crop_path, p.embedding,
                   vp.role, vp.created_at as assigned_at
            FROM video_persons vp
            JOIN persons p ON vp.person_id = p.id
            WHERE vp.video_id = ?
            ORDER BY vp.created_at ASC
            """,
            (video_id,),
        )
        out: dict[str, list[dict[str, Any]]] = {"positive": [], "negative": []}
        for r in rows:
            role = r["role"] if r["role"] in ("positive", "negative") else "positive"
            p_data = {
                "id": r["id"],
                "name": r["name"],
                "slug": r["slug"],
                "exemplar_count": r["exemplar_count"],
                "face_crop_path": r["face_crop_path"],
                "face_crop_url": f"/api/persons/{r['id']}/face-crop" if r["face_crop_path"] else None,
                "role": role,
                "has_embedding": bool(r.get("embedding")),
            }
            if include_embeddings and r.get("embedding") is not None:
                p_data["embedding"] = r["embedding"]
            out[role].append(p_data)
        return out

    def set_video_person_role(self, video_id: str, person_id: int, role: str | None) -> dict[str, Any]:
        """Assigns person as 'positive', 'negative', or None (remove) for a video."""
        now = time.time()
        with self.write() as c:
            if role in ("positive", "negative"):
                c.execute(
                    """
                    INSERT INTO video_persons (video_id, person_id, role, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(video_id, person_id) DO UPDATE SET role=excluded.role
                    """,
                    (video_id, person_id, role, now),
                )
            else:
                c.execute("DELETE FROM video_persons WHERE video_id=? AND person_id=?", (video_id, person_id))

            # Sync videos.target_person_id with primary positive person
            pos = c.execute(
                "SELECT p.id, p.name FROM video_persons vp JOIN persons p ON vp.person_id=p.id WHERE vp.video_id=? AND vp.role='positive' ORDER BY vp.created_at ASC LIMIT 1",
                (video_id,),
            ).fetchone()
            if pos:
                c.execute("UPDATE videos SET target_person_id=?, target_person_name=? WHERE id=?", (pos["id"], pos["name"], video_id))
            else:
                c.execute("UPDATE videos SET target_person_id=NULL, target_person_name=NULL WHERE id=?", (video_id,))

        return self.get_video_persons(video_id)
