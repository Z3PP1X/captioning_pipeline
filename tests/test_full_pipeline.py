"""Full pipeline test suite: Tier 1 metrics, MMR diverse selection, snap-to-16, and dataset export."""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

from pipeline.config import Settings
from pipeline.db import DB
from pipeline.export import (
    BUCKETS_1MP_4,
    Exporter,
    clamp_max_edge,
    render_export_image,
    snap_to_multiple_of_16,
)
from pipeline.metrics import (
    compute_tier1_metrics,
    hard_filter_check,
    phash_hamming_distance,
)
from pipeline.select import select_diverse_top_k


def test_tier1_metrics_and_hard_filter():
    # 1. Test black frame
    black_img = np.zeros((360, 640, 3), dtype=np.uint8)
    m_black = compute_tier1_metrics(black_img)
    ok, reason = hard_filter_check(m_black)
    assert not ok
    assert "Black" in reason

    # 2. Test sharp textured image with normal exposure
    sharp_img = np.full((360, 640, 3), 128, dtype=np.uint8)
    # Draw high-contrast stripes
    sharp_img[::10, :] = 220
    sharp_img[:, ::10] = 30
    m_sharp = compute_tier1_metrics(sharp_img)
    ok, reason = hard_filter_check(m_sharp)
    assert ok, f"Expected pass but got: {reason}"
    assert m_sharp["tile_sharp"] > 50.0
    assert m_sharp["contrast"] > 20.0
    assert len(m_sharp["phash"]) == 16


def test_phash_hamming_distance():
    h1 = "ffff0000ffff0000"
    h2 = "ffff0000ffff0001"
    assert phash_hamming_distance(h1, h2) == 1
    assert phash_hamming_distance(h1, h1) == 0


def test_snap_to_multiple_of_16():
    # Odd dimensions 1923 x 1087
    img = np.zeros((1087, 1923, 3), dtype=np.uint8)
    snapped = snap_to_multiple_of_16(img)
    h, w = snapped.shape[:2]
    assert w % 16 == 0
    assert h % 16 == 0
    assert w == 1920
    assert h == 1072


def test_clamp_max_edge():
    img = np.zeros((3000, 4000, 3), dtype=np.uint8)
    clamped = clamp_max_edge(img, max_edge=2048)
    assert max(clamped.shape[:2]) == 2048
    # Aspect ratio preserved (4:3)
    assert clamped.shape == (1536, 2048, 3)


def test_mmr_selection():
    # Create 5 synthetic candidates
    rng = np.random.default_rng(42)
    candidates = []
    for i in range(10):
        # Synthetic unit embedding
        emb = rng.standard_normal(512).astype(np.float32)
        emb /= np.linalg.norm(emb)
        candidates.append({
            "t": float(i * 2.0),
            "tier1_score": 0.5 + 0.05 * i,
            "embedding": emb,
            "zs_scores": {"person": 0.85, "equipment": 0.40, "negative": 0.05},
        })

    picks, runner_ups, unselected = select_diverse_top_k(
        candidates,
        k=3,
        runner_ups=2,
        rule="person",
    )

    assert len(picks) == 3
    assert len(runner_ups) == 2
    assert picks[0]["role"] == "pick"
    assert picks[0]["state"] == "keep"
    assert runner_ups[0]["role"] == "runner_up"


def test_export_pipeline():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db = DB(tmp_path / "test.db")
        settings = Settings(
            video_dir=tmp_path / "videos",
            cache_dir=tmp_path / "cache",
            finished_dir=tmp_path / "finished",
        )
        settings.ensure_dirs()
        exporter = Exporter(db, settings)

        # 1. Create dummy video in DB
        with db.write() as conn:
            conn.execute(
                """
                INSERT INTO videos (id, path, name, slug, status, duration)
                VALUES ('vid1', '/tmp/fake.mp4', 'Test Video.mp4', 'test_video', 'curating', 120.0)
                """
            )
            conn.execute(
                """
                INSERT INTO chunks (id, video_id, position, start_s, end_s, k, rule, tags)
                VALUES (1, 'vid1', 0, 10.0, 20.0, 2, 'person', '["bondage", "studio"]')
                """
            )

        # 2. Create fake full-res image
        fake_img_path = tmp_path / "cache" / "frame_1.png"
        fake_img_path.parent.mkdir(parents=True, exist_ok=True)
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        img[50:100, 50:100] = 255
        cv2.imwrite(str(fake_img_path), img)

        with db.write() as conn:
            conn.execute(
                """
                INSERT INTO frames (id, video_id, chunk_id, t, full_path, metrics, zs_scores)
                VALUES (1, 'vid1', 1, 12.5, ?, '{"mean_luma": 100}', '{"best_label": "rope"}')
                """,
                (str(fake_img_path),),
            )
            conn.execute(
                """
                INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                VALUES (1, 1, 'pick', 0, 'keep')
                """
            )

        # 3. Run export
        res = exporter.export_video("vid1", custom_trigger="restr_elg")
        assert res["exported"] == 1

        # 4. Verify outputs on disk
        per_video_dir = Path(res["per_video_dir"])
        merged_dir = Path(res["merged_dir"])

        pngs_pv = list(per_video_dir.glob("*.png"))
        txts_pv = list(per_video_dir.glob("*.txt"))
        jsons_pv = list(per_video_dir.glob("*.json"))

        assert len(pngs_pv) == 1
        assert len(txts_pv) == 1
        assert len(jsons_pv) == 1

        # Check caption stub
        caption_content = txts_pv[0].read_text()
        assert "restr_elg" in caption_content
        assert "bondage" in caption_content

        # Check merged output exists
        pngs_merged = list(merged_dir.glob("*.png"))
        assert len(pngs_merged) == 1
        assert (merged_dir / "manifest.jsonl").exists()
        exported_bgr = cv2.imread(str(pngs_pv[0]))
        assert exported_bgr is not None
        assert (exported_bgr.shape[1], exported_bgr.shape[0]) == (1344, 768)


