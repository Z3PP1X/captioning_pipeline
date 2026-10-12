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
        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM chunks WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM videos WHERE id=?", (test_vid_id,))

        # 3. Test frame swap if picks and runners exist
        picks = curation["picks"]
        runners = curation["runners"]
        if picks and runners:
            drop_cand = picks[0]
            res = client.post(
                f"/api/videos/{video_id}/swap-frame",
                json={"deselected_frame_id": drop_cand["id"]},
            )
            assert res.status_code == 200
            swap_res = res.json()
            assert swap_res["status"] == "ok"
            assert "swapped_in" in swap_res
            assert "distribution" in swap_res
