"""Watermark-avoiding smart-bucket cropper and 1MP center-crop bucket normalizer."""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

MIN_DATASET_BUCKETS: int = 3
MAX_DATASET_BUCKETS: int = 4
BUCKET_DIM_LEN: int = 2
BOX_COORD_LEN: int = 4
BUCKET_STEP: int = 64

# Canonical 4-bucket 1MP training set (~1,032,192 px = 4,032 latent tokens per bucket):
# 2 Landscape (Querformat: 16:9 and 4:3) + 2 Portrait (Hochformat: 3:4 and 9:16).
BUCKETS_1MP_4: tuple[tuple[int, int], ...] = (
    (1344, 768),
    (1152, 896),
    (896, 1152),
    (768, 1344),
)

# Canonical 3-bucket 1MP training set (~1.03-1.05 MP):
# Landscape (Querformat 16:9), Square (1:1), Portrait (Hochformat 9:16).
BUCKETS_1MP_3: tuple[tuple[int, int], ...] = (
    (1344, 768),
    (1024, 1024),
    (768, 1344),
)

DEFAULT_1MP_BUCKETS: tuple[tuple[int, int], ...] = BUCKETS_1MP_4

MOSS_BUCKETS: list[tuple[int, int]] = list(BUCKETS_1MP_4)


BUCKET_PRESET_MAP: dict[str, tuple[tuple[int, int], ...]] = {
    "4_buckets": BUCKETS_1MP_4,
    "3_buckets": BUCKETS_1MP_3,
}


def _is_valid_bucket_pair(bucket: Any) -> bool:
    return bool(
        isinstance(bucket, (tuple, list))
        and len(bucket) == BUCKET_DIM_LEN
        and int(bucket[0]) > 0
        and int(bucket[1]) > 0
    )


