"""Learn-as-you-tag image tagging: exemplars, calibration, localised suggestions, learning loop and API."""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.db import DB
from pipeline.image_tags import (
    DEFAULT_THRESHOLD,
    WINDOWS,
    ImageTagEngine,
    TagModel,
    calibrate,
    clamp_box,
    refine_box,
    square_crop,
)


class FakeClip:
    """Deterministic stand-in for the CLIP encoder: the embedding is the crop's mean colour (plus a small bias).

    Pure-colour objects on a black background therefore embed far apart, which is enough to test localisation
    and learning without the ONNX model.
    """

    def __init__(self):
        self.image_calls = 0

    def embed_images(self, frames_bgr, batch_size: int = 16):
        self.image_calls += len(frames_bgr)
        out = np.zeros((len(frames_bgr), 512), dtype=np.float32)
        for i, f in enumerate(frames_bgr):
            out[i, :3] = f.reshape(-1, 3).mean(axis=0) / 255.0
            out[i, 3] = 0.02
        return out / np.linalg.norm(out, axis=1, keepdims=True)

    def embed_texts(self, texts):
        out = np.zeros((len(texts), 512), dtype=np.float32)
        out[:, 100] = 1.0  # orthogonal to every image: the text prior stays silent
        return out


def make_frame(path, color=None, rect=(0.6, 0.6, 0.3, 0.3), size=(320, 240)):
    w, h = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    if color is not None:
        x, y, rw, rh = rect
        cv2.rectangle(img, (int(x * w), int(y * h)), (int((x + rw) * w), int((y + rh) * h)), color, -1)
    cv2.imwrite(str(path), img)
    return str(path)


RED, BLUE = (0, 0, 255), (255, 0, 0)


@pytest.fixture
def env(tmp_path):
    from pipeline.config import Settings

    settings = Settings(cache_dir=tmp_path / "cache", finished_dir=tmp_path / "finished", video_dir=tmp_path / "videos")
    db = DB(tmp_path / "state.db")
    engine = ImageTagEngine(db, settings, FakeClip())  # type: ignore[arg-type]
    with db.write() as c:
        c.execute("INSERT INTO videos (id, path, name, slug, status) VALUES ('v1', '/x/v1.mp4', 'v1.mp4', 'v1', 'curating_300')")
        c.execute("INSERT INTO videos (id, path, name, slug, status) VALUES ('v2', '/x/v2.mp4', 'v2.mp4', 'v2', 'curating_300')")
    return db, engine, tmp_path


def add_frame(db, video_id, t, path):
    with db.write() as c:
        cur = c.execute("INSERT INTO frames (video_id, t, preview_path, full_path) VALUES (?, ?, ?, ?)",
                        (video_id, t, path, path))
        return cur.lastrowid


# ------------------------------------------------------------------ pure helpers
def test_windows_cover_the_frame():
    assert WINDOWS.shape == (19, 4)
    assert tuple(WINDOWS[0]) == (0.0, 0.0, 1.0, 1.0)
    assert np.all(WINDOWS[:, 0] + WINDOWS[:, 2] <= 1.0001)
    assert np.all(WINDOWS[:, 1] + WINDOWS[:, 3] <= 1.0001)


def test_clamp_box_keeps_inside_unit_square():
    assert clamp_box({"x": -0.2, "y": 0.5, "w": 2.0, "h": 0.001}) == (0.0, 0.5, 1.0, 0.02)
    x, y, w, h = clamp_box([0.95, 0.95, 0.5, 0.5])
    assert x + w <= 1.0 and y + h <= 1.0


def test_square_crop_pads_outside_the_frame():
    img = np.full((100, 200, 3), 255, dtype=np.uint8)
    crop = square_crop(img, (0.0, 0.0, 1.0, 1.0), context=0.0)
    assert crop.shape[0] == crop.shape[1] == 200
    assert crop[0, 0].sum() == 0  # padded rows are black


def test_calibrate_separates_positives_from_negatives():
    rng = np.random.default_rng(0)
    base = rng.normal(size=512)
    pos = np.stack([base + rng.normal(scale=0.3, size=512) for _ in range(6)])
    neg = np.stack([rng.normal(size=512) for _ in range(6)])
    pos /= np.linalg.norm(pos, axis=1, keepdims=True)
    neg /= np.linalg.norm(neg, axis=1, keepdims=True)
    stats = calibrate(pos.astype(np.float32), neg.astype(np.float32))
    assert stats.accuracy == 1.0
    assert 0.70 <= stats.threshold <= 0.95
    assert calibrate(pos[:1].astype(np.float32), neg.astype(np.float32)).threshold == DEFAULT_THRESHOLD


def test_negative_exemplar_suppresses_score():
    p = np.zeros((1, 512), np.float32); p[0, 0] = 1
    n = np.zeros((1, 512), np.float32); n[0, 1] = 1
    near_neg = np.zeros((1, 512), np.float32); near_neg[0, :2] = [0.6, 0.8]
    no_neg = TagModel(1, "t", None, p, np.zeros((0, 512), np.float32), None, threshold=0.5)
    with_neg = TagModel(1, "t", None, p, n, None, threshold=0.5)
    assert with_neg.score(near_neg)[0] < no_neg.score(near_neg)[0] * 0.5


