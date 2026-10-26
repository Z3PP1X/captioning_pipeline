"""Tests for MOSS-Image deterministic geometry engine (letterbox & cropper)."""
from __future__ import annotations

import numpy as np

from pipeline.geometry.cropper import (
    BUCKETS_1MP_3,
    BUCKETS_1MP_4,
    MAX_DATASET_BUCKETS,
    MIN_DATASET_BUCKETS,
    MOSS_BUCKETS,
    _parse_padded_watermarks,
    apply_moss_crop,
    center_crop_box,
    center_crop_to_bucket,
    compute_optimal_moss_crop,
    resolve_buckets,
    select_nearest_bucket,
)
from pipeline.geometry.letterbox import crop_letterbox_numpy


def test_letterbox_no_borders() -> None:
    img = np.full((200, 300, 3), 128, dtype=np.uint8)
    cropped, bounds = crop_letterbox_numpy(img)
    assert bounds == {"top": 0, "bottom": 0, "left": 0, "right": 0}
    assert cropped.shape == (200, 300, 3)


def test_letterbox_solid_black() -> None:
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    cropped, bounds = crop_letterbox_numpy(img)
    assert bounds == {"top": 0, "bottom": 0, "left": 0, "right": 0}
    assert cropped.shape == (100, 100, 3)


def test_letterbox_top_bottom_borders() -> None:
    img = np.full((300, 400, 3), 150, dtype=np.uint8)
    img[:30, :] = 5  # Top border
    img[280:, :] = 8  # Bottom border (20 px)

    cropped, bounds = crop_letterbox_numpy(img)
    assert bounds["top"] == 30
    assert bounds["bottom"] == 20
    assert bounds["left"] == 0
    assert bounds["right"] == 0
    assert cropped.shape == (250, 400, 3)


def test_letterbox_left_right_borders_2d() -> None:
    img = np.full((200, 300), 200, dtype=np.uint8)
    img[:, :25] = 2  # Left border
    img[:, 260:] = 0  # Right border (40 px)

    cropped, bounds = crop_letterbox_numpy(img)
    assert bounds["left"] == 25
    assert bounds["right"] == 40
    assert bounds["top"] == 0
    assert bounds["bottom"] == 0
    assert cropped.shape == (200, 235)


def test_watermark_coordinate_conversion_and_padding() -> None:
    img_w, img_h = 1000, 2000
    # Watermark near bottom right: [ymin, xmin, ymax, xmax] in [0..1000]
    watermarks = [{"box_2d": [900, 800, 950, 900], "label": "watermark"}]
    boxes = _parse_padded_watermarks(watermarks, img_w, img_h)

    assert len(boxes) == 1
    ymin, xmin, ymax, xmax = boxes[0]

    # Raw coords:
    # ymin_px = 0.9 * 2000 = 1800, ymax_px = 0.95 * 2000 = 1900
    # xmin_px = 0.8 * 1000 = 800,  xmax_px = 0.9 * 1000 = 900
    # pad_x = 0.02 * 1000 = 20, pad_y = 0.02 * 2000 = 40
    assert ymin == 1800 - 40
    assert ymax == 1900 + 40
    assert xmin == 800 - 20
    assert xmax == 900 + 20


def test_watermark_avoidance_candidate_selection() -> None:
    img_w, img_h = 1000, 1000
    # Watermark in bottom strip: y in [900..980], x in [800..950]
    watermarks = [{"box_2d": [900, 800, 980, 950]}]
    res = compute_optimal_moss_crop(img_w, img_h, watermarks, borders=[])

    x, y, w, h = res["crop_box"]
    assert w % 64 == 0
    assert h % 64 == 0

    # Ensure crop box does not intersect watermark
    # Padded watermark ymin is (900 - 20) = 880
    assert y + h <= 880 or x + w <= 780
    assert res["area_retained_pct"] > 50.0


