"""Tests for local similarity postprocessing (>= 95% dedup with highest-score selection, 150-200 budget)
and deterministic video title hash in captions.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db as global_db
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.indexer import compute_video_title_hash
from pipeline.openrouter import OpenRouterCaptioner
from pipeline.refine import Refiner


# ----------------------------------------------------------------------------- 1. Video title hash tests
def test_compute_video_title_hash():
    # Deterministic output for same input
    h1 = compute_video_title_hash("My Awesome Video 2026.mp4")
    h2 = compute_video_title_hash("My Awesome Video 2026.mp4")
    assert h1 == h2
    assert h1.startswith("v_")
    assert len(h1) == 2 + 8  # "v_" + 8 hex chars

    # Different inputs produce different hashes
    h3 = compute_video_title_hash("Another Scene 01.mp4")
    assert h1 != h3

    # Custom prefix and length
    h_custom = compute_video_title_hash("Test.mp4", prefix="vid_", length=6)
    assert h_custom.startswith("vid_")
    assert len(h_custom) == 4 + 6


# ----------------------------------------------------------------------------- 2. Caption Token 0 + Token 1 format
def test_caption_trigger_and_video_hash_injection():
    trigger = "restrained_elegance"
    video_hash = "v_3f8a1c02"

    # Case A: Raw text without trigger or hash
    raw_a = "A cinematic medium shot of a woman in bondage rigging with soft lighting."
    cleaned_a, _ = OpenRouterCaptioner.sanitize_and_validate_caption(
        raw_a, mode="concept", trigger=trigger, video_hash=video_hash
    )
    tokens_a = cleaned_a.split()
    assert tokens_a[0] == trigger
    assert tokens_a[1] == video_hash

    # Case B: Raw text starts with trigger
    raw_b = "restrained_elegance A cinematic medium shot of a woman in bondage rigging."
    cleaned_b, _ = OpenRouterCaptioner.sanitize_and_validate_caption(
        raw_b, mode="concept", trigger=trigger, video_hash=video_hash
    )
    tokens_b = cleaned_b.split()
    assert tokens_b[0] == trigger
    assert tokens_b[1] == video_hash

    # Case C: Raw text already has both trigger and video_hash
    raw_c = f"{trigger} {video_hash} A cinematic medium shot of a woman in bondage rigging."
    cleaned_c, _ = OpenRouterCaptioner.sanitize_and_validate_caption(
        raw_c, mode="concept", trigger=trigger, video_hash=video_hash
    )
    tokens_c = cleaned_c.split()
    assert tokens_c[0] == trigger
    assert tokens_c[1] == video_hash
    assert cleaned_c.count(video_hash) == 1  # No duplicate injection


# ----------------------------------------------------------------------------- 3. Local similarity postprocessing logic
@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = DB(root / "state.db")
        settings = Settings(video_dir=root / "videos", cache_dir=root / "cache", finished_dir=root / "finished")
        settings.ensure_dirs()
        yield db, settings


def _create_unit_vector(dim: int = 512, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_refine_deduplicate_keeps_highest_score(env):
    db, settings = env
    vid = "vid_dedup_test"
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, '/fake/v.mp4', 'Test Video', 'slug', 'curating_300', 300.0)",
            (vid,),
        )
        conn.execute(
            "INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct) VALUES (1, ?, 0, 0.0, 100.0, 10, 100.0)",
            (vid,),
        )

    # Base embedding
    base_v = _create_unit_vector(seed=42)
    # A near duplicate vector with cosine sim > 0.98
    noise = _create_unit_vector(seed=99) * 0.1
    dup_v = (base_v + noise) / np.linalg.norm(base_v + noise)
    cos_sim = float(base_v @ dup_v)
    assert cos_sim >= 0.95

    # Insert two picks: Frame 1 has lower score (0.4), Frame 2 has higher score (0.9)
    with db.write() as conn:
        for fid, t, emb, qual in [(101, 10.0, base_v, 0.40), (102, 12.0, dup_v, 0.90)]:
            metrics = {"blur": 100.0, "quality": qual, "phash": 12345}
            conn.execute(
                """
                INSERT INTO frames (id, video_id, chunk_id, t, metrics, phash, embedding, preview_path, full_path, native)
                VALUES (?, ?, 1, ?, ?, 12345, ?, '/fake/p.webp', '/fake/f.png', 1)
                """,
                (fid, vid, t, json.dumps(metrics), emb.tobytes()),
            )
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, rank, role, state) VALUES (?, 1, 1, 'pick', 'keep')",
                (fid,),
            )

    refiner = Refiner(db, settings)
    report = refiner.deduplicate_and_trim_similarity(
        vid,
        similarity_threshold=0.95,
        target_min=1,
        target_max=10,
    )

    assert report["before_count"] == 2
    assert report["after_count"] == 1
    assert report["duplicates_removed"] == 1

    # Verify that the frame with the higher score (fid=102, qual=0.90) was kept as 'pick',
    # and the frame with the lower score (fid=101, qual=0.40) was demoted to 'runner_up'
    picks = db.all("SELECT frame_id, role FROM picks WHERE frame_id IN (101, 102)")
    role_by_id = {p["frame_id"]: p["role"] for p in picks}
    assert role_by_id[102] == "pick"
    assert role_by_id[101] == "runner_up"


def test_refine_trim_to_budget(env):
    db, settings = env
    vid = "vid_budget_test"
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, '/fake/v.mp4', 'Test Video', 'slug', 'curating_300', 300.0)",
            (vid,),
        )
        conn.execute(
            "INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct) VALUES (1, ?, 0, 0.0, 100.0, 10, 100.0)",
            (vid,),
        )

    # Insert 220 orthogonal frames (no duplicates)
    with db.write() as conn:
        for i in range(220):
            fid = 1000 + i
            emb = _create_unit_vector(seed=i)
            qual = 0.5 + (i / 1000.0)
            metrics = {"blur": 100.0, "quality": qual, "phash": 1000 + i}
            conn.execute(
                """
                INSERT INTO frames (id, video_id, chunk_id, t, metrics, phash, embedding, preview_path, full_path, native)
                VALUES (?, ?, 1, ?, ?, ?, ?, '/fake/p.webp', '/fake/f.png', 1)
                """,
                (fid, vid, float(i), json.dumps(metrics), 1000 + i, emb.tobytes()),
            )
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, rank, role, state) VALUES (?, 1, 1, 'pick', 'keep')",
                (fid,),
            )

    refiner = Refiner(db, settings)
    report = refiner.deduplicate_and_trim_similarity(
        vid,
        similarity_threshold=0.95,
        target_min=150,
        target_max=200,
    )

    # Trimmed down from 220 to 200 (clamped to target_max)
    assert report["before_count"] == 220
    assert report["after_count"] == 200
    assert report["trimmed_to_target"] == 20

    kept_picks = db.all("SELECT frame_id FROM picks WHERE role='pick'")
    assert len(kept_picks) == 200


# ----------------------------------------------------------------------------- 4. API Endpoint test
def test_api_dedup_similarity():
    client = TestClient(app)
    vid = f"vid_api_dedup_{uuid.uuid4().hex[:8]}"
    with global_db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, 'API Test Video', ?, 'curating_300', 300.0)",
            (vid, f"/fake/{vid}.mp4", vid),
        )
        cursor = conn.execute(
            "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES (?, 0, 0.0, 100.0, 10, 100.0)",
            (vid,),
        )
        cid = cursor.lastrowid
        # Add 3 distinct frames
        for i in range(3):
            emb = _create_unit_vector(seed=100 + i)
            metrics = {"blur": 100.0, "quality": 0.8, "phash": 5000 + i}
            fc = conn.execute(
                """
                INSERT INTO frames (video_id, chunk_id, t, metrics, phash, embedding, preview_path, full_path, native)
                VALUES (?, ?, ?, ?, ?, ?, '/fake/p.webp', '/fake/f.png', 1)
                """,
                (vid, cid, float(i * 10), json.dumps(metrics), 5000 + i, emb.tobytes()),
            )
            fid = fc.lastrowid
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, rank, role, state) VALUES (?, ?, 1, 'pick', 'keep')",
                (fid, cid),
            )

    # Call endpoint
    resp = client.post(f"/api/videos/{vid}/dedup-similarity", json={
        "similarity_threshold": 0.95,
        "target_min": 1,
        "target_max": 200,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["video_id"] == vid
    assert data["after_count"] == 3
    assert "duplicates_removed" in data

    # 404 on nonexistent video
    resp_404 = client.post("/api/videos/nonexistent_12345/dedup-similarity", json={})
    assert resp_404.status_code == 404
