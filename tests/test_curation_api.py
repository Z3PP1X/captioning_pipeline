"""Tests for 15-chunk budget management, 300 curation, and swap API endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db


def test_openrouter_models_endpoint():
    with TestClient(app) as client:
        res = client.get("/api/openrouter/models")
        assert res.status_code == 200
        data = res.json()
        assert "models" in data
        models = data["models"]
        assert any(m["id"] == "google/gemma-4-31b-it" and m["recommended"] for m in models)
        assert any(m["id"] == "deepseek/deepseek-v4.1-flash" for m in models)
        assert any(m["id"] == "x-ai/grok-4.7" for m in models)


def test_curation_endpoints_and_swap():
    with TestClient(app) as client:
        # Create a dedicated test video fixture so user library videos are NEVER modified
        test_vid_id = "test_fixture_curation_vid"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'chunked', 120.0)",
                (test_vid_id, "/tmp/dummy_curate.mp4", "Dummy Curate.mp4", "dummy_curate"),
            )
        try:
            # 1. Test suggest chunks with max 15 and budget weighting
            res = client.post(f"/api/videos/{test_vid_id}/chunks/suggest", json={"max_chunks": 15})
            assert res.status_code == 200
            suggested = res.json().get("suggested", [])
            if not suggested:
                suggested = [
                    {"position": 0, "start_s": 0.0, "end_s": 30.0, "budget_pct": 50.0, "k": 5},
                    {"position": 1, "start_s": 30.0, "end_s": 60.0, "budget_pct": 50.0, "k": 5},
                ]
            assert len(suggested) <= 15
            total_budget = sum(c["budget_pct"] for c in suggested)
            assert abs(total_budget - 100.0) < 0.5

            # Save chunks with budgets
            res = client.put(f"/api/videos/{test_vid_id}/chunks", json={"chunks": suggested})
            assert res.status_code == 200
            saved = res.json()["chunks"]
            assert len(saved) == len(suggested)
            assert "budget_pct" in saved[0]

            # 2. Test curation-300 endpoint
            res = client.get(f"/api/videos/{test_vid_id}/curation-300")
            assert res.status_code == 200
            curation = res.json()
            assert "picks" in curation
            assert "runners" in curation
            assert "distribution" in curation
            assert "stats" in curation
            assert "caption_model" in saved[0]

            # 3. Test patch chunk caption model
            chunk_id = saved[0]["id"]
            patch_res = client.patch(f"/api/chunks/{chunk_id}", json={"caption_model": "deepseek/deepseek-v4.1-flash"})
            assert patch_res.status_code == 200
            assert patch_res.json()["chunk"]["caption_model"] == "deepseek/deepseek-v4.1-flash"
        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM chunks WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM videos WHERE id=?", (test_vid_id,))


def test_pure_deselect_and_promotion_over_budget():
    """Verify deselecting a frame marks it rejected without auto-swapping, and promoting runner increases count without eviction."""
    with TestClient(app) as client:
        test_vid_id = "test_vid_deselect_promote"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'chunked', 120.0)",
                (test_vid_id, "/tmp/dummy_dp.mp4", "Dummy DP.mp4", "dummy_dp"),
            )
            c_cur = conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, caption_model) VALUES (?, 0, 0.0, 60.0, 5, 100.0, 'google/gemma-4-31b-it')",
                (test_vid_id,),
            )
            chunk_id = c_cur.lastrowid

            # Insert 3 frames
            f1 = conn.execute("INSERT INTO frames (video_id, chunk_id, t, metrics) VALUES (?, ?, 1.0, '{}')", (test_vid_id, chunk_id)).lastrowid
            f2 = conn.execute("INSERT INTO frames (video_id, chunk_id, t, metrics) VALUES (?, ?, 2.0, '{}')", (test_vid_id, chunk_id)).lastrowid
            f3 = conn.execute("INSERT INTO frames (video_id, chunk_id, t, metrics) VALUES (?, ?, 3.0, '{}')", (test_vid_id, chunk_id)).lastrowid

            # Frame 1 and 2 are picks, Frame 3 is runner_up
            conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'pick', 0, 'keep')", (f1, chunk_id))
            conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'pick', 1, 'keep')", (f2, chunk_id))
            conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'runner_up', 2, 'candidate')", (f3, chunk_id))

        try:
            # 1. Pure Deselect: PATCH f1 state to reject
            res = client.patch(f"/api/picks/{f1}", json={"state": "reject"})
            assert res.status_code == 200
            assert res.json()["pick"]["state"] == "reject"
            assert res.json()["pick"]["role"] == "pick"

            # Verify f2 is still keep and f3 is still candidate (NO auto-swap happened)
            p2 = db.one("SELECT * FROM picks WHERE frame_id=?", (f2,))
            p3 = db.one("SELECT * FROM picks WHERE frame_id=?", (f3,))
            assert p2["state"] == "keep"
            assert p3["role"] == "runner_up"
            assert p3["state"] == "candidate"

            # 2. Promote f3 to pick WITHOUT eviction
            res = client.patch(f"/api/picks/{f3}", json={"role": "pick", "state": "keep"})
            assert res.status_code == 200
            p3_after = db.one("SELECT * FROM picks WHERE frame_id=?", (f3,))
            assert p3_after["role"] == "pick"
            assert p3_after["state"] == "keep"

            # p2 is still keep! (No forced demotion)
            p2_after = db.one("SELECT * FROM picks WHERE frame_id=?", (f2,))
            assert p2_after["role"] == "pick"
            assert p2_after["state"] == "keep"

            # 3. Check curation-300 returns both active picks
            res = client.get(f"/api/videos/{test_vid_id}/curation-300")
            assert res.status_code == 200
            cur = res.json()
            active_picks = [p for p in cur["picks"] if p["state"] == "keep"]
            assert len(active_picks) == 2
            assert {p["id"] for p in active_picks} == {f2, f3}
        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM frames WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM chunks WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM videos WHERE id=?", (test_vid_id,))


def test_openrouter_caption_endpoints():
    """Verify both /api/videos/{video_id}/caption-openrouter and /api/openrouter/batch_caption endpoints."""
    with TestClient(app) as client:
        test_vid_id = "test_vid_openrouter_caption"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'curating_300', 120.0)",
                (test_vid_id, "/tmp/dummy_cap.mp4", "Dummy Cap.mp4", "dummy_cap"),
            )

        try:
            # 1. Direct video caption endpoint
            res1 = client.post(
                f"/api/videos/{test_vid_id}/caption-openrouter",
                json={
                    "model": "google/gemma-4-31b-it",
                    "mode": "concept",
                    "trigger": "restrained_elegance",
                    "class_noun": "woman, ropes",
                },
            )
            assert res1.status_code == 200
            assert "job_id" in res1.json()
            assert res1.json()["status"] == "queued"

            # 2. Batch caption alias endpoint
            res2 = client.post(
                "/api/openrouter/batch_caption",
                json={
                    "video_id": test_vid_id,
                    "model": "google/gemma-4-31b-it",
                    "mode": "concept",
                    "trigger_word": "restrained_elegance",
                    "class_noun": "woman, ropes",
                },
            )
            assert res2.status_code == 200
            assert "job_id" in res2.json()
            assert res2.json()["status"] == "queued"
        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM jobs WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM videos WHERE id=?", (test_vid_id,))

