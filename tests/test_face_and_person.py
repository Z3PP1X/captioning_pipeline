"""Unit and integration tests for FaceEngine, Persons DB, and Person/Auto-Extract APIs."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db, settings
from pipeline.face import FaceEngine


@pytest.fixture
def face_engine():
    return FaceEngine(settings)


@pytest.fixture
def sample_face_image():
    """Generates a synthetic 200x200 image with a basic face-like oval and eyes."""
    img = np.full((200, 200, 3), 128, dtype=np.uint8)
    # Draw an oval face
    cv2.ellipse(img, (100, 100), (45, 60), 0, 0, 360, (210, 180, 160), -1)
    # Eyes
    cv2.circle(img, (85, 85), 6, (40, 30, 20), -1)
    cv2.circle(img, (115, 85), 6, (40, 30, 20), -1)
    # Mouth
    cv2.ellipse(img, (100, 125), (15, 6), 0, 0, 180, (60, 40, 140), -1)
    return img


def test_face_engine_basic(face_engine, sample_face_image):
    # Test feature extraction and similarity
    feat1 = np.random.randn(128).astype(np.float32)
    feat1 /= np.linalg.norm(feat1)
    feat2 = feat1.copy()

    # Exact match similarity should be ~1.0
    sim = face_engine.compute_similarity(feat1, feat2)
    assert 0.99 <= sim <= 1.01

    # Orthogonal similarity should be ~0.0
    feat3 = np.random.randn(128).astype(np.float32)
    feat3 -= np.dot(feat3, feat1) * feat1
    feat3 /= np.linalg.norm(feat3)
    sim_ortho = face_engine.compute_similarity(feat1, feat3)
    assert abs(sim_ortho) < 0.2


def test_persons_db(tmp_path):
    from pipeline.db import DB
    test_db = DB(tmp_path / "test.db")

    # Create person
    v1 = np.ones(128, dtype=np.float32)
    v1 /= np.linalg.norm(v1)
    p = test_db.create_or_update_person("Elena Fisher", embedding=v1)
    assert p["id"] is not None
    assert p["name"] == "Elena Fisher"
    assert p["slug"] == "elena_fisher"
    assert p["exemplar_count"] == 1

    # Update with second exemplar (centroid merging)
    v2 = np.ones(128, dtype=np.float32) * 0.9
    v2 /= np.linalg.norm(v2)
    p2 = test_db.create_or_update_person("Elena Fisher", embedding=v2, person_id=p["id"])
    assert p2["id"] == p["id"]
    assert p2["exemplar_count"] == 2

    # List persons
    persons = test_db.list_persons()
    assert len(persons) == 1
    assert persons[0]["name"] == "Elena Fisher"

    # Set video target person
    with test_db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, name, slug, path, duration, status) VALUES ('vid_test', 'test.mp4', 'test', '/tmp/test.mp4', 60.0, 'new')"
        )
    test_db.set_video_target_person("vid_test", p["id"])
    vid = test_db.one("SELECT * FROM videos WHERE id='vid_test'")
    assert vid["target_person_id"] == p["id"]
    assert vid["target_person_name"] == "Elena Fisher"

    # Clear target person
    test_db.set_video_target_person("vid_test", None)
    vid = test_db.one("SELECT * FROM videos WHERE id='vid_test'")
    assert vid["target_person_id"] is None
    assert vid["target_person_name"] is None

    # Delete person
    deleted = test_db.delete_person(p["id"])
    assert deleted is True
    assert test_db.get_person(p["id"]) is None


def test_person_and_auto_extract_api():
    client = TestClient(app)
    import uuid
    uniq = uuid.uuid4().hex[:8]
    person_name = f"Hero_{uniq}"

    # 1. Create person
    res = client.post("/api/persons", json={"name": person_name})
    assert res.status_code == 200
    data = res.json()
    assert data["person"]["name"] == person_name
    person_id = data["person"]["id"]

    # 2. List persons
    res = client.get("/api/persons")
    assert res.status_code == 200
    persons = res.json()["persons"]
    assert any(p["id"] == person_id for p in persons)

    # 3. Patch person name
    patched_name = f"{person_name}_Patched"
    res = client.patch(f"/api/persons/{person_id}", json={"name": patched_name})
    assert res.status_code == 200
    assert res.json()["person"]["name"] == patched_name

    # 4. Insert dummy video and set target person
    test_vid_id = f"vid_face_{uniq}"
    with db.write() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO videos (id, name, slug, path, duration, status)
            VALUES (?, 'test_video.mp4', 'test_video', '/tmp/nonexistent.mp4', 120.0, 'new')
            """,
            (test_vid_id,),
        )

    res = client.post(f"/api/videos/{test_vid_id}/target-person", json={"person_id": person_id})
    assert res.status_code == 200
    assert res.json()["video"]["target_person_id"] == person_id

    # 5. Clear target person
    res = client.post(f"/api/videos/{test_vid_id}/target-person", json={"person_id": None})
    assert res.status_code == 200
    assert res.json()["video"]["target_person_id"] is None

    # 6. Delete person
    res = client.delete(f"/api/persons/{person_id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True


def test_tag_face_from_frame_flow(tmp_path, monkeypatch):
    from pipeline.api import jobs
    client = TestClient(app)
    import uuid
    uniq = uuid.uuid4().hex[:8]
    test_vid = f"vid_tag_{uniq}"

    # Setup dummy video, chunk, and frame
    img = np.full((256, 256, 3), 100, dtype=np.uint8)
    frame_path = tmp_path / f"frame_{uniq}.png"
    cv2.imwrite(str(frame_path), img)

    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, name, slug, path, duration, status) VALUES (?, 'tag.mp4', 'tag', ?, 60.0, 'new')",
            (test_vid, f"/tmp/tag_{uniq}.mp4"),
        )
        c_cur = conn.execute(
            "INSERT INTO chunks (video_id, position, start_s, end_s, k) VALUES (?, 0, 0.0, 10.0, 5)",
            (test_vid,),
        )
        chunk_id = c_cur.lastrowid
        f_cur = conn.execute(
            """
            INSERT INTO frames (video_id, chunk_id, t, metrics, preview_path, full_path, zs_scores)
            VALUES (?, ?, 2.5, '{}', ?, ?, '{}')
            """,
            (test_vid, chunk_id, str(frame_path), str(frame_path)),
        )
        frame_id = f_cur.lastrowid
        conn.execute("INSERT INTO picks (frame_id, chunk_id, role, state) VALUES (?, ?, 'pick', 'keep')", (frame_id, chunk_id))

    # Mock face_engine detection and feature extraction
    mock_face = np.array([50, 50, 100, 100, 70, 70, 110, 70, 90, 90, 80, 110, 100, 110, 0.95], dtype=np.float32)
    monkeypatch.setattr(jobs.face_engine, "detect_faces", lambda _img: [mock_face])
    dummy_feat = np.ones(128, dtype=np.float32)
    dummy_feat /= np.linalg.norm(dummy_feat)
    monkeypatch.setattr(jobs.face_engine, "extract_feature", lambda _img, _face: dummy_feat)
    monkeypatch.setattr(jobs.face_engine, "crop_face", lambda _img, _face: np.full((112, 112, 3), 120, dtype=np.uint8))

    # Call tag-face-from-frame
    res = client.post(
        f"/api/videos/{test_vid}/tag-face-from-frame",
        json={"frame_id": frame_id, "person_name": f"Tagged_{uniq}"},
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "ok"
    assert res_data["person"]["name"] == f"Tagged_{uniq}"
    assert res_data["updated_frames"] == 1

    # Check that video target_person_id was set
    vid = db.one("SELECT * FROM videos WHERE id=?", (test_vid,))
    assert vid["target_person_id"] == res_data["person"]["id"]

    # Check frame faces inspection
    f_res = client.get(f"/api/frames/{frame_id}/faces")
    assert f_res.status_code == 200
    faces_data = f_res.json()
    assert faces_data["frame_id"] == frame_id
    assert faces_data["face_count"] == 1
