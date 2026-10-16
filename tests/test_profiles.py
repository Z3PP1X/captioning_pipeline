"""HANDOFF decision 1: per-video 'character' profile (identity gate, solo crops, character caption mode)."""
from __future__ import annotations

import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.db import DB
from pipeline.profiles import apply_character_profile, caption_mode_for, character_crop


def test_character_crop_is_a_portrait_around_the_face():
    crop = character_crop([300, 100, 40, 50], 1280, 720)
    assert crop is not None
    assert abs(crop["width"] * 1280 / (crop["height"] * 720) - 2 / 3) < 0.02
    # the face is inside the crop, near its top
    assert crop["x"] * 1280 <= 300 and (crop["x"] + crop["width"]) * 1280 >= 340
    assert crop["y"] * 720 <= 100 < (crop["y"] + 0.3 * crop["height"]) * 720
    assert 0 <= crop["x"] and crop["x"] + crop["width"] <= 1.0001
    # a face so large that the crop would be the whole frame: no crop
    assert character_crop([0, 0, 400, 700], 480, 720) is None


def _seed(db: DB, tmp_path, vid: str):
    img = np.zeros((360, 640, 3), np.uint8)
    path = tmp_path / f"{vid}.jpg"
    cv2.imwrite(str(path), img)
    with db.write() as c:
        c.execute("INSERT INTO persons (id, name, slug, embedding, created_at, updated_at) VALUES (1, 'Ana', 'ana', ?, 0, 0)",
                  (np.ones(128, np.float32).tobytes(),))
        c.execute("INSERT INTO videos (id, path, name, slug, status, profile) VALUES (?, ?, 'v.mp4', 'v', 'curating_300', 'character')",
                  (vid, f"/tmp/{vid}.mp4"))
        c.execute("INSERT INTO video_persons (video_id, person_id, role, created_at) VALUES (?, 1, 'positive', 0)", (vid,))
        chunk = c.execute("INSERT INTO chunks (video_id, start_s, end_s) VALUES (?, 0, 30)", (vid,)).lastrowid
        frames = {}
        layout = [
            ("off_pick", "pick", "keep", False, 1, []),
            ("star_off", "pick", "star", False, 1, []),
            ("multi_pick", "pick", "keep", True, 2,
             [{"box": [400, 60, 40, 50], "pos_matched": True, "pos_sim": 0.7}, {"box": [100, 80, 40, 50], "pos_matched": False}]),
            ("solo_runner", "runner_up", "candidate", True, 1, [{"box": [300, 60, 40, 50], "pos_matched": True}]),
            ("multi_runner", "runner_up", "candidate", True, 2, []),
        ]
        for i, (name, role, state, matched, faces, face_list) in enumerate(layout):
            zs = {"target_person_matched": matched, "target_person_sim": 0.7 if matched else 0.1, "face_count": faces,
                  "faces": face_list, "composite": 0.5 + 0.01 * i}
            fid = c.execute("INSERT INTO frames (video_id, chunk_id, t, zs_scores, preview_path) VALUES (?, ?, ?, ?, ?)",
                            (vid, chunk, float(i), json.dumps(zs), str(path))).lastrowid
            c.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, ?)",
                      (fid, chunk, role, i, state))
            frames[name] = fid
    return frames


def test_identity_gate_and_solo_crops(tmp_path):
    db = DB(tmp_path / "s.db")
    f = _seed(db, tmp_path, "vp")
    res = apply_character_profile(db, "vp")
    assert res["applied"]
    role = lambda fid: db.one("SELECT role FROM picks WHERE frame_id=?", (fid,))["role"]
    assert role(f["off_pick"]) == "runner_up"          # off-target pick demoted
    assert role(f["star_off"]) == "pick"               # starred frames are never demoted
    assert role(f["solo_runner"]) == "pick"            # replaced by the matching solo runner first
    assert role(f["multi_runner"]) == "runner_up"
    assert res["promoted"] == [f["solo_runner"]]
    crop = db.one("SELECT crop FROM picks WHERE frame_id=?", (f["multi_pick"],))["crop"]
    assert crop and crop["x"] * 640 <= 400                # portrait crop around the matched face
    assert apply_character_profile(db, "vp")["demoted"] == []  # idempotent


def test_concept_profile_is_untouched(tmp_path):
    db = DB(tmp_path / "s.db")
    f = _seed(db, tmp_path, "vc")
    with db.write() as c:
        c.execute("UPDATE videos SET profile='concept' WHERE id='vc'")
    assert apply_character_profile(db, "vc")["applied"] is False
    assert db.one("SELECT role FROM picks WHERE frame_id=?", (f["off_pick"],))["role"] == "pick"
    assert caption_mode_for(db, "vc") == "concept"
    with db.write() as c:
        c.execute("UPDATE videos SET profile='character' WHERE id='vc'")
    assert caption_mode_for(db, "vc") == "character"


def test_profile_api(monkeypatch):
    from pipeline.api import app, db, jobs

    vid = f"fixture_profile_{int(time.time() * 1000)}"
    with db.write() as c:
        c.execute("INSERT INTO videos (id, path, name, slug, status) VALUES (?, ?, 'p.mp4', 'p', 'curating_300')",
                  (vid, f"/tmp/{vid}.mp4"))
    submitted = {}
    monkeypatch.setattr(jobs, "submit_openrouter_caption_job", lambda v, opts: submitted.update(opts) or 1)
    with TestClient(app) as client:
        assert client.patch(f"/api/videos/{vid}", json={"profile": "hero"}).status_code == 400
        res = client.patch(f"/api/videos/{vid}", json={"profile": "character"})
        assert res.status_code == 200 and res.json()["video"]["profile"] == "character"
        assert res.json()["profile_result"]["applied"] is False  # no target person yet
        r = client.post(f"/api/videos/{vid}/caption-openrouter", json={})
        assert r.json()["mode"] == "character" and submitted["mode"] == "character"
        r = client.post(f"/api/videos/{vid}/caption-openrouter", json={"mode": "style"})
        assert r.json()["mode"] == "style"
        assert client.post(f"/api/videos/{vid}/apply-profile").json()["profile"] == "character"
