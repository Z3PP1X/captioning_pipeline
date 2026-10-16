"""Phase F: dHash pre-dedup, window-peak sampling, curation event log, learned weights and reselect.

Synthetic frames / videos and dedicated fixture video ids only - never the user's video library.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import pipeline.api as api
from pipeline.config import DEFAULT_USER_SETTINGS, Settings, validate_user_settings
from pipeline.db import DB
from pipeline.decode import pick_window_peak, sample_chunk_frames
from pipeline.jobs import JobManager
from pipeline.learned_weights import (
    COMPONENTS,
    NotEnoughEvents,
    active_learned_weights,
    blend_composite,
    fit_learned_weights,
    training_set,
)
from pipeline.metrics import compute_dhash, dhash_dedup_consecutive, phash_hamming_distance
from pipeline.reselect import chunk_quotas
from pipeline.scoring import ScoringEngine

RNG = np.random.default_rng(7)


def _texture(seed: int, h: int = 120, w: int = 160) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def _write_video(path: Path, frames: list[np.ndarray], fps: int = 10) -> None:
    h, w = frames[0].shape[:2]
    with av.open(str(path), "w") as c:
        s = c.add_stream("mpeg4", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        s.bit_rate = 4_000_000
        for img in frames:
            for pkt in s.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
                c.mux(pkt)
        for pkt in s.encode():
            c.mux(pkt)


def _lap(img: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())


# ----------------------------------------------------------------------------- settings
def test_phase_f_settings_defaults_validate():
    for key in ("dhash_dedup_max_distance", "window_peak_sampling", "window_peak_oversample", "use_learned_weights",
                "learned_weights_blend", "learned_weights_min_events", "reselect_diversity", "reselect_elasticity"):
        assert key in DEFAULT_USER_SETTINGS
    assert DEFAULT_USER_SETTINGS["use_learned_weights"] is False
    assert DEFAULT_USER_SETTINGS["window_peak_sampling"] is True
    clean, errors, _ = validate_user_settings({"dhash_dedup_max_distance": 6, "use_learned_weights": True})
    assert not errors and clean == {"dhash_dedup_max_distance": 6, "use_learned_weights": True}
    _, errors, _ = validate_user_settings({"dhash_dedup_max_distance": 99, "learned_weights_blend": 2.0})
    assert len(errors) == 2


# ----------------------------------------------------------------------------- 1. dHash pre-dedup
def test_dhash_identical_and_different():
    a, b = _texture(1), _texture(2)
    assert phash_hamming_distance(compute_dhash(a), compute_dhash(a)) == 0
    assert phash_hamming_distance(compute_dhash(a), compute_dhash(b)) > 10
    # Small exposure shift keeps the gradient signs
    brighter = np.clip(a.astype(np.int16) + 6, 0, 255).astype(np.uint8)
    assert phash_hamming_distance(compute_dhash(a), compute_dhash(brighter)) <= 4


def test_dhash_dedup_consecutive_drops_static_runs():
    a, b = _texture(1), _texture(2)
    samples = [(0.0, a), (0.5, a.copy()), (1.0, a.copy()), (1.5, b), (2.0, b.copy()), (2.5, a.copy())]
    kept, dropped = dhash_dedup_consecutive(samples, 4)
    assert [t for t, _ in kept] == [0.0, 1.5, 2.5]  # only *consecutive* repeats go
    assert dropped == 3
    kept_off, dropped_off = dhash_dedup_consecutive(samples, 0)
    assert len(kept_off) == len(samples) and dropped_off == 0


# ----------------------------------------------------------------------------- 2. window-peak sampling
def test_pick_window_peak_prefers_sharp_and_still():
    members = [
        {"t": 0.0, "sharp": 100.0, "motion": 1.0},
        {"t": 0.3, "sharp": 400.0, "motion": 30.0},  # sharpest but moving a lot
        {"t": 0.6, "sharp": 380.0, "motion": 1.5},   # nearly as sharp and still
    ]
    assert pick_window_peak(members)["t"] == 0.6
    assert pick_window_peak(members, motion_weight=0.0)["t"] == 0.3
    assert pick_window_peak(members[:1])["t"] == 0.0


def test_window_peak_sampling_on_synthetic_video(tmp_path):
    # 6 s at 10 fps: every third frame is sharp, the others are heavily blurred
    frames = []
    for i in range(60):
        img = _texture(100 + i)
        frames.append(img if i % 3 == 0 else cv2.GaussianBlur(img, (0, 0), 4))
    video = tmp_path / "synthetic.mp4"
    _write_video(video, frames)

    uniform_stats: dict = {}
    uniform = sample_chunk_frames(video, 0.0, 5.9, sample_fps=1.0, max_samples=50, max_dim=512, stats_out=uniform_stats)
    peak_stats: dict = {}
    peak = sample_chunk_frames(
        video, 0.0, 5.9, sample_fps=1.0, max_samples=50, max_dim=512, window_peak=3, stats_out=peak_stats
    )
    assert uniform_stats["mode"] == "uniform"
    assert peak_stats["mode"] == "window_peak"
    assert abs(len(peak) - len(uniform)) <= 1  # the window count matches the uniform sample count
    assert peak_stats["subsamples"] >= 2 * peak_stats["windows"]
    assert np.mean([_lap(img) for _, img in peak]) > 1.5 * np.mean([_lap(img) for _, img in uniform])
    assert all(t in peak_stats["motion"] for t, _ in peak[1:])


def test_extract_chunk_reports_dhash_dropped(tmp_path):
    """End to end through _execute_extract_chunk on a real (synthetic) file: static half is pre-deduped."""
    static = _texture(5)
    frames = [static.copy() for _ in range(40)] + [_texture(200 + i) for i in range(40)]
    video = tmp_path / "videos" / "phase_f.mp4"
    video.parent.mkdir(parents=True)
    _write_video(video, frames)

    settings = Settings(video_dir=tmp_path / "videos", cache_dir=tmp_path / "cache", finished_dir=tmp_path / "fin")
    settings.ensure_dirs()
    test_db = DB(tmp_path / "state.db")
    with test_db.write() as conn:
        conn.execute("INSERT INTO videos (id, path, name, slug, status, duration) VALUES "
                     "('v_phase_f_dhash', ?, 'phase_f.mp4', 'phase_f', 'chunked', 8.0)", (str(video),))
        conn.execute("INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct) "
                     "VALUES (1, 'v_phase_f_dhash', 0, 0.0, 7.9, 5, 100.0)")

    jm = JobManager(test_db, settings)
    jm.face_engine = MagicMock()
    jm.face_engine.detect_faces.return_value = []
    jm.classifier = MagicMock()
    jm.classifier.embed_images.side_effect = lambda imgs, batch_size=32: _l2(RNG.normal(size=(len(imgs), 16)))

    def fake_score(cands, embeddings, target_person_embedding=None):
        for i, c in enumerate(cands):
            c["scores"] = {"composite": 0.5 + 0.01 * i}
            c["embedding"] = embeddings[i].astype(np.float32)

    jm.scoring_engine = MagicMock()
    jm.scoring_engine.score_candidates_batch.side_effect = fake_score
    with patch("pipeline.jobs.decode_exact_frames", side_effect=lambda v, ts, on_frame: [on_frame(t, static) for t in ts]), \
         patch("pipeline.jobs.hard_filter_check", return_value=(True, None)):
        res = jm._execute_extract_chunk(1, k=5)

    sampling = res["stats"]["sampling"]
    assert res["stats"]["dhash_dropped"] >= 3  # the ~4 s static stretch collapses to one sample
    assert sampling["window_peak"] is True and sampling["raw_samples"] > res["stats"]["dhash_dropped"]
    assert res["stats"]["learned_weights_active"] is False
    stored = test_db.all("SELECT metrics FROM frames WHERE chunk_id=1")
    assert stored and any("motion_energy" in f["metrics"] for f in stored)
    jm.decode_executor.shutdown(wait=False)
    jm.caption_executor.shutdown(wait=False)


# ----------------------------------------------------------------------------- fixtures for API tests
def _l2(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


VID = "test_fixture_phase_f"


def _zs(sharp: float, quality: float, comp: float) -> dict:
    return {"sharpness": sharp, "quality": quality, "multiple_persons": 0.2, "one_person": 0.6, "tools": 0.1,
            "equipment": 0.1, "face_score": 0.5, "face_count": 1, "composite": comp}


def _cleanup(conn_db: DB, vid: str) -> None:
    with conn_db.write() as conn:
        conn.execute("DELETE FROM curation_events WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM jobs WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM chunks WHERE video_id=?", (vid,))
        conn.execute("DELETE FROM videos WHERE id=?", (vid,))


@pytest.fixture
def seeded():
    """2 chunks x 20 frames. Chunk frames come in 4 visual clusters (5 near-identical frames each); within a
    cluster quality rises with the index, so pure quality ranking stacks one cluster while coverage spreads."""
    db = api.db
    _cleanup(db, VID)
    centers = _l2(RNG.normal(size=(8, 64)))
    frames: dict[int, list[int]] = {}
    with db.write() as conn:
        conn.execute("INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'curating_300', 100.0)",
                     (VID, "/nonexistent/phase_f_fixture.mp4", "Phase F Fixture.mp4", "phase_f_fixture"))
        for pos in range(2):
            cid = conn.execute(
                "INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES (?, ?, ?, ?, 5, 50.0)",
                (VID, pos, pos * 50.0, pos * 50.0 + 50.0),
            ).lastrowid
            frames[cid] = []
            for i in range(20):
                cluster = pos * 4 + i // 5
                emb = _l2((centers[cluster] + 0.05 * RNG.normal(size=64))[None, :])[0].astype(np.float32)
                comp = 0.3 + 0.1 * (i % 5) + (0.05 if i // 5 == 0 else 0.0)
                fid = conn.execute(
                    "INSERT INTO frames (video_id, chunk_id, t, metrics, embedding, zs_scores, preview_path, full_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, '', '')",
                    (VID, cid, pos * 50.0 + i * 2.0, json.dumps({"tile_sharp": 100.0 + i}), emb.tobytes(),
                     json.dumps(_zs(0.1 * (i % 10), 0.5, comp))),
                ).lastrowid
                role = "pick" if i < 5 else "runner_up"
                conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, ?)",
                             (fid, cid, role, i, "keep" if role == "pick" else "candidate"))
                frames[cid].append(fid)
    yield {"db": db, "frames": frames, "chunks": list(frames)}
    _cleanup(db, VID)


# ----------------------------------------------------------------------------- 3. curation events
def _events(db: DB) -> list[dict]:
    return db.all("SELECT * FROM curation_events WHERE video_id=? ORDER BY id", (VID,))


def test_curation_endpoints_log_events(seeded):
    db, frames = seeded["db"], seeded["frames"]
    c1, c2 = seeded["chunks"]
    pick, runner = frames[c1][0], frames[c1][7]
    with TestClient(api.app) as client:
        assert client.post(f"/api/videos/{VID}/candidates/{pick}/state", json={"state": "star"}).status_code == 200
        assert client.post(f"/api/videos/{VID}/candidates/{pick}/state", json={"state": "candidate"}).status_code == 200
        assert client.post(f"/api/videos/{VID}/candidates/{frames[c1][1]}/state", json={"state": "reject"}).status_code == 200
        # unchanged state: nothing logged
        assert client.post(f"/api/videos/{VID}/candidates/{frames[c1][2]}/state", json={"state": "keep"}).status_code == 200
        assert client.post(f"/api/videos/{VID}/swap-frame", json={"drop_frame_id": frames[c2][0],
                                                                   "pick_frame_id": frames[c2][9]}).status_code == 200
        assert client.patch(f"/api/picks/{runner}", json={"role": "pick", "state": "keep"}).status_code == 200
        assert client.patch(f"/api/picks/{frames[c1][3]}", json={"state": "reject"}).status_code == 200
        n_before_bulk = len(_events(db))
        assert client.post(f"/api/videos/{VID}/curate/keep_all").status_code == 200
        bulk_keep = _events(db)[n_before_bulk:]
        assert client.post(f"/api/videos/{VID}/curate/reject_all").status_code == 200

    events = _events(db)
    actions = [(e["frame_id"], e["action"]) for e in events[:n_before_bulk]]
    assert actions == [
        (pick, "star"), (pick, "unstar"), (frames[c1][1], "reject"),
        (frames[c2][0], "swap_out"), (frames[c2][9], "swap_in"),
        (runner, "swap_in"), (runner, "keep"), (frames[c1][3], "reject"),
    ]
    # keep_all only logs the picks whose state it changes (the 2 rejected ones and the 'candidate' one)
    assert {e["action"] for e in bulk_keep} == {"bulk_keep"}
    assert {e["frame_id"] for e in bulk_keep} == {frames[c1][1], frames[c1][3], pick}
    n_picks = db.one("SELECT COUNT(*) AS n FROM picks p JOIN frames f ON f.id=p.frame_id "
                     "WHERE f.video_id=? AND p.role='pick'", (VID,))["n"]
    assert sum(1 for e in events if e["action"] == "bulk_reject") == n_picks
    feats = json.loads(events[0]["features"])
    for key in COMPONENTS + ("composite", "tile_sharp", "role", "state"):
        assert key in feats
    assert events[0]["chunk_id"] == c1 and events[0]["created_at"] > 0


# ----------------------------------------------------------------------------- 4. learned weights
def _insert_events(db: DB, n: int, seed: int = 3, one_class: bool = False) -> None:
    rng = np.random.default_rng(seed)
    with db.write() as conn:
        for i in range(n):
            sharp = float(rng.uniform())
            other = {k: float(rng.uniform()) for k in ("quality", "persons", "objects", "face")}
            keep = one_class or (sharp + 0.15 * rng.normal() > 0.5)
            feats = {"sharpness": sharp, **other}
            conn.execute(
                "INSERT INTO curation_events (video_id, frame_id, chunk_id, action, features, created_at) "
                "VALUES ('v_lw', ?, 1, ?, ?, ?)",
                (10_000 + i, "keep" if keep else "reject", json.dumps(feats), time.time()),
            )


def test_fit_learned_weights_recovers_the_signal(tmp_path):
    db = DB(tmp_path / "lw.db")
    with pytest.raises(NotEnoughEvents):
        fit_learned_weights(db, min_events=50)
    _insert_events(db, 120)
    # A later event on the same frame overrides the earlier label; unstar carries no label
    with db.write() as conn:
        conn.execute("INSERT INTO curation_events (video_id, frame_id, action, features, created_at) "
                     "VALUES ('v_lw', 10000, 'unstar', '{}', 0)")
    X, y = training_set(db)
    assert X.shape == (119, len(COMPONENTS)) and y.size == 119
    model = fit_learned_weights(db, min_events=50)
    w = model["weights"]
    assert max(w, key=w.get) == "sharpness" and w["sharpness"] > 0.5
    assert abs(sum(w.values()) - 1.0) < 1e-3 and min(w.values()) >= 0.0
    assert model["accuracy"] >= 0.8 and model["auc"] >= 0.85
    assert db.get_setting("learned_weights")["n_events"] == 119
    assert active_learned_weights(db, {"use_learned_weights": False}) is None
    assert active_learned_weights(db, {"use_learned_weights": True, "learned_weights_min_events": 50}) == w
    assert active_learned_weights(db, {"use_learned_weights": True, "learned_weights_min_events": 500}) is None


def test_fit_rejects_one_class(tmp_path):
    db = DB(tmp_path / "lw1.db")
    _insert_events(db, 60, one_class=True)
    with pytest.raises(NotEnoughEvents):
        fit_learned_weights(db, min_events=50)


def test_learned_weights_endpoints(tmp_path, monkeypatch):
    temp_db = DB(tmp_path / "lw_api.db")
    monkeypatch.setattr(api, "db", temp_db)
    with TestClient(api.app) as client:
        res = client.get("/api/scoring/learned-weights")
        assert res.status_code == 200
        body = res.json()
        assert body["fitted"] is False and body["active"] is False and body["n_events"] == 0
        assert body["min_events"] == 50 and body["weights"] is None
        assert client.post("/api/scoring/learned-weights/fit").status_code == 400

        _insert_events(temp_db, 80)
        res = client.post("/api/scoring/learned-weights/fit")
        assert res.status_code == 200
        body = res.json()
        assert body["fitted"] and body["n_events"] == 80 and body["accuracy"] >= 0.8
        assert body["active"] is False  # use_learned_weights defaults to false
        assert set(body["weights"]) == set(COMPONENTS)

        temp_db.set_setting("user_settings", {"use_learned_weights": True})
        assert client.get("/api/scoring/learned-weights").json()["active"] is True


def _fake_classifier(dim: int = 32) -> MagicMock:
    clf = MagicMock()
    rng = np.random.default_rng(11)
    clf.embed_texts.side_effect = lambda texts: _l2(rng.normal(size=(len(texts), dim))).astype(np.float32)
    return clf


def test_scoring_engine_blend_is_opt_in():
    engine = ScoringEngine(_fake_classifier())
    metrics = {"tile_sharp": 200.0, "mean_luma": 120.0, "contrast": 40.0, "clipping": 0.0, "entropy": 7.0}
    emb = _l2(RNG.normal(size=(2, 32))).astype(np.float32)

    def cands():
        return [{"metrics": dict(metrics), "t": float(i)} for i in range(2)]

    default = engine.score_candidates_batch(cands(), emb)
    assert all("composite_base" not in c["scores"] for c in default)

    weights = {"sharpness": 1.0, "quality": 0.0, "persons": 0.0, "objects": 0.0, "face": 0.0}
    engine.configure_learned(weights, blend=0.5)
    blended = engine.score_candidates_batch(cands(), emb)
    for d, b in zip(default, blended):
        assert b["scores"]["composite_base"] == d["scores"]["composite"]
        expected = blend_composite(d["scores"]["composite"], {"sharpness": b["scores"]["sharpness"]}, weights, 0.5)
        assert abs(b["scores"]["composite"] - expected) < 1e-3

    engine.configure_learned(None)
    again = engine.score_candidates_batch(cands(), emb)
    assert [c["scores"] for c in again] == [c["scores"] for c in default]


# ----------------------------------------------------------------------------- 5. reselect
def _picks(db: DB) -> list[dict]:
    return db.all("SELECT p.frame_id, p.chunk_id, p.state, p.rank FROM picks p JOIN frames f ON f.id=p.frame_id "
                  "WHERE f.video_id=? AND p.role='pick'", (VID,))


def test_chunk_quotas_largest_remainder():
    assert chunk_quotas([1, 2, 3], {1: 50.0, 2: 30.0, 3: 20.0}, 9) == {1: 4, 2: 3, 3: 2}  # 4.5/2.7/1.8
    assert sum(chunk_quotas([1, 2, 3], {1: 0, 2: 0, 3: 0}, 7).values()) == 7


def test_reselect_respects_locks_rejects_and_quotas(seeded):
    db, frames = seeded["db"], seeded["frames"]
    c1, c2 = seeded["chunks"]
    starred, exported, rejected = frames[c1][17], frames[c2][12], frames[c1][0]
    with db.write() as conn:
        conn.execute("UPDATE picks SET state='star' WHERE frame_id=?", (starred,))
        conn.execute("UPDATE picks SET state='reject' WHERE frame_id=?", (rejected,))
        conn.execute("INSERT INTO exports (frame_id, per_video_path, exported_at) VALUES (?, '/tmp/x.png', 0)", (exported,))

    with TestClient(api.app) as client:
        reference = client.get(f"/api/videos/{VID}/curation-300").json()
        res = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 10, "diversity": 1.0})
        assert res.status_code == 200, res.text
        body = res.json()

    assert set(reference) <= set(body)  # same shape as curation-300, plus `reselect`
    rep = body["reselect"]
    pick_ids = {p["id"] for p in body["picks"]}
    assert len(pick_ids) == 10 and rep["picks_count"] == 10 and rep["shortfall"] == 0
    assert starred in pick_ids and exported in pick_ids and rejected not in pick_ids
    assert {r["id"]: r["state"] for r in body["runners"]}[rejected] == "reject"
    assert {p["id"]: p["state"] for p in body["picks"]}[starred] == "star"
    for ch in rep["chunks"]:
        assert ch["floor"] <= ch["picks"] <= ch["cap"]
    # diversity 1.0 = coverage: each chunk's 5 picks span (almost) all of its 4 visual clusters
    for cid in (c1, c2):
        clusters = {frames[cid].index(fid) // 5 for fid in pick_ids if fid in frames[cid]}
        assert len(clusters) >= 3
    # DB matches the response, and runners are ranked per chunk from 0
    assert {p["frame_id"] for p in _picks(db)} == pick_ids
    ranks = sorted(r["rank"] for r in body["runners"] if r["chunk_id"] == c1)
    assert ranks == list(range(len(ranks)))


def test_reselect_quality_only_and_caps(seeded):
    db, frames = seeded["db"], seeded["frames"]
    c1, _ = seeded["chunks"]
    with TestClient(api.app) as client:
        res = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 6, "diversity": 0.0})
        assert res.status_code == 200
        top = {p["id"] for p in res.json()["picks"] if p["chunk_id"] == c1}
        # pure quality: the highest composites of chunk 1 (index 4 of the first cluster = 0.75, then 0.7s)
        assert frames[c1][4] in top

        res = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 10, "per_chunk_cap": 3})
        rep = res.json()["reselect"]
        assert all(ch["picks"] <= 3 for ch in rep["chunks"]) and rep["picks_count"] == 6 and rep["shortfall"] == 4

        res = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 4, "per_chunk_floor": 3})
        assert res.status_code == 400
        assert client.post(f"/api/videos/{VID}/reselect", json={"diversity": 1.5}).status_code == 422


def test_reselect_conflict_and_missing_video(seeded):
    db = seeded["db"]
    before = {p["frame_id"] for p in _picks(db)}
    with TestClient(api.app) as client:
        # Inserted after startup: the lifespan marks orphaned running jobs as errors
        with db.write() as conn:
            conn.execute("INSERT INTO jobs (type, video_id, status, created_at) VALUES ('extract_750', ?, 'running', ?)",
                         (VID, time.time()))
        assert client.post(f"/api/videos/{VID}/reselect", json={}).status_code == 409
        assert client.post("/api/videos/test_fixture_phase_f_missing/reselect", json={}).status_code == 404
    assert {p["frame_id"] for p in _picks(db)} == before