def test_refine_box_localises_the_object():
    scores = np.zeros(len(WINDOWS), dtype=np.float32)
    # the bottom-right half and third windows fire
    for i, (x, y, w, h) in enumerate(WINDOWS):
        if x >= 0.45 and y >= 0.45 and w < 0.99:
            scores[i] = 0.9
    x, y, w, h = refine_box(WINDOWS, scores)
    assert x >= 0.45 and y >= 0.45


# ------------------------------------------------------------------ engine
def test_mark_region_learns_and_suggests_across_videos(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED, (0.6, 0.6, 0.3, 0.3)))
    f2 = add_frame(db, "v1", 2.0, make_frame(tmp / "b.png", RED, (0.55, 0.55, 0.35, 0.35)))
    f3 = add_frame(db, "v1", 3.0, make_frame(tmp / "c.png", BLUE, (0.1, 0.1, 0.3, 0.3)))
    other_video = add_frame(db, "v2", 1.0, make_frame(tmp / "d.png", RED, (0.6, 0.6, 0.3, 0.3)))

    res = engine.add_region(f1, {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3}, tag_name="Red block", category="equipment")
    assert res["tag_id"] and res["region_id"]
    tag = engine.get_tag(res["tag_id"])
    assert tag["positive_count"] == 1 and tag["confirmed_count"] == 1 and tag["category"] == "equipment"
    assert tag["cover_url"].endswith("/crop")

    # Live suggestion on another frame, located where the red object is
    live = engine.frame_tags(f2)
    assert [s["name"] for s in live["suggestions"]] == ["Red block"]
    box = live["suggestions"][0]["box"]
    assert box["x"] + box["w"] > 0.6 and box["y"] + box["h"] > 0.6
    assert engine.frame_tags(f3)["suggestions"] == []

    # Video-wide pass on a different video: tags are global
    out = engine.suggest_for_video("v2")
    assert out["suggested"] == 1
    tags = engine.video_tag_map("v2")[other_video]
    assert tags[0]["status"] == "suggested" and tags[0]["name"] == "Red block"

    # Confirmed frames are excluded from live suggestions and keep their state
    assert engine.frame_tags(f1)["suggestions"] == []
    assert [t["name"] for t in engine.frame_tags(f1)["confirmed"]] == ["Red block"]


def test_accept_and_reject_teach_the_model(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED))
    f2 = add_frame(db, "v1", 2.0, make_frame(tmp / "b.png", RED))
    f3 = add_frame(db, "v1", 3.0, make_frame(tmp / "c.png", (0, 60, 255)))  # orange: near red, not the tag
    tag_id = engine.add_region(f1, {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3}, tag_name="red")["tag_id"]
    engine.suggest_for_video("v1")

    engine.accept_suggestion(f2, tag_id)
    assert engine.get_tag(tag_id)["positive_count"] == 2
    assert [t["name"] for t in engine.frame_tags(f2)["confirmed"]] == ["red"]

    engine.reject_suggestion(f3, tag_id, {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3})
    tag = engine.get_tag(tag_id)
    assert tag["negative_count"] == 1
    assert engine.frame_tags(f3)["rejected"][0]["tag_id"] == tag_id
    # A rejected frame is never re-suggested by a later pass
    engine.suggest_for_video("v1")
    assert not any(t["tag_id"] == tag_id for t in engine.video_tag_map("v1").get(f3, []))


def test_remove_frame_tag_drops_its_exemplar(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED))
    res = engine.add_region(f1, [0.6, 0.6, 0.3, 0.3], tag_name="red")
    assert engine.remove_frame_tag(f1, res["tag_id"])
    assert engine.get_tag(res["tag_id"])["positive_count"] == 0
    assert engine.frame_tags(f1)["confirmed"] == []


def test_exemplars_survive_frame_deletion(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED))
    tag_id = engine.add_region(f1, [0.6, 0.6, 0.3, 0.3], tag_name="red")["tag_id"]
    with db.write() as c:
        c.execute("DELETE FROM frames WHERE id=?", (f1,))  # what a re-extraction does
    assert engine.get_tag(tag_id)["positive_count"] == 1
    assert engine.list_regions(tag_id)[0]["frame_id"] is None


def test_context_negatives_are_learned_and_hidden(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED, (0.6, 0.6, 0.3, 0.3)))
    res = engine.add_region(f1, [0.6, 0.6, 0.3, 0.3], tag_name="red")
    tag = engine.get_tag(res["tag_id"])
    # the black background around the marked box became counter-examples, invisible in the UI lists
    assert tag["context_count"] > 0 and tag["negative_count"] == 0
    assert all(r["source"] != "context" for r in engine.list_regions(res["tag_id"]))
    assert all(r["source"] != "context" for r in engine.frame_tags(f1)["regions"])
    # they belong to the region: forgetting the region forgets them
    engine.delete_region(res["region_id"])
    assert engine.get_tag(res["tag_id"])["context_count"] == 0


