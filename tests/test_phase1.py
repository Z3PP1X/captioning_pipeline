"""Phase 1 automated tests: database, indexer, settings, and FastAPI endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db, indexer
from pipeline.config import Settings, merged_user_settings
from pipeline.db import DB


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        yield DB(Path(f.name))


def test_db_initialization(temp_db):
    videos = temp_db.all("SELECT * FROM videos")
    assert isinstance(videos, list)
    temp_db.set_setting("test_key", {"foo": "bar"})
    val = temp_db.get_setting("test_key")
    assert val == {"foo": "bar"}


def test_settings_merge():
    merged = merged_user_settings({"default_k": 10, "weights": {"sharpness": 0.50}})
    assert merged["default_k"] == 10
    assert merged["weights"]["sharpness"] == 0.50
    # Other default weights remain preserved
    assert merged["weights"]["exposure"] == 0.25


def test_api_routes():
    with TestClient(app) as client:
        # 1. Root HTML serves
        res = client.get("/")
        assert res.status_code == 200

        # 2. Settings endpoint
        res = client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert "settings" in data
        assert "default_k" in data["settings"]

        # 3. Videos endpoint
        res = client.get("/api/videos")
        assert res.status_code == 200
        vids = res.json()["videos"]
        assert isinstance(vids, list)
        assert len(vids) > 0  # Should have discovered real videos from /mnt/c/Users/Patri/Videos/_girls
        first_vid = vids[0]["id"]

        # 4. Next video endpoint
        res = client.get("/api/videos/next")
        assert res.status_code == 200
        assert "video" in res.json()

        # 5. Chunks CRUD on a dedicated test fixture video (never overwriting user videos)
        test_vid_id = "test_fixture_chunks_vid"
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'new', 60.0)",
                (test_vid_id, "/tmp/dummy.mp4", "Dummy.mp4", "dummy"),
            )
        try:
            test_chunks = [
                {"position": 0, "start_s": 5.0, "end_s": 15.0, "k": 6, "rule": "person", "tags": ["test"], "notes": "note1"},
                {"position": 1, "start_s": 20.0, "end_s": 35.0, "k": 4, "rule": "equipment", "tags": ["gear"], "notes": "note2"},
            ]
            put_res = client.put(f"/api/videos/{test_vid_id}/chunks", json={"chunks": test_chunks})
            assert put_res.status_code == 200
            saved = put_res.json()["chunks"]
            assert len(saved) == 2
            assert saved[0]["k"] == 6
            assert saved[1]["rule"] == "equipment"

            get_res = client.get(f"/api/videos/{test_vid_id}/chunks")
            assert get_res.status_code == 200
            assert len(get_res.json()["chunks"]) == 2
        finally:
            with db.write() as conn:
                conn.execute("DELETE FROM chunks WHERE video_id=?", (test_vid_id,))
                conn.execute("DELETE FROM videos WHERE id=?", (test_vid_id,))

        # 6. Byte-range video streaming
        stream_res = client.get(f"/api/videos/{first_vid}/stream", headers={"Range": "bytes=0-1023"})
        assert stream_res.status_code == 206
        assert "Content-Range" in stream_res.headers
        assert stream_res.headers["Content-Range"].startswith("bytes 0-1023/")
        assert len(stream_res.content) == 1024