def test_export_normalizes_5px_crop_variations_to_max_4_1mp_buckets():
    """Verify crops differing by ~5px normalize to at most 4 1MP portrait/landscape buckets via center crop."""
    raw_landscape = np.zeros((1080, 1920, 3), dtype=np.uint8)
    raw_portrait = np.zeros((1920, 1080, 3), dtype=np.uint8)

    # 8 slightly different crops (differing by ~5px in pixel space) across 4 orientations
    crops_and_sources = [
        (raw_landscape, {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}),
        (raw_landscape, {"x": 0.002, "y": 0.004, "width": 0.995, "height": 0.992}),
        (raw_landscape, {"x": 0.12, "y": 0.0, "width": 0.75, "height": 1.0}),
        (raw_landscape, {"x": 0.123, "y": 0.003, "width": 0.745, "height": 0.994}),
        (raw_portrait, {"x": 0.0, "y": 0.12, "width": 1.0, "height": 0.75}),
        (raw_portrait, {"x": 0.003, "y": 0.123, "width": 0.994, "height": 0.745}),
        (raw_portrait, {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}),
        (raw_portrait, {"x": 0.004, "y": 0.002, "width": 0.992, "height": 0.995}),
    ]

    output_buckets: set[tuple[int, int]] = set()
    for src_img, crop_rect in crops_and_sources:
        rendered = render_export_image(src_img, crop=crop_rect)
        assert rendered is not None
        h, w = rendered.shape[:2]
        output_buckets.add((w, h))

    assert len(output_buckets) == 4
    assert output_buckets == set(BUCKETS_1MP_4)


def test_concurrent_job_manager():
    """Verify JobManager handles multi-video job submissions and isolated cancellation."""
    from pipeline.jobs import JobManager

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db = DB(tmp_path / "jobs_test.db")
        settings = Settings(
            video_dir=tmp_path / "videos",
            cache_dir=tmp_path / "cache",
            finished_dir=tmp_path / "finished",
        )
        settings.ensure_dirs()

        with db.write() as conn:
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v1', '/tmp/1.mp4', 'Vid 1.mp4', 'vid1', 'new', 60.0)"
            )
            conn.execute(
                "INSERT INTO videos (id, path, name, slug, status, duration) VALUES ('v2', '/tmp/2.mp4', 'Vid 2.mp4', 'vid2', 'new', 90.0)"
            )

        jm = JobManager(db, settings)
        try:
            assert jm.caption_executor._max_workers == 4
            assert jm.decode_executor._max_workers in (1, 2)
            # Hold submitted jobs in 'queued' so cancellation is deterministic (no worker race)
            held: list[tuple] = []
            real_submit = jm.executor.submit
            jm.executor.submit = lambda fn, *args: held.append((fn, args))

            # Submit jobs for two separate videos
            j1 = jm.submit_extract_job("v1")
            j2 = jm.submit_extract_job("v2")

            assert j1 > 0
            assert j2 > j1

            # Verify DB status
            row1 = db.one("SELECT status, video_id FROM jobs WHERE id=?", (j1,))
            row2 = db.one("SELECT status, video_id FROM jobs WHERE id=?", (j2,))
            assert row1["video_id"] == "v1"
            assert row2["video_id"] == "v2"

            # Cancel job 1 only: a queued job ends immediately and its video leaves 'extracting'
            ok = jm.cancel_job(j1)
            assert ok
            assert j1 in jm._cancel_flags
            assert j2 not in jm._cancel_flags

            row1_after = db.one("SELECT status FROM jobs WHERE id=?", (j1,))
            assert row1_after["status"] == "cancelled"
            assert db.one("SELECT status FROM videos WHERE id='v1'")["status"] == "new"

            # The worker must not resurrect a job cancelled while queued (review C4c)
            for fn, args in held:
                fn(*args)
            assert db.one("SELECT status FROM jobs WHERE id=?", (j1,))["status"] == "cancelled"
            assert db.one("SELECT status FROM jobs WHERE id=?", (j2,))["status"] == "finished"
            assert not jm._cancel_flags

            # Finished jobs cannot be cancelled (review C4d)
            assert jm.cancel_job(j2) is False
            assert db.one("SELECT status FROM jobs WHERE id=?", (j2,))["status"] == "finished"
            jm.executor.submit = real_submit
        finally:
            jm.executor.shutdown(wait=False)
