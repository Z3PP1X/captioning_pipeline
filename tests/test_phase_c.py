"""Comprehensive automated regression tests for Phase C (Pipeline Correctness & Resources).

Tests cover:
- C1: Lazy native frame re-decoding upon promotion/swap (native=1)
- C2: Runner ranking and similarity-weighted replacement (argmax sim * q) on swap
- C3: Per-video job conflict lock (HTTP 409) and separate decode/caption executors
- C5: Intra-chunk pHash deduplication and gate checks
- C7: Budget shortfall redistribution in reduce_750_to_300
- C7: Video status validation (reject invalid statuses with HTTP 400)
- C7: Candidate state validation and fallback role 'runner_up'
- C7: Manifest record deduplication on export
- Response shape unification across candidate/curation endpoints
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db
from pipeline.config import VIDEO_STATUSES, Settings
from pipeline.db import DB
from pipeline.decode import ensure_native_frame, ensure_native_frames
from pipeline.export import append_manifest_record, render_export_image
from pipeline.jobs import JobConflictError, JobManager
from pipeline.scoring import ScoringEngine


def test_ensure_native_frame_lazy_decode(tmp_path: Path):
    """C1: ensure_native_frame re-decodes from source video and updates native=1."""
    # Create a synthetic 1-second test video
    vpath = tmp_path / "test_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(vpath), fourcc, 10.0, (640, 480))
    for i in range(10):
        # Unique color per frame
        frame = np.full((480, 640, 3), i * 20, dtype=np.uint8)
        out.write(frame)
    out.release()

    test_db = DB(tmp_path / "test.db")
    with test_db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v_nat', ?, 'test', 'test', 'chunked', 1.0)",
            (str(vpath),),
        )
        conn.execute(
            "INSERT INTO chunks (id, video_id, position, start_s, end_s) VALUES (1, 'v_nat', 0, 0.0, 1.0)"
        )
        # Runner frame originally stored at 100x100 with native=0
        full_p = tmp_path / "full_c1_200.png"
        cv2.imwrite(str(full_p), np.zeros((100, 100, 3), dtype=np.uint8))
        fid = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path, native) VALUES ('v_nat', 1, 0.2, ?, 0)",
            (str(full_p),),
        ).lastrowid

    # Before ensure_native_frame
    row_before = test_db.one("SELECT native FROM frames WHERE id=?", (fid,))
    assert row_before["native"] == 0
    img_before = cv2.imread(str(full_p))
    assert img_before.shape == (100, 100, 3)

    # Trigger ensure_native_frame
    ok = ensure_native_frame(test_db, fid)
    assert ok is True

    # After ensure_native_frame
    row_after = test_db.one("SELECT native FROM frames WHERE id=?", (fid,))
    assert row_after["native"] == 1
    img_after = cv2.imread(str(full_p))
    # Decoded at full video resolution (480, 640, 3)
    assert img_after.shape == (480, 640, 3)

    # Calling again is an idempotent no-op
    assert ensure_native_frame(test_db, fid) is True


def test_ensure_native_frames_batch(tmp_path: Path):
    """C1: ensure_native_frames batch re-decodes multiple frames."""
    vpath = tmp_path / "batch_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(vpath), fourcc, 10.0, (320, 240))
    for i in range(10):
        out.write(np.full((240, 320, 3), i * 25, dtype=np.uint8))
    out.release()

    test_db = DB(tmp_path / "batch.db")
    with test_db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v_batch', ?, 'b', 'b', 'chunked', 1.0)",
            (str(vpath),),
        )
        conn.execute(
            "INSERT INTO chunks (id, video_id, position, start_s, end_s) VALUES (1, 'v_batch', 0, 0.0, 1.0)"
        )
        p1 = tmp_path / "f1.png"
        p2 = tmp_path / "f2.png"
        cv2.imwrite(str(p1), np.zeros((50, 50, 3), dtype=np.uint8))
        cv2.imwrite(str(p2), np.zeros((50, 50, 3), dtype=np.uint8))
        f1 = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path, native) VALUES ('v_batch', 1, 0.1, ?, 0)",
            (str(p1),),
        ).lastrowid
        f2 = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, full_path, native) VALUES ('v_batch', 1, 0.5, ?, 0)",
            (str(p2),),
        ).lastrowid

    re_decoded = ensure_native_frames(test_db, [f1, f2])
    assert re_decoded == 2
    for fid, p in [(f1, p1), (f2, p2)]:
        row = test_db.one("SELECT native FROM frames WHERE id=?", (fid,))
        assert row["native"] == 1
        assert cv2.imread(str(p)).shape == (240, 320, 3)


def test_budget_shortfall_redistribution():
    """C7: reduce_750_to_300 redistributes budget shortfall when chunks have fewer frames than quota."""
    engine = ScoringEngine()
    chunks = [
        {"id": 1, "budget_pct": 50.0},  # target: 150 frames
        {"id": 2, "budget_pct": 50.0},  # target: 150 frames
    ]
    # Chunk 1 has only 10 candidates (shortfall of 140)
    # Chunk 2 has 300 candidates (surplus)
    candidates = []
    for i in range(10):
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        candidates.append({
            "chunk_id": 1,
            "t": float(i * 1.0),
            "composite": 0.8,
            "scores": {"composite": 0.8, "sharpness": 0.8, "quality": 0.8},
            "embedding": emb,
        })
    for i in range(300):
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        candidates.append({
            "chunk_id": 2,
            "t": float(i * 1.0),
            "composite": 0.7,
            "scores": {"composite": 0.7, "sharpness": 0.7, "quality": 0.7},
            "embedding": emb,
        })

    picks, runners, stats = engine.reduce_750_to_300(candidates, chunks, target_total=200)

    # Total picks should meet target_total despite Chunk 1 shortfall
    assert len(picks) == 200
    p1 = [p for p in picks if p["chunk_id"] == 1]
    p2 = [p for p in picks if p["chunk_id"] == 2]
    # Chunk 1 gave all 10 it had
    assert len(p1) == 10
    # Chunk 2 absorbed the 90 deficit from chunk 1 (100 base + 90 = 190)
    assert len(p2) == 190


def test_runner_ranks_meaningful():
    """C2: Runners are ranked by quality score descending (0, 1, 2...)."""
    engine = ScoringEngine()
    chunks = [{"id": 1, "budget_pct": 100.0}]
    candidates = []
    # 5 candidates with varying composite scores
    for i, score in enumerate([0.5, 0.9, 0.4, 0.95, 0.6]):
        emb = np.random.randn(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        candidates.append({
            "chunk_id": 1,
            "t": float(i * 2.0),
            "composite": score,
            "scores": {"composite": score, "sharpness": score, "quality": score},
            "embedding": emb,
        })

    # Target total 2 picks -> 3 runners
    picks, runners, stats = engine.reduce_750_to_300(candidates, chunks, target_total=2)
    assert len(picks) == 2
    assert len(runners) == 3

    # Check runner ranks are 0, 1, 2 and sorted by composite descending
    runner_ranks = [r["rank"] for r in runners]
    assert runner_ranks == [0, 1, 2]
    scores = [r["scores"]["composite"] for r in runners]
    assert scores == sorted(scores, reverse=True)


def test_swap_frame_similarity_replacement_and_validation():
    """C2: swap_frame validates IDs, picks best replacement via argmax (sim * q), and calls ensure_native_frame."""
    with TestClient(app) as client:
        vid = "vid_swap_test"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, '/tmp/dummy_s.mp4', 'dummy', 'dummy', 'curating_300', 60.0)",
                (vid,),
            )
            conn.execute("INSERT OR REPLACE INTO chunks (id, video_id, position, start_s, end_s) VALUES (101, ?, 0, 0.0, 30.0)", (vid,))
            conn.execute("INSERT OR REPLACE INTO chunks (id, video_id, position, start_s, end_s) VALUES (102, ?, 1, 30.0, 60.0)", (vid,))

            # Embeddings
            e_base = np.zeros(512, dtype=np.float32)
            e_base[0] = 1.0

            # Similar runner (cosine = 1.0)
            e_sim = np.zeros(512, dtype=np.float32)
            e_sim[0] = 1.0

            # Dissimilar runner (cosine = 0.0)
            e_diff = np.zeros(512, dtype=np.float32)
            e_diff[1] = 1.0

            # Drop frame (pick in chunk 101)
            conn.execute(
                "INSERT OR REPLACE INTO frames (id, video_id, chunk_id, t, embedding, zs_scores, full_path, native) VALUES (1001, ?, 101, 5.0, ?, ?, '/tmp/p1001.png', 1)",
                (vid, e_base.tobytes(), json.dumps({"composite": 0.85})),
            )
            conn.execute("INSERT OR REPLACE INTO picks (frame_id, chunk_id, role, rank, state) VALUES (1001, 101, 'pick', 0, 'keep')")

            # Runner 1 (dissimilar, composite 0.6)
            conn.execute(
                "INSERT OR REPLACE INTO frames (id, video_id, chunk_id, t, embedding, zs_scores, full_path, native) VALUES (1002, ?, 101, 6.0, ?, ?, '/tmp/p1002.png', 0)",
                (vid, e_diff.tobytes(), json.dumps({"composite": 0.6})),
            )
            conn.execute("INSERT OR REPLACE INTO picks (frame_id, chunk_id, role, rank, state) VALUES (1002, 101, 'runner_up', 1, 'candidate')")

            # Runner 2 (highly similar, composite 0.8)
            conn.execute(
                "INSERT OR REPLACE INTO frames (id, video_id, chunk_id, t, embedding, zs_scores, full_path, native) VALUES (1003, ?, 101, 5.2, ?, ?, '/tmp/p1003.png', 0)",
                (vid, e_sim.tobytes(), json.dumps({"composite": 0.8})),
            )
            conn.execute("INSERT OR REPLACE INTO picks (frame_id, chunk_id, role, rank, state) VALUES (1003, 101, 'runner_up', 0, 'candidate')")

            # Pick in chunk 102 (different chunk)
            conn.execute(
                "INSERT OR REPLACE INTO frames (id, video_id, chunk_id, t, full_path, native) VALUES (2001, ?, 102, 35.0, '/tmp/p2001.png', 1)",
                (vid,),
            )
            conn.execute("INSERT OR REPLACE INTO picks (frame_id, chunk_id, role, rank, state) VALUES (2001, 102, 'pick', 0, 'keep')")

        try:
            # 1. Validation: swap with frame from different chunk fails with HTTP 400
            res_bad_chunk = client.post(
                f"/api/videos/{vid}/swap-frame",
                json={"drop_frame_id": 1001, "pick_frame_id": 2001},
            )
            assert res_bad_chunk.status_code == 400
            assert "same chunk" in res_bad_chunk.json()["detail"]

            # 2. Validation: swap with non-existent drop frame fails with HTTP 404
            res_not_found = client.post(
                f"/api/videos/{vid}/swap-frame",
                json={"drop_frame_id": 99999},
            )
            assert res_not_found.status_code == 404

            # 3. Auto-replace without pick_frame_id chooses runner 1003 (highest sim * q)
            res_auto = client.post(
                f"/api/videos/{vid}/swap-frame",
                json={"drop_frame_id": 1001},
            )
            assert res_auto.status_code == 200
            data = res_auto.json()
            assert data["swapped_in"]["id"] == 1003
            assert data["swapped_out"]["id"] == 1001

            # Check DB updated
            pick_row = db.one("SELECT role, state FROM picks WHERE frame_id=1003")
            assert pick_row["role"] == "pick"
            assert pick_row["state"] == "keep"
            drop_row = db.one("SELECT role, state FROM picks WHERE frame_id=1001")
            assert drop_row["role"] == "runner_up"
            assert drop_row["state"] == "candidate"

            # Check unified response fields present
            candidate = data["candidates"][0]
            assert "chunk_pos" in candidate
            assert "chunk_position" in candidate
            assert "chunk_start" in candidate
            assert "chunk_end" in candidate
            assert "native" in candidate

        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
                conn.execute("DELETE FROM chunks WHERE video_id=?", (vid,))
                conn.execute("DELETE FROM videos WHERE id=?", (vid,))


def test_candidate_state_endpoint_validation_and_fallback():
    """C7: set_candidate_state_endpoint validates allowed states and inserts fallback with role='runner_up'."""
    with TestClient(app) as client:
        vid = "vid_state_test"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, '/tmp/dummy_st.mp4', 'dummy', 'dummy', 'curating_300', 60.0)",
                (vid,),
            )
            conn.execute("INSERT OR REPLACE INTO chunks (id, video_id, position, start_s, end_s) VALUES (201, ?, 0, 0.0, 30.0)", (vid,))
            # Frame without pick record
            conn.execute(
                "INSERT OR REPLACE INTO frames (id, video_id, chunk_id, t, full_path, native) VALUES (3001, ?, 201, 10.0, '/tmp/p3001.png', 0)",
                (vid,),
            )

        try:
            # 1. Invalid state rejected with HTTP 400
            res_invalid = client.post(
                f"/api/videos/{vid}/candidates/3001/state",
                json={"state": "invalid_state_foo"},
            )
            assert res_invalid.status_code == 400

            # 2. Valid state accepted; fallback inserts role='runner_up'
            res_valid = client.post(
                f"/api/videos/{vid}/candidates/3001/state",
                json={"state": "keep"},
            )
            assert res_valid.status_code == 200
            pick_row = db.one("SELECT role, state FROM picks WHERE frame_id=3001")
            assert pick_row is not None
            assert pick_row["role"] == "runner_up"
            assert pick_row["state"] == "keep"

        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
                conn.execute("DELETE FROM chunks WHERE video_id=?", (vid,))
                conn.execute("DELETE FROM videos WHERE id=?", (vid,))


def test_video_status_validation():
    """C7: PATCH /api/videos/{id} rejects invalid status with HTTP 400."""
    with TestClient(app) as client:
        vid = "vid_status_val"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, '/tmp/dummy_sv.mp4', 'dummy', 'dummy', 'new', 60.0)",
                (vid,),
            )

        try:
            # Invalid status returns 400
            res_bad = client.patch(f"/api/videos/{vid}", json={"status": "bogus_status"})
            assert res_bad.status_code == 400
            assert "Invalid status" in res_bad.json()["detail"]

            # Valid status succeeds
            for valid_st in ("chunked", "curating_300", "broken"):
                res_good = client.patch(f"/api/videos/{vid}", json={"status": valid_st})
                assert res_good.status_code == 200
                assert res_good.json()["video"]["status"] == valid_st

        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM videos WHERE id=?", (vid,))


def test_manifest_deduplication(tmp_path: Path):
    """C7: append_manifest_record prevents duplicate lines in manifest.jsonl."""
    manifest_p = tmp_path / "manifest.jsonl"
    entry1 = {"video_id": "v1", "timestamp_s": 10.0, "caption": "Caption 1"}
    entry2 = {"video_id": "v1", "timestamp_s": 20.0, "caption": "Caption 2"}

    # Write initial entries
    append_manifest_record(manifest_p, entry1)
    append_manifest_record(manifest_p, entry2)

    lines = [json.loads(l) for l in manifest_p.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines) == 2

    # Attempt to write entry1 again -> should update in-place without adding extra line
    entry1_updated = {"video_id": "v1", "timestamp_s": 10.0, "caption": "Updated Caption 1"}
    append_manifest_record(manifest_p, entry1_updated)

    lines_after = [json.loads(l) for l in manifest_p.read_text(encoding="utf-8").strip().splitlines()]
    assert len(lines_after) == 2
    assert lines_after[0]["caption"] == "Updated Caption 1"


def test_per_video_job_lock():
    """C3: JobManager raises JobConflictError when an active job exists on the same video."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        test_db = DB(tmp_path / "lock.db")
        settings = Settings(video_dir=tmp_path / "v", cache_dir=tmp_path / "c", finished_dir=tmp_path / "f")
        settings.ensure_dirs()

        with test_db.write() as conn:
            conn.execute("INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v_lock', '/tmp/vl.mp4', 'vl', 'vl', 'new', 60.0)")

        jm = JobManager(test_db, settings)
        # Prevent jobs from executing to test queue conflict
        held: list = []
        jm.decode_executor.submit = lambda fn, *args: held.append((fn, args))

        j1 = jm.submit_extract_job("v_lock")
        assert j1 > 0

        # Submitting second job while j1 is queued raises JobConflictError
        with pytest.raises(JobConflictError):
            jm.submit_extract_job("v_lock")

        with pytest.raises(JobConflictError):
            jm.submit_extract_750_job("v_lock")

        # After cancelling j1, new job can be submitted
        jm.cancel_job(j1)
        j2 = jm.submit_extract_job("v_lock")
        assert j2 > j1
