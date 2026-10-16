"""GAP B4 (settings validation) and B5 (PATCH /api/picks rebalance)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from pipeline.api import app, db
from pipeline.config import DEFAULT_USER_SETTINGS, validate_user_settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_validate_types_ranges_and_unknown_keys():
    clean, errors, ignored = validate_user_settings({
        "default_k": 8.0,          # whole float accepted as int
        "sample_fps": 3,           # int accepted as float
        "snap_to_cuts": False,
        "trigger_word": "x",
        "not_a_setting": 1,
    })
    assert errors == [] and ignored == ["not_a_setting"]
    assert clean["default_k"] == 8 and isinstance(clean["default_k"], int)
    assert clean["sample_fps"] == 3.0 and isinstance(clean["sample_fps"], float)

    _, errors, _ = validate_user_settings({
        "default_k": 2.5,
        "sample_fps": float("nan"),
        "survivor_frac": 7,
        "snap_to_cuts": "yes",
        "default_rule": "everything",
        "weights": [1, 2],
        "max_edge": True,
    })
    joined = " | ".join(errors)
    for key in ("default_k", "sample_fps", "survivor_frac", "snap_to_cuts", "default_rule", "weights", "max_edge"):
        assert key in joined
    _, errors, _ = validate_user_settings({"target_curated_min": 300, "target_curated_max": 200})
    assert errors


def test_every_default_passes_its_own_validation():
    clean, errors, ignored = validate_user_settings(dict(DEFAULT_USER_SETTINGS))
    assert errors == [] and ignored == []
    assert set(clean) == set(DEFAULT_USER_SETTINGS)


def test_put_settings_rejects_invalid_and_reports_ignored(client):
    before = client.get("/api/settings").json()["settings"]
    res = client.put("/api/settings", json={"sample_fps": -1})
    assert res.status_code == 400
    assert "sample_fps" in res.json()["detail"]["message"]
    # nothing was stored
    assert client.get("/api/settings").json()["settings"]["sample_fps"] == before["sample_fps"]

    res = client.put("/api/settings", json={**before, "made_up": 1})
    assert res.status_code == 200
    assert "made_up" in res.json()["ignored_keys"]
    assert "made_up" not in db.get_setting("user_settings", {})


def _fixture_chunk(prefix: str):
    vid = f"{prefix}_{int(time.time() * 1000)}"
    with db.write() as c:
        c.execute("INSERT INTO videos (id, path, name, slug, status) VALUES (?, ?, 'p.mp4', 'p', 'curating_300')",
                  (vid, f"/tmp/{vid}.mp4"))
        chunk_id = c.execute("INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) "
                             "VALUES (?, 0, 0, 30, 5, 100)", (vid,)).lastrowid
        ids = []
        for i, role in enumerate(("pick", "pick", "runner_up", "runner_up")):
            fid = c.execute("INSERT INTO frames (video_id, chunk_id, t) VALUES (?, ?, ?)", (vid, chunk_id, float(i))).lastrowid
            c.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, ?)",
                      (fid, chunk_id, role, i, "keep" if role == "pick" else "candidate"))
            ids.append(fid)
    return vid, chunk_id, ids


def test_patch_pick_validates_values(client):
    _vid, _chunk, ids = _fixture_chunk("fixture_pick_validate")
    assert client.patch(f"/api/picks/{ids[0]}", json={"state": "maybe"}).status_code == 400
    assert client.patch(f"/api/picks/{ids[0]}", json={"role": "winner"}).status_code == 400
    assert client.patch(f"/api/picks/{ids[0]}", json={"state": "star"}).json()["pick"]["state"] == "star"


def test_patch_pick_rebalance_keeps_the_chunk_quota(client):
    _vid, chunk_id, ids = _fixture_chunk("fixture_pick_rebalance")
    res = client.patch(f"/api/picks/{ids[0]}", json={"role": "runner_up", "rebalance": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pick"]["role"] == "runner_up"
    promoted = body["rebalanced"]["swapped_in"]["id"]
    assert promoted in ids[2:]
    n_picks = db.one("SELECT COUNT(*) AS n FROM picks WHERE chunk_id=? AND role='pick'", (chunk_id,))["n"]
    assert n_picks == 2

    # without rebalance the old plain update still applies (the quota shrinks)
    client.patch(f"/api/picks/{ids[1]}", json={"role": "runner_up"})
    assert db.one("SELECT COUNT(*) AS n FROM picks WHERE chunk_id=? AND role='pick'", (chunk_id,))["n"] == 1
