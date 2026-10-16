"""Unit tests for targeted single-chunk extraction and batch auto-extraction."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.jobs import JobManager


def test_targeted_chunk_extraction_isolation():
    """Verify that extracting a single chunk preserves other chunks and updates only targeted chunk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        test_db = DB(db_path)
        settings = Settings(
            video_dir=tmp_path / "videos",
            cache_dir=tmp_path / "cache",
            finished_dir=tmp_path / "finished",
        )
        settings.ensure_dirs()

        vid_id = "v_target_test"
        dummy_video_path = tmp_path / "videos" / "test.mp4"
        dummy_video_path.parent.mkdir(parents=True, exist_ok=True)
        dummy_video_path.write_bytes(b"dummy_video_content")

        cand_dir = settings.cache_dir / "candidates" / vid_id
        prev_dir = settings.cache_dir / "previews" / vid_id
        cand_dir.mkdir(parents=True, exist_ok=True)
        prev_dir.mkdir(parents=True, exist_ok=True)

        with test_db.write() as conn:
            conn.execute(
                """
                INSERT INTO videos (id, path, name, slug, status, duration)
                VALUES (?, ?, ?, ?, 'chunked', 100.0)
                """,
                (vid_id, str(dummy_video_path), "test.mp4", "test"),
            )
            # Create 2 chunks
            conn.execute(
                """
                INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct, rule, dirty)
                VALUES (101, ?, 0, 0.0, 50.0, 5, 50.0, 'person', 0)
                """,
                (vid_id,),
            )
            conn.execute(
                """
                INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct, rule, dirty)
                VALUES (102, ?, 1, 50.0, 100.0, 5, 50.0, 'person', 1)
                """,
                (vid_id,),
            )
            # Frame and pick for Chunk 101
            chunk1_full = cand_dir / "full_chunk1.png"
            chunk1_prev = prev_dir / "prev_chunk1.webp"
            chunk1_full.write_bytes(b"chunk1_full")
            chunk1_prev.write_bytes(b"chunk1_prev")
            cur = conn.execute(
                """
                INSERT INTO frames (video_id, chunk_id, t, metrics, phash, embedding, preview_path, full_path, native)
                VALUES (?, 101, 10.0, '{}', 'hash1', X'00', ?, ?, 1)
                """,
                (vid_id, str(chunk1_prev), str(chunk1_full)),
            )
            f101_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                VALUES (?, 101, 'pick', 1, 'active')
                """,
                (f101_id,),
            )

            # Old frame and pick for Chunk 102
            chunk2_old_full = cand_dir / "full_chunk2_old.png"
            chunk2_old_prev = prev_dir / "prev_chunk2_old.webp"
            chunk2_old_full.write_bytes(b"chunk2_old_full")
            chunk2_old_prev.write_bytes(b"chunk2_old_prev")
            cur = conn.execute(
                """
                INSERT INTO frames (video_id, chunk_id, t, metrics, phash, embedding, preview_path, full_path, native)
                VALUES (?, 102, 60.0, '{}', 'hash2', X'00', ?, ?, 1)
                """,
                (vid_id, str(chunk2_old_prev), str(chunk2_old_full)),
            )
            f102_old_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                VALUES (?, 102, 'pick', 1, 'active')
                """,
                (f102_old_id,),
            )

        jm = JobManager(test_db, settings)

        # Mock sample_chunk_frames, decode_exact_frames, classifier, and face engine
        fake_img = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        mock_samples = [
            (65.0, fake_img),
            (75.0, fake_img),
            (85.0, fake_img),
        ]

        def fake_decode(vpath, pick_ts, on_frame):
            for t in pick_ts:
                on_frame(t, fake_img)

        jm.face_engine = MagicMock()
        jm.face_engine.detect_faces.return_value = []
        jm.classifier = MagicMock()
        jm.classifier.embed_images.side_effect = lambda imgs, batch_size=32: np.zeros((len(imgs), 512), dtype=np.float32)

        mock_scoring = MagicMock()
        def fake_score_batch(cands, embeddings, target_person_embedding=None):
            for i, c in enumerate(cands):
                c["scores"] = {"person": 0.8 + i * 0.05, "composite": 0.8 + i * 0.05}
                c["embedding"] = np.zeros(512, dtype=np.float32)
        mock_scoring.score_candidates_batch.side_effect = fake_score_batch
        jm.scoring_engine = mock_scoring

        with patch("pipeline.jobs.sample_chunk_frames", return_value=mock_samples), \
             patch("pipeline.jobs.decode_exact_frames", side_effect=fake_decode), \
             patch("pipeline.jobs.hard_filter_check", return_value=(True, None)):

            res = jm._execute_extract_chunk(
                chunk_id=102,
                k=8,
                budget_pct=60.0,
            )

        assert res["chunk_id"] == 102
        assert res["picks_count"] > 0

        # Verify database state
        # Chunk 101 must remain completely untouched
        c101 = test_db.one("SELECT k, dirty FROM chunks WHERE id = 101")
        assert c101["k"] == 5
        c101_frames = test_db.all("SELECT id FROM frames WHERE chunk_id = 101")
        assert len(c101_frames) == 1
        assert c101_frames[0]["id"] == f101_id
        assert chunk1_full.exists()

        # Chunk 102 must have updated budget and dirty=0
        c102 = test_db.one("SELECT k, budget_pct, dirty FROM chunks WHERE id = 102")
        assert c102["k"] == 8
        assert c102["budget_pct"] == 60.0
        assert c102["dirty"] == 0

        # Old frame files for 102 must be purged
        c102_frames = test_db.all("SELECT id FROM frames WHERE chunk_id = 102")
        assert not any(f["id"] == f102_old_id for f in c102_frames)
        assert not chunk2_old_full.exists()
        assert not chunk2_old_prev.exists()


