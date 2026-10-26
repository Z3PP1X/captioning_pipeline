"""Mathematically deterministic, ML-free letterbox scanner."""
from __future__ import annotations

import numpy as np

LUMA_MEAN_THRESHOLD = 12.0
LUMA_STD_THRESHOLD = 3.5


def _to_luminance(img: np.ndarray) -> np.ndarray:
    """Convert input 2D or 3D image to 2D luminance float array."""
    if img.ndim == 2:
        return img.astype(np.float32)
    if img.ndim == 3 and img.shape[2] >= 3:
        return (
            0.299 * img[:, :, 0].astype(np.float32)
            + 0.587 * img[:, :, 1].astype(np.float32)
            + 0.114 * img[:, :, 2].astype(np.float32)
        )
    if img.ndim == 3:
        return img[:, :, 0].astype(np.float32)
    raise ValueError(f"Unsupported image shape for letterbox scanning: {img.shape}")


def _find_border_range(means: np.ndarray, stds: np.ndarray, length: int) -> tuple[int, int]:
    """Scan from edges inward to find trimmed counts (start_trim, end_trim)."""
    start = 0
    while start < length and means[start] < LUMA_MEAN_THRESHOLD and stds[start] < LUMA_STD_THRESHOLD:
        start += 1

    if start == length:
        return length, 0

    end = 0
    idx = length - 1
    while idx >= start and means[idx] < LUMA_MEAN_THRESHOLD and stds[idx] < LUMA_STD_THRESHOLD:
        end += 1
        idx -= 1

    return start, end


def crop_letterbox_numpy(img: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    """Detect outer black bars from outside-in and crop losslessly.

    Returns:
        (cropped_image, {"top": int, "bottom": int, "left": int, "right": int})
    """
    zero_bounds = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    if img is None or img.size == 0:
        return img, zero_bounds

    h, w = img.shape[:2]
    if h == 0 or w == 0:
        return img, zero_bounds

    luma = _to_luminance(img)
    row_means = np.mean(luma, axis=1)
    row_stds = np.std(luma, axis=1)
    col_means = np.mean(luma, axis=0)
    col_stds = np.std(luma, axis=0)

    top_trim, bottom_trim = _find_border_range(row_means, row_stds, h)
    left_trim, right_trim = _find_border_range(col_means, col_stds, w)

    if (top_trim + bottom_trim >= h) or (left_trim + right_trim >= w):
        return img, zero_bounds

    if top_trim == 0 and bottom_trim == 0 and left_trim == 0 and right_trim == 0:
        return img, zero_bounds

    cropped = img[top_trim : h - bottom_trim, left_trim : w - right_trim]
    bounds = {
        "top": int(top_trim),
        "bottom": int(bottom_trim),
        "left": int(left_trim),
        "right": int(right_trim),
    }
    return cropped, bounds