def resolve_buckets(
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[tuple[int, int], ...]:
    """Resolve bucket preset or custom bucket sequence, enforcing at most 4 buckets."""
    if buckets is None or isinstance(buckets, str):
        return BUCKET_PRESET_MAP.get(str(buckets), BUCKETS_1MP_4)
    cleaned = [(int(b[0]), int(b[1])) for b in buckets if _is_valid_bucket_pair(b)]
    return tuple(cleaned[:MAX_DATASET_BUCKETS]) if cleaned else BUCKETS_1MP_4


def select_nearest_bucket(
    cand_w: float,
    cand_h: float,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[int, int]:
    """Select the 1MP bucket that maximizes retained center-crop area (closest aspect ratio)."""
    active_buckets = resolve_buckets(buckets)
    safe_w = max(1.0, float(cand_w))
    safe_h = max(1.0, float(cand_h))
    cand_ar = safe_w / safe_h

    def _retained_fraction(bucket: tuple[int, int]) -> float:
        b_ar = float(bucket[0]) / float(bucket[1])
        return min(cand_ar / b_ar, b_ar / cand_ar)

    return max(active_buckets, key=_retained_fraction)


def center_crop_box(
    img_w: int,
    img_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, int, int]:
    """Compute exact centered crop rectangle (x0, y0, crop_w, crop_h) matching target aspect ratio."""
    safe_w = max(1, int(img_w))
    safe_h = max(1, int(img_h))
    target_ar = float(target_w) / float(max(1, target_h))
    src_ar = float(safe_w) / float(safe_h)

    if src_ar >= target_ar:
        crop_h = safe_h
        crop_w = max(1, min(safe_w, int(round(safe_h * target_ar))))
    else:
        crop_w = safe_w
        crop_h = max(1, min(safe_h, int(round(safe_w / target_ar))))

    x0 = max(0, (safe_w - crop_w) // 2)
    y0 = max(0, (safe_h - crop_h) // 2)
    return x0, y0, crop_w, crop_h


def center_crop_to_bucket(
    img: np.ndarray,
    bucket: tuple[int, int] | None = None,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Center-crop image to the nearest 1MP bucket aspect ratio and resize with Lanczos-4."""
    h, w = img.shape[:2]
    target_bucket = (
        (int(bucket[0]), int(bucket[1]))
        if bucket is not None
        else select_nearest_bucket(w, h, buckets=buckets)
    )
    bw, bh = target_bucket
    x0, y0, crop_w, crop_h = center_crop_box(w, h, bw, bh)
    cropped = img[y0 : y0 + crop_h, x0 : x0 + crop_w]
    if cropped.shape[1] != bw or cropped.shape[0] != bh:
        cropped = cv2.resize(cropped, (bw, bh), interpolation=cv2.INTER_LANCZOS4)
    return cropped, target_bucket


def _extract_box(wm: Any) -> list[float] | None:
    """Extract [ymin, xmin, ymax, xmax] from watermark dict or object."""
    if hasattr(wm, "box_2d"):
        return list(wm.box_2d)
    if isinstance(wm, dict):
        if "box_2d" in wm:
            return list(wm["box_2d"])
        if all(k in wm for k in ("ymin", "xmin", "ymax", "xmax")):
            return [float(wm["ymin"]), float(wm["xmin"]), float(wm["ymax"]), float(wm["xmax"])]
    if isinstance(wm, (list, tuple)) and len(wm) == BOX_COORD_LEN:
        return [float(v) for v in wm]
    return None


def _parse_padded_watermarks(
    watermarks: list[dict[str, Any]], img_w: int, img_h: int
) -> list[tuple[float, float, float, float]]:
    """Convert box_2d [0..1000] coordinates to padded pixel coordinates (ymin, xmin, ymax, xmax)."""
    pad_x = 0.02 * img_w
    pad_y = 0.02 * img_h
    outer_boxes: list[tuple[float, float, float, float]] = []

    for wm in watermarks:
        box = _extract_box(wm)
        if not box:
            continue
        ymin_px = (box[0] / 1000.0) * img_h
        xmin_px = (box[1] / 1000.0) * img_w
        ymax_px = (box[2] / 1000.0) * img_h
        xmax_px = (box[3] / 1000.0) * img_w

        # Check if overlapping outer 25% margin
        is_outer = (
            xmin_px <= 0.25 * img_w
            or xmax_px >= 0.75 * img_w
            or ymin_px <= 0.25 * img_h
            or ymax_px >= 0.75 * img_h
        )
        if is_outer:
            padded = (
                max(0.0, ymin_px - pad_y),
                max(0.0, xmin_px - pad_x),
                min(float(img_h), ymax_px + pad_y),
                min(float(img_w), xmax_px + pad_x),
            )
            outer_boxes.append(padded)

    return outer_boxes


def _init_candidate_bounds(img_w: int, img_h: int, borders: list[str]) -> tuple[float, float, float, float]:
    """Apply initial border trims from border flags."""
    x1, y1 = 0.0, 0.0
    x2, y2 = float(img_w), float(img_h)
    border_set = {b.lower().strip() for b in borders or []}
    if "up" in border_set or "top" in border_set:
        y1 = max(y1, 0.05 * img_h)
    if "down" in border_set or "bottom" in border_set:
        y2 = min(y2, 0.95 * img_h)
    if "left" in border_set:
        x1 = max(x1, 0.05 * img_w)
    if "right" in border_set:
        x2 = min(x2, 0.95 * img_w)
    return x1, y1, x2, y2


def _intersects_any(rx1: float, ry1: float, rx2: float, ry2: float, boxes: list[tuple[float, float, float, float]]) -> bool:
    """Check if candidate rectangle intersects any outer box."""
    return any(rx1 < bx2 and rx2 > bx1 and ry1 < by2 and ry2 > by1 for by1, bx1, by2, bx2 in boxes)


def _collect_edge_candidates(
    bounds: tuple[float, float, float, float],
    img_w: int,
    img_h: int,
    outer_boxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    x1, y1, x2, y2 = bounds
    top_y = max((b[2] for b in outer_boxes if b[0] <= 0.25 * img_h), default=y1)
    bot_y = min((b[0] for b in outer_boxes if b[2] >= 0.75 * img_h), default=y2)
    left_x = max((b[3] for b in outer_boxes if b[1] <= 0.25 * img_w), default=x1)
    right_x = min((b[1] for b in outer_boxes if b[3] >= 0.75 * img_w), default=x2)

    return [
        (x1, max(y1, top_y), x2, min(y2, bot_y)),
        (max(x1, left_x), y1, min(x2, right_x), y2),
        (max(x1, left_x), max(y1, top_y), min(x2, right_x), min(y2, bot_y)),
    ]


def _collect_individual_box_cuts(
    bounds: tuple[float, float, float, float],
    outer_boxes: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    x1, y1, x2, y2 = bounds
    cuts: list[tuple[float, float, float, float]] = []
    for by1, bx1, by2, bx2 in outer_boxes:
        cuts.extend([
            (x1, y1, x2, min(y2, by1)),
            (x1, max(y1, by2), x2, y2),
            (x1, y1, min(x2, bx1), y2),
            (max(x1, bx2), y1, x2, y2),
        ])
    return cuts


def _generate_candidate_rectangles(
    img_w: int,
    img_h: int,
    outer_boxes: list[tuple[float, float, float, float]],
    borders: list[str],
) -> list[tuple[float, float, float, float]]:
    """Generate candidate crop rectangles that avoid outer watermarks."""
    bounds = _init_candidate_bounds(img_w, img_h, borders)
    if not outer_boxes:
        return [bounds]

    raw_candidates = _collect_edge_candidates(bounds, img_w, img_h, outer_boxes)
    raw_candidates.extend(_collect_individual_box_cuts(bounds, outer_boxes))

    valid = [
        (rx1, ry1, rx2, ry2)
        for rx1, ry1, rx2, ry2 in raw_candidates
        if rx2 > rx1 and ry2 > ry1 and not _intersects_any(rx1, ry1, rx2, ry2, outer_boxes)
    ]
    return valid or [bounds]


def _select_nearest_bucket(cand_w: float, cand_h: float) -> tuple[int, int]:
    """Find closest MOSS bucket by aspect ratio."""
    return select_nearest_bucket(cand_w, cand_h, MOSS_BUCKETS)


def _fit_bucket_crop(
    cand: tuple[float, float, float, float],
    bucket: tuple[int, int],
    img_w: int,
    img_h: int,
) -> list[int]:
    """Center and snap crop box to bucket aspect ratio with multiples of 64."""
    cx1, cy1, cx2, cy2 = cand
    cw, ch = cx2 - cx1, cy2 - cy1
    bw, bh = bucket
    ratio = bw / bh

    if cw / ch >= ratio:
        h = max(BUCKET_STEP, (int(ch) // BUCKET_STEP) * BUCKET_STEP)
        w = max(BUCKET_STEP, (int(round(h * ratio)) // BUCKET_STEP) * BUCKET_STEP)
        while w > cw and h > BUCKET_STEP:
            h -= BUCKET_STEP
            w = max(BUCKET_STEP, (int(round(h * ratio)) // BUCKET_STEP) * BUCKET_STEP)
    else:
        w = max(BUCKET_STEP, (int(cw) // BUCKET_STEP) * BUCKET_STEP)
        h = max(BUCKET_STEP, (int(round(w / ratio)) // BUCKET_STEP) * BUCKET_STEP)
        while h > ch and w > BUCKET_STEP:
            w -= BUCKET_STEP
            h = max(BUCKET_STEP, (int(round(w / ratio)) // BUCKET_STEP) * BUCKET_STEP)

    x = int(cx1 + (cw - w) / 2.0)
    y = int(cy1 + (ch - h) / 2.0)

    # Clamp bounds safely
    x = max(int(cx1), min(int(cx2 - w), x))
    y = max(int(cy1), min(int(cy2 - h), y))
    x = max(0, min(img_w - w, x))
    y = max(0, min(img_h - h, y))

    return [int(x), int(y), int(w), int(h)]


def compute_optimal_moss_crop(
    img_w: int,
    img_h: int,
    watermarks: list[dict[str, Any]],
    borders: list[str],
) -> dict[str, Any]:
    """Deterministic watermark-avoiding smart crop snapped to MOSS-Image aspect-ratio buckets."""
    outer_boxes = _parse_padded_watermarks(watermarks, img_w, img_h)
    candidates = _generate_candidate_rectangles(img_w, img_h, outer_boxes, borders)

    # Pick candidate with maximal remaining area
    best_cand = max(candidates, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
    cand_w = best_cand[2] - best_cand[0]
    cand_h = best_cand[3] - best_cand[1]

    target_bucket = _select_nearest_bucket(cand_w, cand_h)
    crop_box = _fit_bucket_crop(best_cand, target_bucket, img_w, img_h)

    bw, bh = target_bucket
    gcd = math.gcd(bw, bh)
    aspect_ratio_str = f"{bw // gcd}:{bh // gcd}"
    area_retained = round((float(crop_box[2] * crop_box[3]) / float(img_w * img_h)) * 100.0, 2)

    return {
        "crop_box": crop_box,
        "target_bucket": [bw, bh],
        "aspect_ratio": aspect_ratio_str,
        "area_retained_pct": area_retained,
    }


def _resolve_target_bucket(
    target_bucket: tuple[int, int] | list[int] | None,
) -> tuple[int, int] | None:
    if target_bucket is None or len(target_bucket) != BUCKET_DIM_LEN:
        return None
    cand = (int(target_bucket[0]), int(target_bucket[1]))
    if cand in MOSS_BUCKETS or cand in BUCKETS_1MP_3:
        return cand
    return select_nearest_bucket(cand[0], cand[1], MOSS_BUCKETS)


def apply_moss_crop(
    img: np.ndarray,
    crop_box: list[int] | tuple[int, int, int, int],
    target_bucket: tuple[int, int] | list[int] | None = None,
) -> np.ndarray:
    """Apply crop box, center-crop to target bucket aspect ratio, and resize to 1MP bucket."""
    if img is None or img.size == 0:
        return img

    h_img, w_img = img.shape[:2]
    x, y, w, h = crop_box
    x0 = max(0, min(w_img - 1, int(x)))
    y0 = max(0, min(h_img - 1, int(y)))
    x1 = max(x0 + 1, min(w_img, x0 + int(w)))
    y1 = max(y0 + 1, min(h_img, y0 + int(h)))

    cropped = img[y0:y1, x0:x1]
    bucket_tuple = _resolve_target_bucket(target_bucket)
    normalized, _ = center_crop_to_bucket(cropped, bucket=bucket_tuple, buckets=MOSS_BUCKETS)
    return normalized
