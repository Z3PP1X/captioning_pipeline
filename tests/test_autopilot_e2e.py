"""End-to-end autopilot run on a synthetic video with the real job manager, CLIP model and decoder.

Face recognition is replaced by a colour-based stand-in (red scene = the target person, blue scene = somebody
else, green scene = nobody) so the orchestration is deterministic; the YuNet/SFace models themselves are
covered by tests/test_face_and_person.py.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from pipeline.config import Settings
from pipeline.db import DB
from pipeline.indexer import Indexer
from pipeline.jobs import JobManager
from pipeline.autopilot import AutopilotConfig

REAL_MODELS = Path.home() / ".cache" / "captioning_pipeline" / "models"
pytestmark = pytest.mark.skipif(
    not (REAL_MODELS / "models--Xenova--clip-vit-base-patch32").is_dir(),
    reason="CLIP model not downloaded (run scripts/download_models.py)",
)

FPS = 8
DURATION_S = 120
# BGR tint per scene: 0-40 s red, 40-80 s blue, 80-120 s green
SCENE_TINT = [(30, 30, 200), (200, 30, 30), (30, 200, 30)]


def write_synthetic_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (320, 240))
    for i in range(FPS * DURATION_S):
        scene = min(2, int(i / FPS // 40))
        rng = np.random.default_rng(i // 3)
        img = np.full((240, 320, 3), SCENE_TINT[scene], dtype=np.uint8)
        for _ in range(14):  # a different layout every ~0.4 s keeps frames diverse and sharp
            x, y = int(rng.integers(0, 280)), int(rng.integers(0, 200))
            w, h = int(rng.integers(20, 90)), int(rng.integers(20, 70))
            shade = int(rng.integers(0, 255))
            tint = tuple(int(0.5 * c + 0.5 * shade) for c in SCENE_TINT[scene])
            cv2.rectangle(img, (x, y), (x + w, y + h), tint, -1 if rng.random() > 0.4 else 2)
        writer.write(img)
    writer.release()


def fake_match_frame(img_bgr, target):
    b, g, r = img_bgr.reshape(-1, 3).mean(axis=0)
    if r > g and r > b:
        return {"matched": True, "max_sim": 0.7, "target_person_sim": 0.7, "face_count": 1,
                "faces": [{"box": [0, 0, 10, 10], "score": 0.9, "sim": 0.7, "matched": True}]}
    if b > r and b > g:
        return {"matched": False, "max_sim": 0.05, "target_person_sim": 0.05, "face_count": 1,
                "faces": [{"box": [0, 0, 10, 10], "score": 0.9, "sim": 0.05, "matched": False}]}
    return {"matched": False, "max_sim": 0.0, "target_person_sim": 0.0, "face_count": 0, "faces": []}


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_MODELS_DIR", str(REAL_MODELS))
    settings = Settings(video_dir=tmp_path / "videos", cache_dir=tmp_path / "cache", finished_dir=tmp_path / "finished")
    settings.ensure_dirs()
    settings.video_dir.mkdir(parents=True, exist_ok=True)
    write_synthetic_video(settings.video_dir / "synthetic.mp4")

    db = DB(settings.db_path)
    Indexer(db, settings).scan_and_sync()
    video_id = db.one("SELECT id FROM videos")["id"]
    db.set_setting("user_settings", {"target_candidates": 90})

    jm = JobManager(db, settings)
    indexer = MagicMock()
    indexer.get_or_build_filmstrip.return_value = {"cuts": [40.0, 80.0], "thumbnails": []}
    jm.autopilot.chunker.indexer = indexer
    jm.face_engine.match_frame = fake_match_frame
    yield jm, db, video_id
    jm.decode_executor.shutdown(wait=True)
    jm.caption_executor.shutdown(wait=True)


def wait_for(jm: JobManager, video_id: str, stages: tuple[str, ...], timeout: float = 240.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = jm.autopilot.get_state(video_id)
        if state["stage"] in stages:
            return state
        if state["stage"] == "error":
            pytest.fail(f"autopilot failed: {state.get('error')}")
        time.sleep(0.25)
    pytest.fail(f"timed out waiting for {stages}; state={jm.autopilot.get_state(video_id)}")


def test_autopilot_with_human_review_end_to_end(pipeline):
    jm, db, video_id = pipeline
    emb = np.ones(128, dtype=np.float32) / np.sqrt(128)
    person = db.create_or_update_person("Hero", embedding=emb)

    jm.autopilot.start(video_id, AutopilotConfig(
        target_person_id=person["id"],
        exclude_ranges=["1:50-2:00"],
        target_frames=30,
        dup_similarity=0.995,
        review_chunks=True,
    ))
    state = wait_for(jm, video_id, ("awaiting_review",))

    # --- planning: scenes avoid the excluded range, the person's scenes are prioritised
    chunks = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position", (video_id,))
    assert len(chunks) == 3
    assert max(c["end_s"] for c in chunks) <= 110.0 + 0.02
    assert [c["priority"] for c in chunks] == ["high", "low", "low"]
    assert chunks[0]["person_presence"] == 1.0
    assert chunks[1]["person_presence"] == 0.0 and chunks[1]["face_presence"] == 1.0   # someone else
    assert chunks[2]["face_presence"] == 0.0                                           # nobody
    assert chunks[0]["budget_pct"] > chunks[1]["budget_pct"]
    assert round(sum(c["budget_pct"] for c in chunks), 1) == 100.0
    assert state["plan"]["scenes"] == 3 and state["plan"]["target_person"] == "Hero"
    assert db.one("SELECT COUNT(*) AS n FROM frames WHERE video_id=?", (video_id,))["n"] == 0, "nothing extracted before approval"

    # --- human in the loop: skip the empty scene
    jm.autopilot.approve(video_id, {chunks[2]["id"]: "skip"})
    state = wait_for(jm, video_id, ("ready",))

    # --- extraction honoured the review
    frames_by_chunk = {r["chunk_id"]: r["n"] for r in db.all("SELECT chunk_id, COUNT(*) AS n FROM frames GROUP BY chunk_id")}
    assert chunks[2]["id"] not in frames_by_chunk, "a skipped scene is never sampled"
    assert set(frames_by_chunk) == {chunks[0]["id"], chunks[1]["id"]}

    picks = db.all(
        "SELECT p.frame_id, p.state, p.chunk_id, f.zs_scores FROM picks p JOIN frames f ON f.id=p.frame_id WHERE p.role='pick'"
    )
    assert 0 < len(picks) <= 30
    assert all(p["state"] == "keep" for p in picks)

    # --- re-evaluation report and person prioritisation
    report = state["report"]
    assert report["after"]["dup_pairs"] == 0
    assert report["target_person"]["enabled"] and report["target_person"]["name"] == "Hero"
    matched_share = sum(1 for p in picks if p["zs_scores"]["target_person_matched"]) / len(picks)
    assert matched_share >= 0.6, f"target person share {matched_share:.2f}"
    assert report["picks_after"] == len(picks)

    # --- every job finished, the video is ready for captioning
    assert {j["type"]: j["status"] for j in db.all("SELECT type, status FROM jobs")} == {
        "autopilot_plan": "finished", "extract_750": "finished", "autopilot_refine": "finished",
    }
    assert db.one("SELECT status FROM videos WHERE id=?", (video_id,))["status"] == "curating_300"
    assert "ready for captioning" in state["message"].lower()


def test_autopilot_unattended_runs_through_without_review(pipeline):
    jm, db, video_id = pipeline
    jm.autopilot.start(video_id, AutopilotConfig(target_frames=24, review_chunks=False, dup_similarity=0.995))
    state = wait_for(jm, video_id, ("ready",))

    assert state["report"]["target_person"]["enabled"] is False
    assert db.one("SELECT COUNT(*) AS n FROM picks WHERE role='pick'")["n"] > 0
    assert [j["type"] for j in db.all("SELECT type FROM jobs ORDER BY id")] == [
        "autopilot_plan", "extract_750", "autopilot_refine",
    ]


def test_on_demand_refine_of_a_manually_curated_video(pipeline):
    jm, db, video_id = pipeline
    jm.autopilot.start(video_id, AutopilotConfig(target_frames=24, review_chunks=False, dup_similarity=0.995))
    wait_for(jm, video_id, ("ready",))

    jm.autopilot.refine(video_id, {"dup_similarity": 0.80})
    state = wait_for(jm, video_id, ("ready",))
    deadline = time.time() + 60
    while time.time() < deadline and db.one("SELECT COUNT(*) AS n FROM jobs WHERE type='autopilot_refine' AND status='finished'")["n"] < 2:
        time.sleep(0.2)
    state = jm.autopilot.get_state(video_id)
    assert state["report"]["config"]["dup_similarity"] == 0.80
    assert state["report"]["after"]["dup_pairs"] == 0
