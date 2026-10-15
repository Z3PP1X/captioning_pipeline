"""Autopilot REST API: contract, validation, stage guards (job submission is stubbed, no real work runs)."""
from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db, jobs


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def video(monkeypatch, tmp_path):
    """A fixture video with three chunks and a tiny real file; job submission is recorded, not run."""
    vid = f"vid_ap_{uuid.uuid4().hex[:8]}"
    path = tmp_path / "tiny.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(30):
        writer.write(np.full((120, 160, 3), (i * 8) % 255, dtype=np.uint8))
    writer.release()

    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, 'ap.mp4', ?, 'new', 600.0)",
            (vid, str(path), vid),
        )
        for pos in range(3):
            conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES (?, ?, ?, ?, 5, 33.3)",
                (vid, pos, pos * 100.0, pos * 100.0 + 100.0),
            )

    submitted: list[tuple] = []
    monkeypatch.setattr(jobs, "submit_autopilot_job", lambda job_type, video_id, runner: submitted.append((job_type, video_id)) or len(submitted))
    monkeypatch.setattr(jobs, "submit_extract_750_job", lambda video_id, options=None: submitted.append(("extract_750", video_id, options)) or 77)
    yield vid, submitted
    with db.write() as conn:
        conn.execute("DELETE FROM videos WHERE id=?", (vid,))


def test_idle_state_and_payload_shape(client, video):
    vid, _ = video
    body = client.get(f"/api/videos/{vid}/autopilot").json()
    assert body["stage"] == "idle" and body["video_id"] == vid
    assert body["priorities"] == ["skip", "low", "normal", "high"]
    assert len(body["chunks"]) == 3 and body["chunks"][0]["priority"] == "normal"
    assert body["target_person"] is None
    assert client.get("/api/videos/nope/autopilot").status_code == 404


