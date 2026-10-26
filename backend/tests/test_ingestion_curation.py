"""Unit and integration tests for Module A (Ingestion, Quality Math, BK-Tree Deduplication)
and Module B (Rule-Based Heuristic Tagger & Dataset Stratification)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.models.domain import ExtractionConfig, QualityWeights
from backend.app.modules.curation.rule_tagger import RuleBasedHeuristicPlugin
from backend.app.modules.curation.stratifier import RuleBasedStratifier
from backend.app.modules.ingestion.deduplicator import (
    BKTree,
    MultiIndexPrefixBuckets,
    cluster_media_items,
    cluster_naive_pairwise,
    compute_dhash,
    compute_fingerprint,
    compute_hsv_histogram,
    compute_phash,
    hamming_distance_hex,
    hsv_cosine_similarity,
)
from backend.app.modules.ingestion.extractor import (
    extract_video_pipeline,
    ingest_image_folder,
    pick_window_peak,
    probe_video_metadata,
)
from backend.app.modules.ingestion.quality_math import (
    border_black_fraction,
    compute_quality_score,
    compute_quality_scores_batch,
)
from backend.benchmarks.benchmark_vision import (
    _generate_synthetic_hash_dataset,
    make_synthetic_archetypes,
)


def test_quality_math_calibration_and_hard_gates() -> None:
    """Verify Q in [0, 1] ordering and all 6 deterministic hard gates on synthetic archetypes."""
    archetypes = make_synthetic_archetypes(512, 512)

    sharp_res = compute_quality_score(archetypes["sharp_studio"])
    blur_res = compute_quality_score(archetypes["motion_blurred"])
    chiaro_res = compute_quality_score(archetypes["chiaroscuro_key"])
    dark_res = compute_quality_score(archetypes["dark_void"])
    blown_res = compute_quality_score(archetypes["blown_out"])
    flat_res = compute_quality_score(archetypes["flat_low_entropy"])
    letterbox_res = compute_quality_score(archetypes["letterbox_card"])

    # 1. Sharp studio image passes all gates with high quality score
    assert sharp_res.passed_gates is True
    assert sharp_res.curation_state == "candidate"
    assert sharp_res.quality_score > 0.70
    assert sharp_res.sharpness_laplacian > 100.0
    assert sharp_res.sharpness_tenengrad > 20.0

    # 2. Chiaroscuro studio image passes gates and preserves high exposure score via low-key target (95)
    assert chiaro_res.passed_gates is True
    assert chiaro_res.exposure_score > 0.70
    assert chiaro_res.quality_score > 0.60

    # 3. Motion blurred frame triggers `motion_blur` gate and scores much lower than sharp_studio
    assert blur_res.passed_gates is False
    assert "motion_blur" in blur_res.reject_reasons
    assert blur_res.curation_state == "auto_rejected"
    assert blur_res.quality_score < sharp_res.quality_score

    # 4. Dark void frame triggers `too_dark`
    assert dark_res.passed_gates is False
    assert "too_dark" in dark_res.reject_reasons
    assert dark_res.curation_state == "auto_rejected"

    # 5. Blown-out white frame triggers `blown_out` and `severe_clipping`
    assert blown_res.passed_gates is False
    assert "blown_out" in blown_res.reject_reasons
    assert "severe_clipping" in blown_res.reject_reasons

    # 6. Flat transition frame triggers `low_entropy` and `motion_blur`
    assert flat_res.passed_gates is False
    assert "low_entropy" in flat_res.reject_reasons
    assert "motion_blur" in flat_res.reject_reasons
    assert flat_res.entropy_shannon < 0.1

    # 7. Letterboxed card triggers `letterbox_card`
    assert letterbox_res.passed_gates is False
    assert "letterbox_card" in letterbox_res.reject_reasons
    assert border_black_fraction(cv2.cvtColor(archetypes["letterbox_card"], cv2.COLOR_BGR2GRAY)) > 0.45


def test_batch_relative_blur_gate() -> None:
    """Verify batch scoring with relative chunk blur floor flags soft frames in an ultra-sharp chunk."""
    archetypes = make_synthetic_archetypes(512, 512)
    sharp = archetypes["sharp_studio"]
    # Mildly softened image that passes absolute floor (15.0) but is < 25% of sharp median
    mild_blur = cv2.GaussianBlur(sharp, (9, 9), 2.2)

    batch_res = compute_quality_scores_batch(
        [sharp, sharp, sharp, sharp, mild_blur],
        weights=QualityWeights(),
        apply_relative_blur_gate=True,
    )
    assert batch_res[0].passed_gates is True
    assert "motion_blur" in batch_res[-1].reject_reasons


def test_phash_dhash_hsv_fingerprints() -> None:
    """Verify 64-bit pHash, dHash, and 24-bin HSV histogram invariance to minor noise and sensitivity to edits."""
    archetypes = make_synthetic_archetypes(512, 512)
    img1 = archetypes["sharp_studio"]
    # Slight compression/brightness jitter should keep pHash/dHash very close
    img1_jitter = np.clip(img1.astype(np.int16) + 3, 0, 255).astype(np.uint8)
    img2 = cv2.flip(img1, 1)  # Horizontal flip changes spatial structure

    fp1 = compute_fingerprint(img1)
    fp1_j = compute_fingerprint(img1_jitter)
    fp2 = compute_fingerprint(img2)

    assert len(fp1.phash_hex) == 16
    assert len(fp1.dhash_hex) == 16
    assert len(fp1.hsv_hist) == 96  # 24 * float32

    assert hamming_distance_hex(fp1.phash_hex, fp1_j.phash_hex) <= 2
    assert hamming_distance_hex(fp1.dhash_hex, fp1_j.dhash_hex) <= 2
    assert hsv_cosine_similarity(fp1.hsv_hist, fp1_j.hsv_hist) >= 0.98

    # Flipped image has distinct hash
    assert hamming_distance_hex(fp1.phash_hex, fp2.phash_hex) >= 8


def test_bktree_and_multi_index_exact_recall() -> None:
    """Verify BKTree and MultiIndexPrefixBuckets achieve 100% recall against brute-force Hamming search."""
    rng = np.random.default_rng(99)
    hashes = [int(rng.integers(0, 1 << 63, dtype=np.uint64)) for _ in range(250)]
    # Inject known near-duplicates (distance 0..6)
    for i in range(50):
        base = hashes[i]
        hashes.append(base ^ (1 << (i % 64)) ^ (1 << ((i * 7) % 64)))

    bktree = BKTree()
    mih = MultiIndexPrefixBuckets()
    for idx, h in enumerate(hashes):
        bktree.add(h, idx)
        mih.add(h, idx)

    for q_idx in range(0, len(hashes), 15):
        q = hashes[q_idx]
        for radius in (0, 3, 6):
            expected = {i for i, h in enumerate(hashes) if (q ^ h).bit_count() <= radius}
            bk_found = {i for i, _ in bktree.search(q, radius)}
            assert bk_found == expected

            mih_cands = mih.query_candidates(q, max_band_errors=(0 if radius <= 3 else 1))
            mih_found = {i for i in mih_cands if (q ^ hashes[i]).bit_count() <= radius}
            assert mih_found == expected


def test_clustering_best_representative_and_equivalence() -> None:
    """Verify O(N log N) clustering matches naive O(N^2) and selects argmax Q in every cluster."""
    items, expected_best_ids = _generate_synthetic_hash_dataset(300, avg_cluster_size=5, seed=777)

    clusters_fast, assign_fast, stats_fast = cluster_media_items(
        items, phash_threshold=6, dhash_threshold=8, hsv_min_cosine=0.85, index_strategy="auto"
    )
    clusters_bk, assign_bk, _ = cluster_media_items(
        items, phash_threshold=6, dhash_threshold=8, hsv_min_cosine=0.85, index_strategy="bktree"
    )
    clusters_naive, assign_naive, stats_naive = cluster_naive_pairwise(
        items, phash_threshold=6, dhash_threshold=8, hsv_min_cosine=0.85
    )

    assert len(clusters_fast) == 60
    assert len(clusters_bk) == 60
    assert len(clusters_naive) == 60

    rep_fast = {c.representative_item_id for c in clusters_fast}
    rep_bk = {c.representative_item_id for c in clusters_bk}
    rep_naive = {c.representative_item_id for c in clusters_naive}
    assert rep_fast == expected_best_ids
    assert rep_bk == expected_best_ids
    assert rep_naive == expected_best_ids

    # Check curation states: exactly 60 'keep' representatives and 240 'duplicate_hidden'
    kept = [a for a in assign_fast if a.is_cluster_representative and a.curation_state == "keep"]
    dups = [a for a in assign_fast if not a.is_cluster_representative and a.curation_state == "duplicate_hidden"]
    assert len(kept) == 60
    assert len(dups) == 240
    assert stats_fast["distance_computations"] < stats_naive["distance_computations"]


def test_ingest_image_folder_and_video_pipeline(tmp_path: Path) -> None:
    """Verify Path 1 (Image Folder Ingestion) and Path 2 (Video Extraction Pipeline)."""
    archetypes = make_synthetic_archetypes(320, 240)
    img_dir = tmp_path / "input_images"
    thumb_dir = tmp_path / "thumbs"
    img_dir.mkdir(parents=True, exist_ok=True)

    # Save 2 near-duplicate sharp images (one slightly sharper, with sidecar .txt) + 1 dark reject
    cv2.imwrite(str(img_dir / "001_sharp.png"), archetypes["sharp_studio"])
    (img_dir / "001_sharp.txt").write_text("ohwx_woman standing in studio", encoding="utf-8")

    slightly_noisy = np.clip(archetypes["sharp_studio"].astype(np.int16) + 2, 0, 255).astype(np.uint8)
    cv2.imwrite(str(img_dir / "002_dup.jpg"), slightly_noisy)
    cv2.imwrite(str(img_dir / "003_dark.png"), archetypes["dark_void"])

    items, clusters, stats = ingest_image_folder(
        img_dir,
        thumbnails_dir=thumb_dir,
        dedup_hamming_threshold=6,
    )
    assert len(items) == 3
    assert stats["auto_rejected"] == 1
    assert len(clusters) == 2  # {001, 002} grouped into 1 cluster, {003} in another
    assert items[0].existing_caption == "ohwx_woman standing in studio"
    assert items[0].thumbnail_path is not None and Path(items[0].thumbnail_path).is_file()

    # Path 2: Create a synthetic 2-second video with 2 distinct scenes using PyAV or OpenCV VideoWriter
    video_file = tmp_path / "synthetic_clip.avi"
    try:
        import av

        container = av.open(str(video_file), mode="w")
        stream = container.add_stream("mpeg4", rate=15)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"

        for f_idx in range(30):
            src_img = archetypes["sharp_studio"] if f_idx < 15 else archetypes["chiaroscuro_key"]
            frame = av.VideoFrame.from_ndarray(src_img, format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        container.close()
    except ImportError:
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        writer = cv2.VideoWriter(str(video_file), fourcc, 15.0, (320, 240))
        for f_idx in range(30):
            src_img = archetypes["sharp_studio"] if f_idx < 15 else archetypes["chiaroscuro_key"]
            writer.write(src_img)
        writer.release()

    meta = probe_video_metadata(video_file)
    assert meta.width == 320 and meta.height == 240

    v_items, v_clusters, _, v_stats = extract_video_pipeline(
        video_file,
        tmp_path / "extracted_frames",
        thumbnails_dir=tmp_path / "video_thumbs",
        config=ExtractionConfig(mode="interval", interval_s=0.4, window_peak=2, prefer_pyav=True),
    )
    assert len(v_items) >= 2
    assert len(v_clusters) >= 1
    assert v_stats["candidates_extracted"] == len(v_items)

    # Verify pick_window_peak helper
    win_best = pick_window_peak(
        [
            {"t": 0.1, "sharp": 120.0, "motion": 12.0},
            {"t": 0.2, "sharp": 118.0, "motion": 0.5},
        ],
        motion_weight=0.5,
    )
    assert win_best["t"] == 0.2


def test_rule_tagger_and_stratifier() -> None:
    """Verify RuleBasedHeuristicPlugin and RuleBasedStratifier on diverse synthetic items."""
    archetypes = make_synthetic_archetypes(512, 512)
    tagger = RuleBasedHeuristicPlugin()
    assert tagger.plugin_id == "rule_cv_heuristics"
    assert tagger.requires_ml_runtime is False
    assert tagger.is_available() is True

    tags_sharp = tagger.analyze_item(
        archetypes["sharp_studio"], {"width": 512, "height": 768, "aspect_ratio": 512 / 768}
    )
    slugs_sharp = {t.slug for t in tags_sharp}
    assert "portrait_tall" in slugs_sharp

    tags_chiaro = tagger.analyze_item(
        archetypes["chiaroscuro_key"], {"width": 768, "height": 512, "aspect_ratio": 768 / 512}
    )
    slugs_chiaro = {t.slug for t in tags_chiaro}
    assert "landscape_wide" in slugs_chiaro
    assert "chiaroscuro_high_contrast" in slugs_chiaro
    assert "close_focus_bokeh" in slugs_chiaro

    # Test RuleBasedStratifier distribution, outlier detection, and MMR subset selection
    stratifier = RuleBasedStratifier()
    synthetic_items, _ = _generate_synthetic_hash_dataset(60, avg_cluster_size=1, seed=101)
    tags_by_item = {}
    for idx, it in enumerate(synthetic_items):
        it["aspect_ratio"] = 0.7 if idx % 3 == 0 else (1.5 if idx % 3 == 1 else 1.0)
        it["exposure_mean_luma"] = 60.0 if idx % 4 == 0 else (195.0 if idx % 4 == 1 else 128.0)
        it["contrast_rms"] = 50.0
        it["timestamp_s"] = float(idx) * 1.5
        tags_by_item[it["id"]] = tags_sharp if idx % 2 == 0 else tags_chiaro

    # Inject one low-quality outlier
    synthetic_items[-1]["quality_score"] = 0.05
    synthetic_items[-1]["curation_state"] = "auto_rejected"

    dist = stratifier.compute_distribution(synthetic_items, tags_by_item)
    assert dist["total_items"] == 60
    assert sum(dist["aspect_ratio_buckets"].values()) == 60
    assert synthetic_items[-1]["id"] in dist["outlier_ids"]

    gaps = stratifier.detect_outliers_and_gaps(dist, target_size=25)
    assert synthetic_items[-1]["id"] in gaps["outlier_ids"]

    selection = stratifier.stratify_selection(
        synthetic_items,
        tags_by_item=tags_by_item,
        target_count=25,
        diversity_weight=0.6,
    )
    assert len(selection["recommended_keep_ids"]) == 25
    assert len(selection["recommended_demote_ids"]) == 35
    assert synthetic_items[-1]["id"] in selection["recommended_demote_ids"]