def test_window_cache_is_reused(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED))
    add_frame(db, "v1", 2.0, make_frame(tmp / "b.png", RED))
    engine.add_region(f1, [0.6, 0.6, 0.3, 0.3], tag_name="red")
    engine.suggest_for_video("v1")
    calls = engine.classifier.image_calls
    engine.suggest_for_video("v1")
    assert engine.classifier.image_calls == calls  # second pass is a matrix product only
    # compute_missing=False never decodes images
    add_frame(db, "v1", 3.0, make_frame(tmp / "c.png", RED))
    res = engine.suggest_for_video("v1", compute_missing=False)
    assert engine.classifier.image_calls == calls and res["scored"] == 2


def test_merge_and_rename(env):
    db, engine, tmp = env
    f1 = add_frame(db, "v1", 1.0, make_frame(tmp / "a.png", RED))
    f2 = add_frame(db, "v1", 2.0, make_frame(tmp / "b.png", RED))
    a = engine.add_region(f1, [0.6, 0.6, 0.3, 0.3], tag_name="rope")["tag_id"]
    b = engine.add_region(f2, [0.6, 0.6, 0.3, 0.3], tag_name="Rope (red)")["tag_id"]
    merged = engine.merge_tags(b, a)
    assert merged["positive_count"] == 2 and merged["confirmed_count"] == 2
    assert engine.get_tag(b) is None
    with pytest.raises(ValueError):
        engine.create_tag("  ")
    engine.create_tag("chair")
    with pytest.raises(ValueError):
        engine.update_tag(a, name="chair")
    assert engine.update_tag(a, name="red rope", prompt="a photo of red rope")["name"] == "red rope"


# ------------------------------------------------------------------ API
def test_tag_api_flow(tmp_path, monkeypatch):
    from pipeline.api import app, db, jobs

    monkeypatch.setattr(jobs.image_tags, "classifier", FakeClip())
    vid = f"fixture_tags_{int(time.time() * 1000)}"
    with db.write() as c:
        c.execute("INSERT INTO videos (id, path, name, slug, status) VALUES (?, ?, 'n.mp4', 'n', 'curating_300')",
                  (vid, f"/tmp/{vid}.mp4"))
    f1 = add_frame(db, vid, 1.0, make_frame(tmp_path / "a.png", RED))
    f2 = add_frame(db, vid, 2.0, make_frame(tmp_path / "b.png", RED))

    with TestClient(app) as client:
        r = client.post(f"/api/frames/{f1}/tags", json={"box": {"x": 0.6, "y": 0.6, "w": 0.3, "h": 0.3},
                                                         "tag_name": f"api red {vid}"})
        assert r.status_code == 200, r.text
        body = r.json()
        tag_id = body["tag"]["id"]
        assert body["frame"]["confirmed"][0]["tag_id"] == tag_id

        r = client.get(f"/api/frames/{f2}/tags")
        assert any(s["tag_id"] == tag_id for s in r.json()["suggestions"])

        r = client.post(f"/api/frames/{f2}/tags/{tag_id}/accept", json={})
        assert r.status_code == 200 and r.json()["tag"]["positive_count"] == 2

        tags = client.get("/api/tags").json()
        assert any(t["id"] == tag_id for t in tags["tags"]) and "equipment" in tags["categories"]
        detail = client.get(f"/api/tags/{tag_id}").json()
        assert len(detail["regions"]) == 2
        assert client.get(detail["regions"][0]["crop_url"]).status_code == 200

        cur = client.get(f"/api/videos/{vid}/curation-300").json()
        assert cur["picks"] == []  # no picks in this fixture; tags ride on frames that are picks

        assert client.get(f"/api/videos/{vid}/tags").json()["tags"][0]["confirmed"] == 2
        assert client.post(f"/api/frames/{f2}/tags", json={"box": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}).status_code == 400
        assert client.post("/api/frames/999999/tags", json={"box": {"x": 0, "y": 0, "w": 0.5, "h": 0.5},
                                                            "tag_name": "x"}).status_code == 404

        r = client.post(f"/api/videos/{vid}/suggest-tags")
        assert r.status_code == 200 and r.json()["status"] == "queued"

        assert client.delete(f"/api/frames/{f2}/tags/{tag_id}").status_code == 200
        assert client.delete(f"/api/tags/{tag_id}").status_code == 200
        assert client.get(f"/api/tags/{tag_id}").status_code == 404


def test_confirmed_tags_become_caption_hints():
    from pipeline.openrouter import tag_hint_text

    assert tag_hint_text([]) == "" and tag_hint_text(None) == ""
    text = tag_hint_text(["red rope", "chair", "red rope", " "])
    assert "red rope, chair." in text and text.count("red rope") == 1