def test_bucket_selection_and_64_divisibility() -> None:
    test_cases = [
        (1920, 1080),  # 16:9 widescreen -> should pick wide bucket (e.g. 1344, 768)
        (1080, 1920),  # 9:16 portrait -> should pick tall bucket (e.g. 768, 1344)
        (1000, 1000),  # 1:1 square -> should pick (1024, 1024)
    ]
    for w, h in test_cases:
        res = compute_optimal_moss_crop(w, h, watermarks=[], borders=[])
        bw, bh = res["target_bucket"]
        assert (bw, bh) in MOSS_BUCKETS
        crop_x, crop_y, crop_w, crop_h = res["crop_box"]
        assert crop_w % 64 == 0
        assert crop_h % 64 == 0
        assert crop_x >= 0 and crop_x + crop_w <= w
        assert crop_y >= 0 and crop_y + crop_h <= h


def test_apply_moss_crop() -> None:
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    crop_res = compute_optimal_moss_crop(1920, 1080, watermarks=[], borders=[])
    cropped = apply_moss_crop(img, crop_res["crop_box"], crop_res["target_bucket"])

    target_w, target_h = crop_res["target_bucket"]
    assert cropped.shape == (target_h, target_w, 3)


def test_1mp_bucket_constraints_and_jitter_collapse() -> None:
    """Verify max 4 buckets at ~1MP (portrait & landscape) and that 5px crop jitter collapses into <=4 buckets."""
    assert MIN_DATASET_BUCKETS <= len(MOSS_BUCKETS) <= MAX_DATASET_BUCKETS
    assert len(BUCKETS_1MP_4) == 4
    assert len(BUCKETS_1MP_3) == 3

    # Verify both landscape (Querformat) and portrait (Hochformat) 1MP buckets exist
    has_landscape = any(bw > bh for bw, bh in BUCKETS_1MP_4)
    has_portrait = any(bh > bw for bw, bh in BUCKETS_1MP_4)
    assert has_landscape and has_portrait
    for bw, bh in (*BUCKETS_1MP_4, *BUCKETS_1MP_3):
        area = bw * bh
        assert 950_000 <= area <= 1_100_000, f"Bucket {bw}x{bh} area {area} not ~1MP"
        assert bw % 64 == 0 and bh % 64 == 0

    # Simulate 24 images whose manual crops differ by 5px across widescreen, 4:3, 3:4, and 9:16
    jittered_sizes = [
        (1920, 1080), (1915, 1075), (1910, 1085), (1905, 1070), (1925, 1082), (1895, 1065),
        (1200, 900), (1195, 905), (1205, 895), (1190, 890), (1210, 910), (1185, 898),
        (900, 1200), (905, 1195), (895, 1205), (890, 1190), (910, 1210), (898, 1185),
        (608, 1080), (603, 1075), (613, 1085), (598, 1070), (618, 1082), (600, 1065),
    ]
    observed_buckets: set[tuple[int, int]] = set()
    for w, h in jittered_sizes:
        raw = np.zeros((h, w, 3), dtype=np.uint8)
        # Place bright marker in exact center to verify center crop preserves center subject
        raw[h // 2 - 4 : h // 2 + 4, w // 2 - 4 : w // 2 + 4] = 255
        norm, bucket = center_crop_to_bucket(raw)
        bw, bh = bucket
        assert norm.shape == (bh, bw, 3)
        assert norm[bh // 2, bw // 2, 0] > 200
        observed_buckets.add(bucket)

    assert len(observed_buckets) == 4
    assert observed_buckets == set(BUCKETS_1MP_4)

    # Verify resolve_buckets caps custom sequences to at most 4 buckets
    oversized = [(1344, 768), (1152, 896), (1024, 1024), (896, 1152), (768, 1344), (1536, 640)]
    assert len(resolve_buckets(oversized)) == MAX_DATASET_BUCKETS
    assert len(resolve_buckets("3_buckets")) == 3
    assert select_nearest_bucket(1000, 1000, "3_buckets") == (1024, 1024)
    x0, y0, cw, ch = center_crop_box(1920, 1080, 1344, 768)
    assert ch == 1080 and cw == 1890 and x0 == 15 and y0 == 0