def test_batch_auto_extract_runner():
    """Verify batch auto-extraction iterates unextracted videos and handles individual errors."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "test.db"
        test_db = DB(db_path)
        settings = Settings(
            video_dir=tmp_path / "videos",
            cache_dir=tmp_path / "cache",
            finished_dir=tmp_path / "finished",
        )
        settings.ensure_dirs()

        with test_db.write() as conn:
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v1', '/d/1.mp4', '1.mp4', '1', 'new', 60.0)"
            )
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v2', '/d/2.mp4', '2.mp4', '2', 'curating_300', 60.0)"
            )
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v3', '/d/3.mp4', '3.mp4', '3', 'chunked', 60.0)"
            )
            # Pre-seed chunks so auto-chunker is skipped
            conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, rule) VALUES ('v1', 0, 0.0, 60.0, 5, 100.0, 'person')"
            )
            conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, rule) VALUES ('v3', 0, 0.0, 60.0, 5, 100.0, 'person')"
            )

        jm = JobManager(test_db, settings)
        processed: list[str] = []

        def fake_extract_750(vid, **kwargs):
            if vid == "v3":
                raise RuntimeError("Simulated failure on video 3")
            processed.append(vid)

        jm._execute_extract_750 = fake_extract_750  # type: ignore

        # Run with video_ids=None (auto-query) and only_unextracted=True
        # In our DB, only v1 and v3 are unextracted (v2 is curating_300)
        unextracted_rows = test_db.all(
            "SELECT id FROM videos WHERE status NOT IN ('curating', 'curating_300', 'captioning', 'exported', 'skipped')"
        )
        target_ids = [r["id"] for r in unextracted_rows]
        assert "v1" in target_ids
        assert "v3" in target_ids
        assert "v2" not in target_ids

        # Run batch job with queued status so _start_job starts cleanly
        with test_db.write() as conn:
            cur = conn.execute("INSERT INTO jobs (type, status, progress) VALUES ('batch_auto_extract', 'queued', 0.0)")
            job_id = cur.lastrowid

        jm._run_batch_auto_extract_job(
            job_id=job_id,
            video_ids=target_ids,
            options={},
        )

        # v1 succeeded
        assert "v1" in processed
        # v2 was not run
        assert "v2" not in processed
        # Job finished status in DB
        j = test_db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        assert j["status"] == "finished"
        assert "failed" in (j["error"] or "")


def test_chunk_and_batch_api_endpoints():
    """Verify HTTP API contracts for batch auto-extract and chunk extract."""
    with TestClient(app) as client:
        # 1. Batch auto extract dispatch
        res = client.post("/api/videos/batch-auto-extract", json={"only_unextracted": True})
        assert res.status_code == 200
        data = res.json()
        assert "job_id" in data
        assert data["status"] == "queued"

        # 2. Chunk extract dispatch (fixture chunk)
        vid_id = "fixture_chunk_api_vid"
        chunk_id = 9999
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'chunked', 60.0)",
                (vid_id, "/tmp/dummy.mp4", "dummy.mp4", "dummy"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct, rule) VALUES (?, ?, 0, 0.0, 30.0, 5, 50.0, 'person')",
                (chunk_id, vid_id),
            )

        res = client.post(f"/api/chunks/{chunk_id}/extract", json={"k": 8, "budget_pct": 60.0})
        assert res.status_code == 200
        cdata = res.json()
        assert "job_id" in cdata
        assert cdata["status"] == "queued"
        assert cdata["chunk_id"] == chunk_id
        assert cdata["video_id"] == vid_id