def test_start_parses_ranges_and_queues_planning_job(client, video):
    vid, submitted = video
    res = client.post(f"/api/videos/{vid}/autopilot/start", json={
        "exclude_ranges": ["0:00-0:45", [120, "2:30"], {"start": 300, "end": "5:30"}],
        "target_frames": 120,
        "review_chunks": True,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["stage"] == "planning" and body["job_id"] == 1
    assert body["config"]["exclude_ranges"] == [
        {"start_s": 0.0, "end_s": 45.0}, {"start_s": 120.0, "end_s": 150.0}, {"start_s": 300.0, "end_s": 330.0},
    ]
    assert body["config"]["target_frames"] == 120
    assert submitted == [("autopilot_plan", vid)]


@pytest.mark.parametrize("payload,status", [
    ({"exclude_ranges": ["90-30"]}, 422),
    ({"exclude_ranges": ["nonsense"]}, 422),
    ({"exclude_ranges": ["0-600"]}, 400),
    ({"target_person_id": 987654}, 400),
    ({"person_focus": 1.5}, 422),
])
def test_start_validation(client, video, payload, status):
    vid, submitted = video
    assert client.post(f"/api/videos/{vid}/autopilot/start", json=payload).status_code == status
    assert submitted == []


def test_start_on_curated_video_requires_confirmation(client, video):
    vid, submitted = video
    chunk = db.one("SELECT id FROM chunks WHERE video_id=? ORDER BY position", (vid,))
    with db.write() as conn:
        cur = conn.execute(
            "INSERT INTO frames (video_id, chunk_id, t, preview_path, full_path) VALUES (?, ?, 5.0, '/x', '/x')", (vid, chunk["id"])
        )
        conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'pick', 0, 'keep')", (cur.lastrowid, chunk["id"]))

    res = client.post(f"/api/videos/{vid}/autopilot/start", json={})
    assert res.status_code == 409
    assert res.json()["detail"]["requires_confirm"] is True
    assert submitted == []
    assert client.post(f"/api/videos/{vid}/autopilot/start", json={"confirm_destructive": True}).status_code == 200


def test_priorities_review_flow_then_approve(client, video):
    vid, submitted = video
    jobs.autopilot._set(vid, stage="planning")
    res = client.post(f"/api/videos/{vid}/autopilot/priorities", json={"priorities": {"1": "high"}})
    assert res.status_code == 409, "cannot edit priorities while the pipeline is running"

    jobs.autopilot._set(vid, stage="awaiting_review", config={"exclude_ranges": []})
    ids = [c["id"] for c in db.all("SELECT id FROM chunks WHERE video_id=? ORDER BY position", (vid,))]

    res = client.post(f"/api/videos/{vid}/autopilot/priorities", json={"priorities": {str(ids[0]): "skip", str(ids[1]): "high"}})
    assert res.status_code == 200, res.text
    by_id = {c["id"]: c for c in res.json()["chunks"]}
    assert by_id[ids[0]]["priority"] == "skip" and by_id[ids[0]]["budget_pct"] == 0.0
    assert by_id[ids[1]]["budget_pct"] > by_id[ids[2]]["budget_pct"]
    assert round(sum(c["budget_pct"] for c in by_id.values()), 1) == 100.0

    assert client.post(f"/api/videos/{vid}/autopilot/priorities", json={"priorities": {str(ids[1]): "urgent"}}).status_code == 400
    assert client.post(f"/api/videos/{vid}/autopilot/priorities", json={"priorities": {"424242": "low"}}).status_code == 400

    res = client.post(f"/api/videos/{vid}/autopilot/approve", json={"priorities": {str(ids[2]): "low"}})
    assert res.status_code == 200, res.text
    assert res.json()["stage"] == "extracting" and res.json()["job_id"] == 77
    assert submitted[-1][0] == "extract_750"


def test_manual_extract_during_review_means_approve(client, video):
    vid, submitted = video
    jobs.autopilot._set(vid, stage="awaiting_review", config={"exclude_ranges": []})
    res = client.post(f"/api/videos/{vid}/extract-750")
    assert res.status_code == 200 and res.json()["autopilot"] == "extracting"
    assert jobs.autopilot.get_state(vid)["stage"] == "extracting"


def test_approve_outside_review_is_rejected(client, video):
    vid, _ = video
    assert client.post(f"/api/videos/{vid}/autopilot/approve", json={}).status_code == 400
    jobs.autopilot._set(vid, stage="extracting")
    assert client.post(f"/api/videos/{vid}/autopilot/approve", json={}).status_code == 400


def test_refine_requires_extracted_picks(client, video):
    vid, submitted = video
    assert client.post(f"/api/videos/{vid}/autopilot/refine", json={}).status_code == 400
    assert submitted == []
    assert client.post(f"/api/videos/{vid}/autopilot/refine", json={"dup_similarity": 0.2}).status_code == 422


def test_reset_blocked_while_running(client, video):
    vid, _ = video
    jobs.autopilot._set(vid, stage="extracting")
    assert client.post(f"/api/videos/{vid}/autopilot/reset").status_code == 409
    jobs.autopilot._set(vid, stage="ready")
    res = client.post(f"/api/videos/{vid}/autopilot/reset")
    assert res.status_code == 200 and res.json()["stage"] == "idle"


def test_batch_reports_per_video_results_and_overrides_ranges(client, video):
    vid, submitted = video
    res = client.post("/api/autopilot/batch", json={
        "video_ids": [vid, "missing-video"],
        "config": {"exclude_ranges": ["0-10"], "review_chunks": False},
        "per_video": {vid: {"exclude_ranges": ["20-40"]}},
    })
    assert res.status_code == 200
    body = res.json()
    assert body["started"] == 1
    assert body["results"][0] == {"video_id": vid, "ok": True}
    assert body["results"][1]["ok"] is False and "not found" in body["results"][1]["error"].lower()
    assert jobs.autopilot.get_state(vid)["config"]["exclude_ranges"] == [{"start_s": 20.0, "end_s": 40.0}]


def test_video_list_carries_only_the_slim_autopilot_state(client, video):
    vid, _ = video
    jobs.autopilot._set(vid, stage="ready", message="done", report={"swaps": [{"x": 1}] * 50})
    listed = next(v for v in client.get("/api/videos").json()["videos"] if v["id"] == vid)
    assert listed["autopilot_stage"] == "ready"
    assert listed["autopilot"] == {"stage": "ready", "message": "done"}
    assert "report" in client.get(f"/api/videos/{vid}/autopilot").json()


def test_chunk_put_round_trips_priority_and_presence(client, video):
    vid, _ = video
    chunks = client.get(f"/api/videos/{vid}/chunks").json()["chunks"]
    chunks[0]["priority"] = "skip"
    chunks[1]["person_presence"] = 0.5
    res = client.put(f"/api/videos/{vid}/chunks", json={"chunks": chunks})
    assert res.status_code == 200, res.text
    saved = client.get(f"/api/videos/{vid}/chunks").json()["chunks"]
    assert saved[0]["priority"] == "skip" and saved[0]["budget_pct"] == 0.0
    assert saved[1]["person_presence"] == 0.5
    bad = client.put(f"/api/videos/{vid}/chunks", json={"chunks": [{**saved[0], "priority": "urgent"}]})
    assert bad.status_code == 400


def test_frame_at_faces_at_and_enroll_error_paths(client, video):
    vid, _ = video
    img = client.get(f"/api/videos/{vid}/frame-at", params={"t": 1.0})
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    faces = client.get(f"/api/videos/{vid}/faces-at", params={"t": 1.0}).json()
    assert faces["faces"] == [] and faces["width"] == 160

    assert client.post("/api/persons/enroll", json={"name": "X", "video_id": vid}).status_code == 400
    no_face = client.post("/api/persons/enroll", json={"name": "X", "video_id": vid, "time_s": 1.0})
    assert no_face.status_code == 400 and "No face" in no_face.json()["detail"]
    assert client.post("/api/persons/enroll", json={"name": " ", "video_id": vid, "time_s": 1.0}).status_code == 400
    assert client.get("/api/videos/nope/frame-at").status_code == 404
