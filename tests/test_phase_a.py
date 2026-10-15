"""Regression tests for the Phase A data-loss and security fixes (HANDOFF.md §4, pipeline review C1-C4, H3, H6, H7).

Every test runs on a temp DB, temp cache and temp finished dir: no library videos, no network.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import pipeline.api as api
from pipeline.autochunk import AutoChunker, DestructiveRechunkError
from pipeline.config import Settings
from pipeline.db import DB
from pipeline.export import Exporter, existing_caption_kind
from pipeline.jobs import JobManager
from pipeline.reconcile import get_video_scenes_caption_stats, reconcile_captions_and_state

VID = "fixture_phase_a"
SLUG = "fixture_a"
VLM_CAPTION = "restrained_elegance Photograph of an adult woman kneeling on a concrete floor in a box tie."


@pytest.fixture()
def env(tmp_path: Path):
    settings = Settings(
        video_dir=tmp_path / "videos",
        cache_dir=tmp_path / "cache",
        finished_dir=tmp_path / "proj" / "data" / "finished",
    )
    settings.ensure_dirs()
    return settings, DB(settings.db_path)


def _seed(db: DB, settings: Settings, status: str = "curating_300", n_chunks: int = 2,
          picks_per_chunk: int = 2, runners_per_chunk: int = 1) -> dict:
    """Video with chunks, frames (real PNGs on disk), picks and runner-ups."""
    frames_dir = settings.cache_dir / "candidates" / VID
    frames_dir.mkdir(parents=True, exist_ok=True)
    out: dict = {"chunks": [], "picks": [], "runners": []}
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, ?, 120.0)",
            (VID, "/nonexistent/fixture.mp4", "Fixture A.mp4", SLUG, status),
        )
        for c in range(n_chunks):
            cid = conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, dirty) VALUES (?, ?, ?, ?, 5, ?, 0)",
                (VID, c, c * 60.0, (c + 1) * 60.0, 100.0 / n_chunks),
            ).lastrowid
            out["chunks"].append(cid)
            for i in range(picks_per_chunk + runners_per_chunk):
                t = c * 60.0 + 1.0 + i
                png = frames_dir / f"full_c{cid}_{int(t * 1000)}.png"
                cv2.imwrite(str(png), np.full((64, 96, 3), 40 + 10 * i, dtype=np.uint8))
                fid = conn.execute(
                    "INSERT INTO frames (video_id, chunk_id, t, metrics, zs_scores, full_path) VALUES (?, ?, ?, '{}', '{}', ?)",
                    (VID, cid, t, str(png)),
                ).lastrowid
                is_pick = i < picks_per_chunk
                conn.execute(
                    "INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, ?)",
                    (fid, cid, "pick" if is_pick else "runner_up", i, "keep" if is_pick else "candidate"),
                )
                out["picks" if is_pick else "runners"].append({"id": fid, "chunk_id": cid, "t": t, "pos": c})
    return out


def _stem(frame: dict) -> str:
    return f"{SLUG}_c{frame['pos'] + 1:02d}_{int(frame['t'] * 1000):07d}"


# ----------------------------------------------------------------------------- A1 (C1): exporter vs captions
def test_exporter_never_overwrites_vlm_caption(env):
    settings, db = env
    seeded = _seed(db, settings)
    per_video = settings.finished_dir / "per_video" / SLUG
    per_video.mkdir(parents=True)
    captioned = seeded["picks"][0]
    (per_video / f"{_stem(captioned)}.txt").write_text(VLM_CAPTION, encoding="utf-8")
    cv2.imwrite(str(per_video / f"{_stem(captioned)}.png"), np.zeros((64, 96, 3), dtype=np.uint8))

    res = Exporter(db, settings).export_video(VID)

    assert (per_video / f"{_stem(captioned)}.txt").read_text(encoding="utf-8") == VLM_CAPTION
    assert (settings.finished_dir / "merged" / f"{_stem(captioned)}.txt").read_text(encoding="utf-8") == VLM_CAPTION
    assert res["captions_kept"] == 1
    assert res["stubs_written"] == len(seeded["picks"]) - 1
    # Uncaptioned frames get stubs, marked as such so the caption job replaces them later
    stub = seeded["picks"][1]
    assert existing_caption_kind(per_video / f"{_stem(stub)}.txt") == "stub"
    assert existing_caption_kind(per_video / f"{_stem(captioned)}.txt") == "caption"
    assert db.one("SELECT COUNT(*) AS n FROM exports")["n"] == len(seeded["picks"])

    # A second export (the old export-all repeated this on 'exported' videos) still keeps the caption
    Exporter(db, settings).export_video(VID)
    assert (per_video / f"{_stem(captioned)}.txt").read_text(encoding="utf-8") == VLM_CAPTION


def test_exporter_restores_missing_png_without_touching_caption(env):
    settings, db = env
    seeded = _seed(db, settings, n_chunks=1, picks_per_chunk=1)
    per_video = settings.finished_dir / "per_video" / SLUG
    per_video.mkdir(parents=True)
    frame = seeded["picks"][0]
    (per_video / f"{_stem(frame)}.txt").write_text(VLM_CAPTION, encoding="utf-8")

    Exporter(db, settings).export_video(VID)

    assert (per_video / f"{_stem(frame)}.png").is_file()
    assert (per_video / f"{_stem(frame)}.txt").read_text(encoding="utf-8") == VLM_CAPTION
    assert not (per_video / f"{_stem(frame)}.json").exists()


def test_legacy_stub_sidecar_is_detected(tmp_path: Path):
    txt = tmp_path / "x_c01_0001000.txt"
    txt.write_text("restrained_elegance, rope, woman", encoding="utf-8")
    assert existing_caption_kind(txt) == "caption"
    # Pre-fix exporter sidecars had no caption_source: those .txt files were always stubs
    txt.with_suffix(".json").write_text(json.dumps({"caption": "restrained_elegance"}), encoding="utf-8")
    assert existing_caption_kind(txt) == "stub"
    txt.write_text("", encoding="utf-8")
    assert existing_caption_kind(txt) is None


# ----------------------------------------------------------------------------- A2 (B1): export-all selection
@pytest.fixture()
def client(env, monkeypatch):
    """API bound to the temp DB (no lifespan: no library scan)."""
    settings, db = env
    monkeypatch.setattr(api, "settings", settings)
    monkeypatch.setattr(api, "db", db)
    monkeypatch.setattr(api, "autochunker", AutoChunker(db, settings))
    monkeypatch.setattr(api, "exporter", Exporter(db, settings))
    return TestClient(api.app)


def test_export_all_includes_curating_300_and_skips_exported(env, client, monkeypatch):
    _, db = env
    with db.write() as conn:
        for vid, status in [("v_cur300", "curating_300"), ("v_cur", "curating"), ("v_done", "exported"),
                            ("v_busy", "curating_300"), ("v_new", "new")]:
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status) VALUES (?, ?, ?, ?, ?)",
                (vid, f"/x/{vid}.mp4", vid, vid, status),
            )
        conn.execute("INSERT INTO jobs (type, video_id, status) VALUES ('openrouter_caption', 'v_busy', 'running')")
    called: list[str] = []
    monkeypatch.setattr(api.exporter, "export_video", lambda vid, custom_trigger=None: called.append(vid) or {"exported": 1})

    res = client.post("/api/export", json={})

    assert res.status_code == 200
    assert sorted(called) == ["v_cur", "v_cur300"]
    assert res.json()["videos_processed"] == 2


# ----------------------------------------------------------------------------- A3 (C2): chunk upsert
def test_chunk_save_keeps_curation_and_marks_moved_chunks_dirty(env):
    settings, db = env
    seeded = _seed(db, settings)
    chunker = AutoChunker(db, settings)
    current = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position", (VID,))
    edited = [dict(c) for c in current]
    edited[0]["budget_pct"] = 70.0          # budget only: not dirty
    edited[1]["budget_pct"] = 30.0
    edited[1]["end_s"] = 115.0               # range moved: dirty, frames kept

    saved = chunker.save_user_chunks(VID, edited)

    assert [c["id"] for c in saved] == seeded["chunks"]
    assert [c["dirty"] for c in saved] == [0, 1]
    assert saved[1]["end_s"] == 115.0
    n_frames = len(seeded["picks"]) + len(seeded["runners"])
    assert db.one("SELECT COUNT(*) AS n FROM frames WHERE video_id=?", (VID,))["n"] == n_frames
    assert db.one("SELECT COUNT(*) AS n FROM picks")["n"] == n_frames
    # Editing a curated video's chunks must not demote it to 'chunked'
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "curating_300"


def test_removing_a_chunk_with_picks_requires_confirmation(env, client):
    settings, db = env
    seeded = _seed(db, settings)
    current = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position", (VID,))
    only_first = [dict(current[0], budget_pct=100.0)]

    with pytest.raises(DestructiveRechunkError) as err:
        AutoChunker(db, settings).save_user_chunks(VID, only_first)
    assert err.value.affected[0]["id"] == seeded["chunks"][1]
    assert err.value.affected[0]["picks"] == 2

    res = client.put(f"/api/videos/{VID}/chunks", json={"chunks": only_first})
    assert res.status_code == 409
    assert res.json()["detail"]["requires_confirm"] is True
    assert db.one("SELECT COUNT(*) AS n FROM chunks WHERE video_id=?", (VID,))["n"] == 2

    res = client.put(f"/api/videos/{VID}/chunks", json={"chunks": only_first, "confirm_destructive": True})
    assert res.status_code == 200
    assert [c["id"] for c in res.json()["chunks"]] == [seeded["chunks"][0]]
    # Only the removed chunk's frames are gone
    assert db.one("SELECT COUNT(*) AS n FROM frames WHERE video_id=?", (VID,))["n"] == 3


def test_suggest_on_curated_video_requires_confirmation(env, client, monkeypatch):
    settings, db = env
    _seed(db, settings)
    # No video file: replace the filmstrip/cut detection with a fixed cut list
    monkeypatch.setattr(api.autochunker.indexer, "get_or_build_filmstrip", lambda vid: {"cuts": [40.0, 80.0]})
    monkeypatch.setattr("pipeline.autochunk.sample_chunk_frames", lambda *a, **k: [])

    res = client.post(f"/api/videos/{VID}/chunks/suggest")
    assert res.status_code == 409
    assert db.one("SELECT COUNT(*) AS n FROM picks")["n"] == 6


# ----------------------------------------------------------------------------- A4/A5 (C3/C4): caption job
def _held_manager(db: DB, settings: Settings) -> tuple[JobManager, list]:
    jm = JobManager(db, settings)
    held: list = []
    intercept = lambda fn, *args: held.append((fn, args))
    jm.executor.submit = intercept
    jm.decode_executor.submit = intercept
    jm.caption_executor.submit = intercept
    return jm, held


def test_cancelled_caption_job_does_not_mark_video_exported(env):
    settings, db = env
    _seed(db, settings)
    jm, held = _held_manager(db, settings)
    calls = 0

    async def fake_caption(**kwargs):
        nonlocal calls
        calls += 1
        jm._cancel_flags.add(job_id)  # user cancels while the first frame is in flight
        return VLM_CAPTION, []

    jm.captioner.caption_single_frame = fake_caption
    job_id = jm.submit_openrouter_caption_job(VID, {"api_key": "k", "model": "google/gemma-4-31b-it"})
    for fn, args in held:
        fn(*args)

    assert db.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "cancelled"
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "curating_300"
    assert calls < 4
    assert not jm._cancel_flags
    jm.executor.shutdown(wait=False)


def test_one_failing_frame_does_not_fail_the_caption_job(env):
    settings, db = env
    seeded = _seed(db, settings)
    jm, held = _held_manager(db, settings)
    bad_t = seeded["picks"][1]["t"]

    async def fake_caption(img_bgr, model, **kwargs):
        if img_bgr[0, 0, 0] == 50:  # the second pick of each chunk (see _seed colours)
            raise RuntimeError("OpenRouter API Error 502: upstream")
        return VLM_CAPTION, []

    jm.captioner.caption_single_frame = fake_caption
    job_id = jm.submit_openrouter_caption_job(VID, {"api_key": "k"})
    for fn, args in held:
        fn(*args)

    job = db.one("SELECT status, error FROM jobs WHERE id=?", (job_id,))
    assert job["status"] == "finished"
    assert "2 of 4 frames failed" in job["error"]
    assert f"t={bad_t:.3f}s" in job["error"]
    assert db.one("SELECT COUNT(*) AS n FROM exports")["n"] == 2
    # Not every approved frame has a caption: the video is not 'exported'
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "curating_300"
    jm.executor.shutdown(wait=False)


def test_caption_job_replaces_stubs_and_truncated_captions(env):
    settings, db = env
    seeded = _seed(db, settings, n_chunks=1, picks_per_chunk=3, runners_per_chunk=0)
    per_video = settings.finished_dir / "per_video" / SLUG
    Exporter(db, settings).export_video(VID)  # writes stubs for all three
    good, cut, stub = seeded["picks"]
    (per_video / f"{_stem(good)}.txt").write_text(VLM_CAPTION, encoding="utf-8")
    (per_video / f"{_stem(good)}.json").unlink()
    (per_video / f"{_stem(cut)}.txt").write_text(VLM_CAPTION[:-20], encoding="utf-8")  # cut mid-sentence
    (per_video / f"{_stem(cut)}.json").unlink()

    jm, held = _held_manager(db, settings)
    billed: list = []

    async def fake_caption(**kwargs):
        billed.append(1)
        return VLM_CAPTION + " Fresh.", []

    jm.captioner.caption_single_frame = fake_caption
    jm.submit_openrouter_caption_job(VID, {"api_key": "k"})
    for fn, args in held:
        fn(*args)

    assert len(billed) == 2  # the cut caption and the stub; the good caption is reused
    assert (per_video / f"{_stem(good)}.txt").read_text(encoding="utf-8") == VLM_CAPTION
    for frame in (cut, stub):
        assert (per_video / f"{_stem(frame)}.txt").read_text(encoding="utf-8").endswith("Fresh.")
        assert existing_caption_kind(per_video / f"{_stem(frame)}.txt") == "caption"
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "exported"
    jm.executor.shutdown(wait=False)


# ----------------------------------------------------------------------------- A5 (C4a): extract-750 cancel
def test_cancelled_extract_750_keeps_existing_curation(env, monkeypatch):
    settings, db = env
    seeded = _seed(db, settings)
    jm, held = _held_manager(db, settings)

    def sample_then_cancel(*args, **kwargs):
        jm._cancel_flags.add(job_id)  # user cancels during sampling
        return [(1.5, np.zeros((32, 32, 3), dtype=np.uint8))]

    monkeypatch.setattr("pipeline.jobs.sample_chunk_frames", sample_then_cancel)
    job_id = jm.submit_extract_750_job(VID)
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "extracting_750"
    for fn, args in held:
        fn(*args)

    assert db.one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == "cancelled"
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "curating_300"
    picks = db.all("SELECT frame_id FROM picks p JOIN frames f ON p.frame_id=f.id WHERE p.role='pick'")
    assert {p["frame_id"] for p in picks} == {p["id"] for p in seeded["picks"]}
    jm.executor.shutdown(wait=False)


# ----------------------------------------------------------------------------- A6 (H3): reconcile
def test_reconcile_ignores_rejected_picks(env):
    settings, db = env
    seeded = _seed(db, settings, status="exported")
    rejected = seeded["picks"][0]
    with db.write() as conn:
        conn.execute("UPDATE picks SET state='reject' WHERE frame_id=?", (rejected["id"],))
        for p in seeded["picks"][1:]:
            conn.execute("INSERT INTO exports (frame_id, per_video_path) VALUES (?, 'x')", (p["id"],))

    stats = reconcile_captions_and_state(db, settings)

    assert stats["videos_healed"] == []
    assert db.one("SELECT status FROM videos WHERE id=?", (VID,))["status"] == "exported"
    first_chunk = get_video_scenes_caption_stats(db, VID)[0]
    assert first_chunk["picks_count"] == 2 and first_chunk["approved_count"] == 1
    assert first_chunk["is_fully_captioned"] is True


# ----------------------------------------------------------------------------- A7 (H6): SSE from threads
def test_broadcast_from_worker_thread_reaches_stream(env):
    settings, db = env
    jm = JobManager(db, settings)

    async def scenario():
        stream = jm.sse_stream()
        assert "connected" in await stream.__anext__()
        next_msg = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0)  # let the stream block on its queue
        worker = threading.Thread(target=jm.broadcast_event, args=("job_progress", {"job_id": 7}))
        worker.start()
        worker.join()
        msg = await asyncio.wait_for(next_msg, timeout=2.0)
        await stream.aclose()
        return msg

    msg = asyncio.run(scenario())
    assert json.loads(msg.removeprefix("data: "))["data"]["job_id"] == 7
    assert jm._listeners == []
    jm.executor.shutdown(wait=False)


# ----------------------------------------------------------------------------- A8 (H7): security
def test_settings_never_return_the_api_key(env, client):
    _, db = env
    db.set_setting("user_settings", {"openrouter_api_key": "sk-or-secret"})

    body = client.get("/api/settings").json()["settings"]
    assert "openrouter_api_key" not in body
    assert body["openrouter_api_key_set"] is True

    # Saving the form (which never saw the key) must not wipe it
    res = client.put("/api/settings", json={**body, "openrouter_api_key": "", "max_edge": 1536})
    assert "sk-or-secret" not in res.text
    stored = db.get_setting("user_settings", {})
    assert stored["openrouter_api_key"] == "sk-or-secret"
    assert stored["max_edge"] == 1536
    assert "openrouter_api_key_set" not in stored


def test_spa_fallback_does_not_escape_web_root():
    for path in ("../../../pyproject.toml", "../../pipeline/config.py", "%2e%2e/%2e%2e/%2e%2e/pyproject.toml"):
        res = api.serve_spa(path)
        served = Path(getattr(res, "path", "")).resolve()
        assert not served.name.endswith((".toml", ".py")), f"{path} escaped to {served}"


def test_cors_rejects_foreign_origins(client):
    evil = client.options(
        "/api/settings",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"},
    )
    assert "access-control-allow-origin" not in evil.headers
    dev = client.options(
        "/api/settings",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "PUT"},
    )
    assert dev.headers.get("access-control-allow-origin") == "http://localhost:4200"
