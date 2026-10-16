"""Phase F2: analysis cache + frame_features, content cut detection, quality v2, DINOv2 selection embedder,
axis caps and cross-dataset dedup in reselect.

Synthetic videos (PyAV) in tmp_path, temp DBs or dedicated fixture video ids only - never the user's library.
Model downloads are mocked.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import pipeline.api as api
from pipeline.analysis import AnalysisCache, crop_signature, open_analysis_cache, sampling_interval, write_frame_features
from pipeline.autochunk import AutoChunker
from pipeline.config import DEFAULT_USER_SETTINGS, Settings, validate_user_settings
from pipeline.cuts import detect_content_cuts, find_cuts
from pipeline.db import DB
from pipeline.decode import sample_chunk_frames
from pipeline.embedders import Dinov2Embedder, letterbox_preprocess, selection_embeddings_for_rows
from pipeline.indexer import Indexer
from pipeline.jobs import JobManager
from pipeline.quality import (
    IqaScorer,
    border_black_fraction,
    combine,
    noise_sigma,
    normalise_components,
    quality_gates,
    raw_components,
)
from pipeline.reselect import frame_axes, normalise_axis_caps, ReselectError
from pipeline.scoring import ScoringEngine


# ----------------------------------------------------------------------------- helpers
def _texture(seed: int, h: int = 120, w: int = 160, block: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (h // block, w // block, 3), dtype=np.uint8)
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


def _l2(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def _env(tmp_path: Path, vid: str, frames: list[np.ndarray], fps: int = 10, chunks=((0.0, None),)):
    video = tmp_path / "videos" / f"{vid}.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    _write_video(video, frames, fps)
    dur = len(frames) / fps
    settings = Settings(video_dir=tmp_path / "videos", cache_dir=tmp_path / "cache", finished_dir=tmp_path / "fin")
    settings.ensure_dirs()
    db = DB(tmp_path / "state.db")
    with db.write() as conn:
        conn.execute(
            "INSERT INTO videos (id, path, name, slug, status, duration, width, height) "
            "VALUES (?, ?, ?, ?, 'chunked', ?, ?, ?)",
            (vid, str(video), f"{vid}.mp4", vid, dur, frames[0].shape[1], frames[0].shape[0]),
        )
        for pos, (a, b) in enumerate(chunks):
            conn.execute(
                "INSERT INTO chunks (id, video_id, position, start_s, end_s, k, budget_pct) VALUES (?, ?, ?, ?, ?, 5, ?)",
                (pos + 1, vid, pos, a, b if b is not None else dur - 0.1, 100.0 / len(chunks)),
            )
    return video, settings, db


def _fake_classifier(dim: int = 32) -> MagicMock:
    clf = MagicMock()
    rng = np.random.default_rng(11)
    clf.embed_texts.side_effect = lambda texts: _l2(rng.normal(size=(len(texts), dim))).astype(np.float32)
    clf.embed_images.side_effect = lambda imgs, batch_size=32: _l2(
        np.stack([np.resize(cv2.resize(i, (8, 4)).astype(np.float32).ravel(), dim) + 1.0 for i in imgs])
    ).astype(np.float32)
    return clf


def _manager(db: DB, settings: Settings) -> JobManager:
    jm = JobManager(db, settings)
    jm.face_engine = MagicMock()
    jm.face_engine.detect_faces.return_value = []
    jm.classifier = _fake_classifier()
    jm.scoring_engine = ScoringEngine(jm.classifier)
    return jm


def _shutdown(jm: JobManager) -> None:
    jm.decode_executor.shutdown(wait=False)
    jm.caption_executor.shutdown(wait=False)


# ----------------------------------------------------------------------------- settings
def test_phase_f2_settings_defaults_and_validation():
    assert DEFAULT_USER_SETTINGS["reuse_analysis_cache"] is True
    assert DEFAULT_USER_SETTINGS["cut_detector"] == "content"
    assert DEFAULT_USER_SETTINGS["quality_v2"] is True
    assert DEFAULT_USER_SETTINGS["iqa_model"] == "off"
    assert DEFAULT_USER_SETTINGS["selection_embedder"] == "clip"
    assert DEFAULT_USER_SETTINGS["reselect_axis_caps"] == {}
    assert DEFAULT_USER_SETTINGS["reselect_cross_dataset_dedup"] is False
    clean, errors, ignored = validate_user_settings({
        "cut_detector": "legacy", "selection_embedder": "dinov2", "iqa_model": "topiq_nr",
        "reselect_axis_caps": {"equipment": 0.25, "face": 1}, "quality_v2": False, "cut_min_scene_s": 1.0,
    })
    assert not errors and not ignored
    assert clean["reselect_axis_caps"] == {"equipment": 0.25, "face": 1.0}
    _, errors, _ = validate_user_settings({"cut_detector": "transnet", "selection_embedder": "siglip",
                                           "iqa_model": "musiq", "reselect_axis_caps": {"pose": 0.3},
                                           "cut_adaptive_threshold": 0.1})
    assert len(errors) == 5
    _, errors, _ = validate_user_settings({"reselect_axis_caps": {"solo": 1.5}})
    assert errors


# ----------------------------------------------------------------------------- 1. analysis cache
def test_analysis_cache_serves_samples_without_decoding(tmp_path):
    frames = [_texture(300 + i) for i in range(80)]  # 8 s at 10 fps, every frame different
    video, settings, db = _env(tmp_path, "v_f2_cache", frames)
    sampler = MagicMock(wraps=sample_chunk_frames)

    cache = AnalysisCache(settings.cache_dir, "v_f2_cache", video)
    assert cache.enabled and not cache.path.exists()
    stats1: dict = {}
    first = cache.sample(sampler, video, 0.0, 5.0, sample_fps=2.0, max_samples=50, max_dim=512, window_peak=3,
                         stats_out=stats1)
    assert sampler.call_count == 1 and stats1["mode"] == "window_peak" and stats1["cache_decoded_ranges"] == 1
    assert 9 <= len(first) <= 11
    cache.save()
    assert cache.path.is_file()

    # A new job: same range -> no decode, identical samples (JPEG round-tripped both times)
    sampler.reset_mock()
    again = AnalysisCache(settings.cache_dir, "v_f2_cache", video)
    stats2: dict = {}
    second = again.sample(sampler, video, 0.0, 5.0, sample_fps=2.0, max_samples=50, max_dim=512, window_peak=3,
                          stats_out=stats2)
    assert sampler.call_count == 0 and stats2["cache_decoded_ranges"] == 0
    assert [t for t, _ in first] == [t for t, _ in second]
    assert all(np.array_equal(a, b) for (_, a), (_, b) in zip(first, second))
    assert stats1["motion"] == stats2["motion"]

    # A sparser request over a covered range is served from the cache; an extended chunk decodes only the gap
    third = again.sample(sampler, video, 0.0, 5.0, sample_fps=1.0, max_samples=50, max_dim=512, window_peak=3)
    assert sampler.call_count == 0 and 4 <= len(third) <= 6
    again.sample(sampler, video, 0.0, 7.5, sample_fps=2.0, max_samples=50, max_dim=512, window_peak=3)
    assert sampler.call_count == 1
    (_, gap_a, gap_b), _ = sampler.call_args
    assert gap_a == pytest.approx(5.0, abs=0.01) and gap_b == pytest.approx(7.5)
    # A different mode is not served from window-peak samples
    again.sample(sampler, video, 0.0, 2.0, sample_fps=2.0, max_samples=50, max_dim=512, window_peak=0)
    assert sampler.call_count == 2
    assert sampling_interval(0.0, 10.0, 4.0, 20) == pytest.approx(0.5)


def test_analysis_cache_embeddings_and_invalidation(tmp_path):
    frames = [_texture(400 + i) for i in range(40)]
    video, settings, db = _env(tmp_path, "v_f2_inval", frames)
    cache = open_analysis_cache(db, settings.cache_dir, "v_f2_inval", video)
    cache.sample(sample_chunk_frames, video, 0.0, 3.0, sample_fps=2.0, max_samples=20, max_dim=512)
    crop = crop_signature(None, None)
    cache.put_embeddings("clip", "m", crop, [0.0, 0.5], np.eye(2, 4, dtype=np.float32))
    cache.save()
    write_frame_features(db, "v_f2_inval", [{"t": 0.0, "lap_var": 10.0, "gates": []}])
    write_frame_features(db, "v_f2_inval", [{"t": 0.0, "face_count": 2}])  # partial update keeps lap_var
    row = db.one("SELECT * FROM frame_features WHERE video_id='v_f2_inval' AND t=0.0")
    assert row["lap_var"] == 10.0 and row["face_count"] == 2 and row["gates"] == []

    reopened = open_analysis_cache(db, settings.cache_dir, "v_f2_inval", video)
    assert not reopened.invalidated
    got = reopened.get_embeddings("clip", "m", crop, [0.5, 0.0, 9.9])
    assert set(got) == {0, 1} and np.allclose(got[0], [0, 1, 0, 0])
    assert reopened.get_embeddings("clip", "m", crop_signature({"top": 10}, None), [0.0]) == {}
    assert reopened.get_embeddings("clip", "other-model", crop, [0.0]) == {}

    # Touching the file (new mtime) invalidates the npz and the frame_features rows
    st = os.stat(video)
    os.utime(video, (st.st_atime, st.st_mtime + 100))
    stale = open_analysis_cache(db, settings.cache_dir, "v_f2_inval", video)
    assert stale.invalidated and stale.t.size == 0 and not stale.path.exists()
    assert db.one("SELECT COUNT(*) AS n FROM frame_features WHERE video_id='v_f2_inval'")["n"] == 0
    # No file -> no caching at all
    assert not AnalysisCache(settings.cache_dir, "x", tmp_path / "missing.mp4").enabled


def test_extract_chunk_reuses_analysis_cache_and_writes_features(tmp_path):
    frames = [_texture(500 + i) for i in range(60)]
    # A blurred stretch in the middle: quality v2's relative blur gate drops it
    for i in range(20, 26):
        frames[i] = cv2.GaussianBlur(frames[i], (0, 0), 6)
    video, settings, db = _env(tmp_path, "v_f2_extract", frames)
    jm = _manager(db, settings)
    fake_decode = lambda v, ts, on_frame: [on_frame(t, frames[0]) for t in ts]  # noqa: E731
    with patch("pipeline.jobs.decode_exact_frames", side_effect=fake_decode), \
         patch("pipeline.jobs.sample_chunk_frames", wraps=sample_chunk_frames) as sampler:
        res1 = jm._execute_extract_chunk(1, k=5)
        assert sampler.call_count == 1
        n_embed_1 = sum(len(c.args[0]) for c in jm.classifier.embed_images.call_args_list)
        res2 = jm._execute_extract_chunk(1, k=5)
        assert sampler.call_count == 1  # nothing decoded the second time
    n_embed_2 = sum(len(c.args[0]) for c in jm.classifier.embed_images.call_args_list)
    assert n_embed_2 == n_embed_1  # CLIP embeddings came from the cache
    s1, s2 = res1["stats"], res2["stats"]
    assert s1["quality_v2"] is True and s1["clip_embeddings"]["cached"] == 0
    assert s2["clip_embeddings"] == {"computed": 0, "cached": s1["clip_embeddings"]["computed"]}
    assert s2["sampling"]["analysis_cache"]["decoded"] == 0 and s2["sampling"]["analysis_cache"]["served"] > 0
    assert s1["quality_gates"].get("blur", 0) >= 1
    assert s1["frame_features"] > 0
    feats = db.all("SELECT * FROM frame_features WHERE video_id='v_f2_extract' ORDER BY t")
    assert any("blur" in (f["gates"] or []) for f in feats)
    passed = [f for f in feats if f["gates"] == []]
    assert passed and all(f["q_components"] and "raw" in f["q_components"] for f in passed)
    assert all(f["emb_refs"]["clip"].endswith("#emb_clip") for f in passed)
    stored = db.all("SELECT zs_scores, metrics FROM frames WHERE chunk_id=1")
    assert stored and all(s["zs_scores"]["quality_version"] == 2 for s in stored)
    assert all("q_raw" in s["metrics"] and "border_black" in s["metrics"] for s in stored)
    # Same picks both times: the cache makes re-extraction deterministic
    assert res1["picks_count"] == res2["picks_count"]

    # Setting off: decode again
    db.set_setting("user_settings", {"reuse_analysis_cache": False, "quality_v2": False})
    with patch("pipeline.jobs.decode_exact_frames", side_effect=fake_decode), \
         patch("pipeline.jobs.sample_chunk_frames", wraps=sample_chunk_frames) as sampler:
        res3 = jm._execute_extract_chunk(1, k=5)
        assert sampler.call_count == 1
    assert res3["stats"]["quality_v2"] is False and "analysis_cache" not in res3["stats"]["sampling"]
    stored = db.all("SELECT zs_scores FROM frames WHERE chunk_id=1")
    assert all("quality_version" not in s["zs_scores"] for s in stored)
    _shutdown(jm)


# ----------------------------------------------------------------------------- 2. cut detection
def test_find_cuts_adaptive_rule_and_fades():
    fps = 10.0
    n = 120
    t = np.arange(n) / fps
    scores = np.full(n, 3.0)
    scores[0] = 0.0
    scores[30] = 60.0                # hard cut
    scores[50:56] = 25.0             # fast pan: high but sustained -> ratio ~1, no cut
    scores[90] = 40.0
    scores[91] = 38.0                # two spikes in a row: min scene length keeps one
    luma = np.full(n, 120.0)
    luma[60:70] = np.linspace(110, 15, 10)  # fade out
    luma[70:75] = 2.0                        # black
    luma[75:80] = np.linspace(20, 110, 5)   # fade in
    cuts, zones = find_cuts(t, scores, luma, adaptive_threshold=3.0, min_content_val=15.0, min_scene_s=0.5,
                            fade_threshold=12.0)
    assert 3.0 in cuts
    assert not any(5.0 <= c <= 5.6 for c in cuts)
    assert sum(1 for c in cuts if 8.9 <= c <= 9.2) == 1
    fade_cut = [c for c in cuts if 6.5 <= c <= 8.0]
    assert fade_cut == [7.2]
    assert zones and zones[0]["kind"] == "fade" and zones[0]["start"] <= 6.1 and zones[0]["end"] >= 7.9
    # A dark run at the very start is not a transition
    luma2 = luma.copy()
    luma2[:5] = 0.0
    _, zones2 = find_cuts(t, scores, luma2)
    assert all(z["start"] > 1.0 for z in zones2)


def _shots_video(path: Path, fps: int = 10) -> list[np.ndarray]:
    a, b, c = _texture(1), _texture(2), _texture(3)
    frames = [np.roll(a, i, axis=1) for i in range(30)]               # slow pan, 0.0-2.9 s
    frames += [b.copy() for _ in range(30)]                            # cut at 3.0 s
    frames += [(b.astype(np.float32) * (1 - (i + 1) / 10)).astype(np.uint8) for i in range(10)]  # fade out
    frames += [np.zeros_like(a) for _ in range(5)]                     # black 7.0-7.4 s
    frames += [(c.astype(np.float32) * ((i + 1) / 10)).astype(np.uint8) for i in range(10)]      # fade in
    frames += [c.copy() for _ in range(35)]
    _write_video(path, frames, fps)
    return frames


def test_detect_content_cuts_is_frame_accurate(tmp_path):
    video = tmp_path / "shots.mp4"
    _shots_video(video)
    seen: list[float] = []
    res = detect_content_cuts(video, on_frame=lambda t, f: seen.append(t))
    assert res["frames"] == 120 and len(seen) == 120
    hard = [c for c in res["cuts"] if c < 5.0]
    assert hard == [pytest.approx(3.0, abs=0.001)]  # the first frame of the new shot, exactly
    fades = [c for c in res["cuts"] if 6.0 <= c <= 8.5]
    assert len(fades) == 1 and 7.0 <= fades[0] <= 7.4
    assert len(res["cuts"]) == 2
    assert res["transitions"] and res["transitions"][0]["kind"] == "fade"


def test_filmstrip_uses_content_detector_with_fallback(tmp_path):
    video = tmp_path / "videos" / "strip.mp4"
    video.parent.mkdir(parents=True)
    _shots_video(video)
    settings = Settings(video_dir=tmp_path / "videos", cache_dir=tmp_path / "cache", finished_dir=tmp_path / "fin")
    settings.ensure_dirs()
    db = DB(tmp_path / "state.db")
    with db.write() as conn:
        conn.execute("INSERT INTO videos (id, path, name, slug, duration) VALUES ('v_strip', ?, 'strip.mp4', 'strip', 12.0)",
                     (str(video),))
    idx = Indexer(db, settings)
    data = idx.get_or_build_filmstrip("v_strip")
    assert data["cut_detector"] == "content" and data["thumbnails"]
    assert any(abs(c - 3.0) < 0.001 for c in data["cuts"]) and data["transitions"]
    assert (settings.filmstrip_dir / "v_strip" / data["thumbnails"][0]["file"]).is_file()

    with patch("pipeline.cuts.detect_content_cuts") as det:  # cached: no second pass
        assert idx.get_or_build_filmstrip("v_strip")["cuts"] == data["cuts"]
        det.assert_not_called()

    db.set_setting("user_settings", {"cut_detector": "legacy"})
    legacy = idx.get_or_build_filmstrip("v_strip")
    assert legacy["cut_detector"] == "legacy" and legacy["transitions"] == []

    db.set_setting("user_settings", {"cut_detector": "content", "cut_min_scene_s": 1.0})
    with patch("pipeline.cuts.detect_content_cuts", side_effect=RuntimeError("boom")):
        fallback = idx.get_or_build_filmstrip("v_strip")
    assert fallback["cut_detector"] == "legacy" and fallback["cut_config"]["cut_detector"] == "content"
    with patch("pipeline.cuts.detect_content_cuts") as det:  # the failed config is not retried every call
        idx.get_or_build_filmstrip("v_strip")
        det.assert_not_called()


def test_autochunk_suggestions_follow_content_cuts(tmp_path):
    a, b, c = _texture(11, 72, 96), _texture(12, 72, 96), _texture(13, 72, 96)
    frames = [a] * 60 + [b] * 60 + [c] * 60  # 3 shots x 12 s at 5 fps
    video, settings, db = _env(tmp_path, "v_f2_chunk", frames, fps=5)
    chunker = AutoChunker(db, settings)
    scenes = chunker.plan_scenes("v_f2_chunk", min_scene_s=5.0)
    assert len(scenes) == 3
    assert scenes[0][1] == pytest.approx(12.0, abs=0.01) and scenes[1][1] == pytest.approx(24.0, abs=0.01)


# ----------------------------------------------------------------------------- 3. quality v2
def test_quality_gates():
    sharp = {"tile_sharp": 400.0, "mean_luma": 120.0, "clipping": 0.0, "border_black": 0.0}
    metrics = [dict(sharp) for _ in range(6)] + [
        {**sharp, "mean_luma": 8.0},                 # too dark
        {**sharp, "mean_luma": 250.0},               # blown
        {**sharp, "clipping": 0.6},                  # clipping
        {**sharp, "border_black": 0.6},              # letterbox black
        {**sharp, "tile_sharp": 60.0},               # above the absolute floor 15, below 25 % of the chunk median
    ]
    reasons = quality_gates(metrics)
    assert all(r == [] for r in reasons[:6])
    assert reasons[6] == ["too_dark"] and reasons[7] == ["blown_out"] and reasons[8] == ["clipping"]
    assert reasons[9] == ["letterbox_black"] and reasons[10] == ["blur"]
    # A soft chunk is judged against itself: 60 passes when the whole chunk is soft
    assert quality_gates([{**sharp, "tile_sharp": 60.0}] * 4) == [[]] * 4

    img = _texture(5)
    boxed = np.zeros((120, 160, 3), dtype=np.uint8)
    boxed[40:80, 50:110] = img[40:80, 50:110]
    gray = cv2.cvtColor(boxed, cv2.COLOR_BGR2GRAY)
    assert border_black_fraction(gray) > 0.7
    assert border_black_fraction(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)) == 0.0


def test_quality_components_and_normalisation():
    clean = _texture(7, 240, 320)
    rng = np.random.default_rng(1)
    noisy = np.clip(clean.astype(np.float32) + rng.normal(0, 18, clean.shape), 0, 255).astype(np.uint8)
    soft = cv2.GaussianBlur(clean, (0, 0), 3)
    g = lambda im: cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)  # noqa: E731
    assert noise_sigma(g(noisy)) > 3 * noise_sigma(g(clean))

    face = {"box": [120, 60, 80, 80], "score": 0.9, "landmarks": [[145, 90], [175, 90], [160, 105], [148, 120], [172, 120]]}
    profile = {**face, "landmarks": [[145, 90], [175, 90], [190, 105], [148, 120], [172, 120]]}
    r_clean = raw_components(clean, {"motion_energy": 1.0}, [face], native_scale=2.0)
    r_soft = raw_components(soft, {"motion_energy": 6.0}, None)
    r_profile = raw_components(clean, {}, [profile])
    assert r_clean["subject"] == "face" and r_soft["subject"] == "centre"
    assert r_clean["sharp"] > 5 * r_soft["sharp"]
    assert r_clean["face_short_px"] == 160.0 and r_soft["face"] is None
    assert r_profile["face"] < r_clean["face"]

    norm = normalise_components([r_clean, r_soft, r_profile], [1, 1, 1])
    assert norm[0]["sharp"] > norm[1]["sharp"]
    assert norm[0]["motion"] > norm[1]["motion"]  # lower motion is better
    assert norm[1]["face"] == 0.5                   # no face: neutral
    s, q = combine(norm[0])
    assert 0.0 <= s <= 1.0 and 0.0 <= q <= 1.0
    # Video-level reference: a chunk of 1 frame is ranked against the rest of the video
    alone = normalise_components([r_soft], [9], {"sharp": [r_clean["sharp"], r_clean["sharp"] * 2]})
    assert alone[0]["sharp"] == 0.0


def test_scoring_engine_quality_v2_and_learned_blend():
    clf = _fake_classifier()
    engine = ScoringEngine(clf)
    sharp, soft = _texture(21), cv2.GaussianBlur(_texture(22), (0, 0), 3)

    def cands():
        return [{"img_bgr": im.copy(), "t": float(i), "chunk_id": 1, "metrics": {}} for i, im in enumerate([sharp, soft, sharp])]

    emb = _l2(np.random.default_rng(3).normal(size=(3, 32))).astype(np.float32)
    v1 = engine.score_candidates_batch(cands(), emb)
    assert all("quality_version" not in c["scores"] for c in v1)

    engine.configure_quality(True)
    v2 = engine.score_candidates_batch(cands(), emb)
    assert all(c["scores"]["quality_version"] == 2 and "q_components" in c["scores"] for c in v2)
    assert v2[0]["scores"]["sharpness"] > v2[1]["scores"]["sharpness"]
    assert "q_raw" in v2[0]["metrics"]

    weights = {"sharpness": 1.0, "quality": 0.0, "persons": 0.0, "objects": 0.0, "face": 0.0}
    engine.configure_learned(weights, blend=1.0)
    blended = engine.score_candidates_batch(cands(), emb)
    for b in blended:
        assert b["scores"]["composite"] == pytest.approx(b["scores"]["sharpness"], abs=1e-3)
        assert "composite_base" in b["scores"]


def test_iqa_scorer_clip_probe_and_offline_topiq(tmp_path):
    settings = Settings(cache_dir=tmp_path / "cache")
    clf = MagicMock()
    good, bad = np.array([1.0, 0.0], dtype=np.float32), np.array([0.0, 1.0], dtype=np.float32)
    clf.embed_texts.return_value = np.stack([good, bad])
    scorer = IqaScorer(settings)
    out = scorer.score("clip_iqa", [np.zeros((4, 4, 3), np.uint8)] * 2, np.stack([good, bad]), classifier=clf)
    assert out[0] > 0.99 and out[1] < 0.01
    assert scorer.score("off", [], None) is None

    with patch("huggingface_hub.hf_hub_download", side_effect=OSError("offline")):
        offline = IqaScorer(settings)
        assert offline.score("topiq_nr", [np.zeros((8, 8, 3), np.uint8)]) is None
    assert "TOPIQ unavailable" in offline.error

    class FakeSess:
        def get_inputs(self):
            return [MagicMock(name="image")]

        def run(self, _out, feeds):
            x = next(iter(feeds.values()))
            assert x.shape == (1, 3, 384, 384) and x.max() <= 1.0
            return [np.array([[float(x.mean())]], dtype=np.float32)]

    with_model = IqaScorer(settings)
    with_model._sess = FakeSess()
    vals = with_model.score("topiq_nr", [np.full((20, 30, 3), 255, np.uint8), np.zeros((20, 30, 3), np.uint8)])
    assert vals[0] > vals[1]


def test_extract_reports_iqa_fallback(tmp_path):
    frames = [_texture(700 + i) for i in range(30)]
    video, settings, db = _env(tmp_path, "v_f2_iqa", frames)
    db.set_setting("user_settings", {"iqa_model": "topiq_nr"})
    jm = _manager(db, settings)
    jm.iqa.error = "TOPIQ unavailable: offline"  # loader already failed: no download attempt
    with patch("pipeline.jobs.decode_exact_frames", side_effect=lambda v, ts, on_frame: [on_frame(t, frames[0]) for t in ts]):
        res = jm._execute_extract_chunk(1, k=5)
    assert res["stats"]["iqa"] == {"model": "topiq_nr", "active": False, "error": "TOPIQ unavailable: offline"}

    db.set_setting("user_settings", {"iqa_model": "clip_iqa"})
    with patch("pipeline.jobs.decode_exact_frames", side_effect=lambda v, ts, on_frame: [on_frame(t, frames[0]) for t in ts]):
        res = jm._execute_extract_chunk(1, k=5)
    assert res["stats"]["iqa"]["active"] is True
    stored = db.all("SELECT metrics, zs_scores FROM frames WHERE chunk_id=1")
    assert all("iqa" in s["metrics"]["q_raw"] and "iqa" in s["zs_scores"]["q_components"] for s in stored)
    _shutdown(jm)


# ----------------------------------------------------------------------------- 4. DINOv2 embedder
class _FakeDinoSession:
    def get_inputs(self):
        inp = MagicMock()
        inp.name = "pixel_values"
        return [inp]

    def run(self, _out, feeds):
        x = feeds["pixel_values"]
        b = x.shape[0]
        hidden = np.zeros((b, 257, 384), dtype=np.float32)
        hidden[:, 0, :3] = x.reshape(b, 3, -1).mean(axis=2)  # CLS carries the mean colour
        hidden[:, 0, 3] = 1.0
        return [hidden]


def _fake_dino(settings: Settings) -> Dinov2Embedder:
    emb = Dinov2Embedder(settings)
    emb._sess = _FakeDinoSession()
    return emb


def test_dinov2_letterbox_and_fallback(tmp_path):
    wide = np.full((100, 300, 3), 200, np.uint8)
    x = letterbox_preprocess(wide)
    assert x.shape == (3, 224, 224)
    assert np.allclose(x[:, 0, :], 0.0, atol=1e-5)          # padding = ImageNet mean -> 0
    assert not np.allclose(x[:, 112, :], 0.0)                 # the frame is centred, not cropped
    settings = Settings(cache_dir=tmp_path / "cache")
    v = _fake_dino(settings).embed_images([wide, np.zeros((50, 50, 3), np.uint8)])
    assert v.shape == (2, 384) and np.allclose(np.linalg.norm(v, axis=1), 1.0)

    with patch("huggingface_hub.hf_hub_download", side_effect=OSError("offline")):
        offline = Dinov2Embedder(settings)
        assert offline.available() is False
    assert "DINOv2 unavailable" in offline.error
    matrix, reason = selection_embeddings_for_rows([{"id": 1, "t": 0.0}], embedder=offline, cache=None, crop="[]")
    assert matrix is None and "DINOv2 unavailable" in reason


def test_extract_caches_dinov2_and_reselect_uses_it(tmp_path):
    frames = [_texture(800 + i) for i in range(40)]
    video, settings, db = _env(tmp_path, "v_f2_dino", frames)
    db.set_setting("user_settings", {"selection_embedder": "dinov2"})
    jm = _manager(db, settings)
    jm.dino = _fake_dino(settings)
    with patch("pipeline.jobs.decode_exact_frames", side_effect=lambda v, ts, on_frame: [on_frame(t, frames[0]) for t in ts]):
        res = jm._execute_extract_chunk(1, k=5)
    sel = res["stats"]["selection_embedder"]
    assert sel["embedder"] == "dinov2" and sel["computed"] > 0
    feats = db.all("SELECT emb_refs FROM frame_features WHERE video_id='v_f2_dino' AND emb_refs LIKE '%dinov2%'")
    assert len(feats) == sel["computed"]

    from pipeline.reselect import reselect_video

    embed = jm.selection_embed_rows("v_f2_dino", {"selection_embedder": "dinov2"})
    jm.dino.embed_images = MagicMock(side_effect=AssertionError("must come from the cache"))
    report = reselect_video(db, "v_f2_dino", target_count=4, embed_rows=embed)
    assert report["embedder"] == "dinov2" and report["embedder_fallback"] is None and report["picks_count"] == 4

    # Embedder unavailable and nothing cached for a new crop -> CLIP fallback with the reason
    with db.write() as conn:
        conn.execute("UPDATE videos SET letterbox=? WHERE id='v_f2_dino'", (json.dumps({"top": 2, "bottom": 2}),))
    jm.dino = Dinov2Embedder(settings)
    jm.dino.error = "DINOv2 unavailable: offline"
    report = reselect_video(db, "v_f2_dino", target_count=4,
                            embed_rows=jm.selection_embed_rows("v_f2_dino", {"selection_embedder": "dinov2"}))
    assert report["embedder"] == "clip" and "offline" in report["embedder_fallback"]
    assert jm.selection_embed_rows("v_f2_dino", {"selection_embedder": "clip"}) is None
    _shutdown(jm)


# ----------------------------------------------------------------------------- 5. axis caps + cross-dataset dedup
VID = "test_fixture_phase_f2"
VID_OTHER = "test_fixture_phase_f2_other"


def _zs(equip: bool, comp: float) -> dict:
    return {"sharpness": 0.5, "quality": 0.5, "multiple_persons": 0.1, "one_person": 0.1 if equip else 0.8,
            "tools": 0.9 if equip else 0.05, "equipment": 0.1, "face_score": 0.2, "face_count": 0, "composite": comp}


def _cleanup(db: DB) -> None:
    with db.write() as conn:
        for vid in (VID, VID_OTHER):
            conn.execute("DELETE FROM jobs WHERE video_id=?", (vid,))
            conn.execute("DELETE FROM frames WHERE video_id=?", (vid,))
            conn.execute("DELETE FROM chunks WHERE video_id=?", (vid,))
            conn.execute("DELETE FROM videos WHERE id=?", (vid,))


@pytest.fixture
def seeded():
    """One chunk, 20 frames: the first 12 are 'equipment' frames with the best composites, 8 are solo frames.
    A second fixture video has 3 exported frames that duplicate frames 12-14 of the first one."""
    db = api.db
    _cleanup(db)
    rng = np.random.default_rng(5)
    embs = _l2(rng.normal(size=(20, 64))).astype(np.float32)
    ids: list[int] = []
    with db.write() as conn:
        for vid in (VID, VID_OTHER):
            conn.execute("INSERT INTO videos (id, path, name, slug, status, duration) VALUES (?, ?, ?, ?, 'curating_300', 60.0)",
                         (vid, f"/nonexistent/{vid}.mp4", f"{vid}.mp4", vid))
        cid = conn.execute("INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES (?, 0, 0, 60, 5, 100)",
                           (VID,)).lastrowid
        for i in range(20):
            equip = i < 12
            fid = conn.execute(
                "INSERT INTO frames (video_id, chunk_id, t, metrics, embedding, zs_scores, preview_path, full_path) "
                "VALUES (?, ?, ?, '{}', ?, ?, '', '')",
                (VID, cid, i * 2.0, embs[i].tobytes(), json.dumps(_zs(equip, 0.9 - 0.01 * i if equip else 0.5 - 0.01 * i))),
            ).lastrowid
            conn.execute("INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, ?, ?, 'candidate')",
                         (fid, cid, "pick" if i < 10 else "runner_up", i))
            ids.append(fid)
        ocid = conn.execute("INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct) VALUES (?, 0, 0, 60, 5, 100)",
                            (VID_OTHER,)).lastrowid
        for j, src in enumerate((12, 13, 14)):
            fid = conn.execute(
                "INSERT INTO frames (video_id, chunk_id, t, metrics, embedding, zs_scores, preview_path, full_path) "
                "VALUES (?, ?, ?, '{}', ?, '{}', '', '')",
                (VID_OTHER, ocid, float(j), embs[src].tobytes()),
            ).lastrowid
            conn.execute("INSERT INTO exports (frame_id, per_video_path, exported_at) VALUES (?, '/tmp/o.png', 0)", (fid,))
    yield {"db": db, "ids": ids}
    _cleanup(db)


def test_frame_axes_and_cap_normalisation():
    ax = frame_axes({"multiple_persons": 0.6, "one_person": 0.1, "tools": 0.5, "face_visible_50pct": True})
    assert ax == {"multi_person": True, "solo": False, "equipment": True, "face": True}
    assert frame_axes({"face_count": 2})["multi_person"] is True
    assert normalise_axis_caps({"tools_equipment": 0.3, "one_person": 1}) == {"equipment": 0.3, "solo": 1.0}
    with pytest.raises(ReselectError):
        normalise_axis_caps({"pose": 0.2})
    with pytest.raises(ReselectError):
        normalise_axis_caps({"face": 2.0})


def test_reselect_axis_caps_and_cross_dataset_dedup(seeded):
    ids = seeded["ids"]
    with TestClient(api.app) as client:
        # Pure quality without caps: the 10 picks are all equipment frames (highest composites)
        res = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 10, "diversity": 0.0, "axis_caps": {}})
        assert res.status_code == 200, res.text
        rep = res.json()["reselect"]
        assert {p["id"] for p in res.json()["picks"]} == set(ids[:10])
        assert rep["axis_caps"] == {} and rep["cross_dataset"]["enabled"] is False and rep["embedder"] == "clip"

        # Cap equipment at 40 %: 4 equipment frames + 6 solo frames
        res = client.post(f"/api/videos/{VID}/reselect",
                          json={"target_count": 10, "diversity": 0.0, "axis_caps": {"equipment": 0.4}})
        assert res.status_code == 200
        rep = res.json()["reselect"]
        picks = {p["id"] for p in res.json()["picks"]}
        assert len(picks & set(ids[:12])) == 4 and len(picks) == 10
        assert rep["axis_caps"]["equipment"] == {"max_share": 0.4, "cap": 4, "picks": 4, "pool": 12, "overflow": 0}

        # Not enough uncapped frames: the cap is exceeded only as far as needed (overflow reported)
        res = client.post(f"/api/videos/{VID}/reselect",
                          json={"target_count": 14, "diversity": 0.0, "axis_caps": {"equipment": 0.2}})
        eq = res.json()["reselect"]["axis_caps"]["equipment"]
        assert eq["cap"] == 2 and eq["picks"] == 6 and eq["overflow"] == 4

        # Cross-dataset: frames 12-14 duplicate exports of another video and are never picked
        res = client.post(f"/api/videos/{VID}/reselect", json={
            "target_count": 10, "diversity": 0.0, "axis_caps": {"equipment": 0.4}, "cross_dataset_dedup": True})
        assert res.status_code == 200
        rep = res.json()["reselect"]
        picks = {p["id"] for p in res.json()["picks"]}
        assert rep["cross_dataset"]["reference_frames"] >= 3 and rep["cross_dataset"]["blocked"] == 3
        assert set(rep["cross_dataset"]["blocked_ids"]) == set(ids[12:15])
        assert not picks & set(ids[12:15])
        # 4 equipment + the 5 remaining solo frames, then one equipment frame over the cap to reach the target
        assert len(picks) == 10 and rep["shortfall"] == 0
        assert rep["axis_caps"]["equipment"]["picks"] == 5 and rep["axis_caps"]["equipment"]["overflow"] == 1

        assert client.post(f"/api/videos/{VID}/reselect", json={"axis_caps": {"pose": 0.5}}).status_code == 400
        assert client.post(f"/api/videos/{VID}/reselect", json={"axis_caps": {"face": 3}}).status_code == 400


def test_reselect_axis_caps_default_from_settings(seeded):
    db, ids = seeded["db"], seeded["ids"]
    stored = db.get_setting("user_settings", None)
    try:
        db.set_setting("user_settings", {**(stored or {}), "reselect_axis_caps": {"equipment": 0.3},
                                         "reselect_cross_dataset_dedup": True})
        with TestClient(api.app) as client:
            rep = client.post(f"/api/videos/{VID}/reselect", json={"target_count": 10, "diversity": 0.0}).json()["reselect"]
            assert rep["axis_caps"]["equipment"]["cap"] == 3 and rep["cross_dataset"]["enabled"] is True
            # The request overrides the stored defaults
            rep = client.post(f"/api/videos/{VID}/reselect", json={
                "target_count": 10, "diversity": 0.0, "axis_caps": {}, "cross_dataset_dedup": False}).json()["reselect"]
            assert rep["axis_caps"] == {} and rep["cross_dataset"]["enabled"] is False
    finally:
        if stored is None:
            with db.write() as conn:
                conn.execute("DELETE FROM settings WHERE key='user_settings'")
        else:
            db.set_setting("user_settings", stored)


def test_safe_decode_frames_drains_the_decoder(tmp_path):
    """The flush packet (no pts/dts) must be decoded, or the buffered last frames of a video are lost."""
    import av
    import numpy as np

    from pipeline.decode import safe_decode_frames

    path = tmp_path / "flush.mp4"
    out = av.open(str(path), "w")
    st = out.add_stream("libx264", rate=10)
    st.width, st.height, st.pix_fmt = 64, 48, "yuv420p"
    for i in range(30):
        frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 8, np.uint8), format="rgb24")
        for pkt in st.encode(frame):
            out.mux(pkt)
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    with av.open(str(path)) as c:
        assert sum(1 for _ in safe_decode_frames(c, c.streams.video[0])) == 30
