"""Pure mathematical, zero-ML image and video frame quality scoring (Module A.2).

Evaluates any BGR or grayscale image in <3 ms (at <=512px evaluation scale) using:
1. Sharpness / Blur Detection:
   - 4x4 tile Laplacian variance (top-3 tiles to handle shallow DoF/bokeh) with Immerkaer
     flat-region sensor noise subtraction (20 * sigma_noise^2).
   - Whole-frame resolution-normalized Laplacian variance.
   - Tenengrad gradient energy (Sobel Gx^2 + Gy^2) on the central 60% subject region.
2. Exposure & Contrast:
   - Shadow (< 5) and highlight (> 250) histogram clipping fractions.
   - Chiaroscuro-aware luminance balance (adapts target luminance from 128 to 95 for high-contrast
     low-key studio lighting).
   - Root-Mean-Square (RMS) contrast sigma_I.
3. Information Density:
   - 256-bin Shannon entropy H(I) = -sum(p_k * log2(p_k)) in [0, 8] bits.
4. Hard Quality Gates:
   - Automatic rejection flags: `too_dark`, `blown_out`, `severe_clipping`, `low_entropy`,
     `letterbox_card`, `motion_blur`.
5. Composite Quality Score Q in [0, 1] with configurable weights.
"""
from __future__ import annotations

import math
from typing import Sequence

import cv2
import numpy as np

from backend.app.models.domain import CurationState, QualityScoreResult, QualityWeights

# Default reference constants calibrated for <=512px luminance evaluation
DEFAULT_LAPLACIAN_REF = 350.0
DEFAULT_TENENGRAD_REF = 45.0
DEFAULT_CONTRAST_REF = 60.0
DEFAULT_ENTROPY_REF = 7.5

# Default hard gate thresholds
GATE_MIN_LUMA = 10.0
GATE_MAX_LUMA = 248.0
GATE_MAX_CLIPPING = 0.40
GATE_MIN_ENTROPY = 2.20
GATE_MAX_BORDER_BLACK = 0.45
GATE_MIN_LAPLACIAN_SHARP = 15.0
GATE_RELATIVE_SHARP_FACTOR = 0.25

# Pre-allocated 3x3 Immerkaer noise estimation kernel
_IMMERKAER_KERNEL = np.array(
    [[1.0, -2.0, 1.0], [-2.0, 4.0, -2.0], [1.0, -2.0, 1.0]],
    dtype=np.float32,
)
_IMMERKAER_SCALE = math.sqrt(math.pi / 2.0) / 6.0


def _prepare_eval_gray(img: np.ndarray, max_eval_dim: int = 512) -> tuple[np.ndarray, float]:
    """Convert BGR/BGRA/gray image to uint8 grayscale downscaled to max_eval_dim on longest edge.

    Returns (gray_u8, scale_factor_from_eval_to_512).
    """
    if img is None or img.size == 0:
        raise ValueError("Input image is empty or None")

    if img.ndim == 3:
        if img.shape[2] == 4:
            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        elif img.shape[2] == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img[:, :, 0]
    elif img.ndim == 2:
        gray = img
    else:
        raise ValueError(f"Unsupported image dimensions: {img.shape}")

    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)

    h, w = gray.shape[:2]
    longest = max(h, w)
    if max_eval_dim > 0 and longest > max_eval_dim:
        scale = max_eval_dim / float(longest)
        new_w = max(16, int(round(w * scale)))
        new_h = max(16, int(round(h * scale)))
        gray = cv2.resize(gray, (new_w, new_h), interpolation=cv2.INTER_AREA)
        longest = max(new_h, new_w)

    norm_scale = 512.0 / float(max(16, longest))
    return gray, norm_scale


def border_black_fraction(gray: np.ndarray, thr: float = 16.0) -> float:
    """Compute fraction of frame covered by uniform dark border bars (letterbox / pillarbox)."""
    h, w = gray.shape[:2]
    if h < 4 or w < 4:
        return 0.0

    row_means = gray.mean(axis=1)
    row_stds = gray.std(axis=1)
    col_means = gray.mean(axis=0)
    col_stds = gray.std(axis=0)

    rows_dark = (row_means < thr) & (row_stds < 8.0)
    cols_dark = (col_means < thr) & (col_stds < 8.0)

    def _leading_true_count(mask: np.ndarray) -> int:
        inv = ~mask
        if not inv.any():
            return int(mask.size)
        return int(np.argmax(inv))

    top = _leading_true_count(rows_dark)
    bottom = _leading_true_count(rows_dark[::-1])
    left = _leading_true_count(cols_dark)
    right = _leading_true_count(cols_dark[::-1])

    if top + bottom >= h or left + right >= w:
        return 1.0
    inner_area = (h - top - bottom) * (w - left - right)
    return float(1.0 - inner_area / float(h * w))


def estimate_immerkaer_noise(
    gray_f32: np.ndarray,
    sobel_gx: np.ndarray | None = None,
    sobel_gy: np.ndarray | None = None,
) -> float:
    """Fast Immerkaer sensor noise estimator restricted to the flatter 50% of the frame.

    Restricting to low-gradient pixels prevents high-frequency subject texture from being
    misclassified as sensor noise.
    """
    h, w = gray_f32.shape[:2]
    if h < 8 or w < 8:
        return 0.0

    resp = np.abs(cv2.filter2D(gray_f32, cv2.CV_32F, _IMMERKAER_KERNEL))[1:-1, 1:-1]
    if sobel_gx is None:
        sobel_gx = cv2.Sobel(gray_f32, cv2.CV_32F, 1, 0, ksize=3)
    if sobel_gy is None:
        sobel_gy = cv2.Sobel(gray_f32, cv2.CV_32F, 0, 1, ksize=3)

    mag = np.abs(sobel_gx[1:-1, 1:-1]) + np.abs(sobel_gy[1:-1, 1:-1])
    flat_threshold = float(np.median(mag))
    mask = mag <= flat_threshold
    if not mask.any():
        return 0.0
    return float(_IMMERKAER_SCALE * float(resp[mask].mean()))


def compute_sharpness_metrics(
    gray: np.ndarray,
    norm_scale: float = 1.0,
    laplacian_ref: float = DEFAULT_LAPLACIAN_REF,
    tenengrad_ref: float = DEFAULT_TENENGRAD_REF,
) -> tuple[float, float, float, float, float]:
    """Compute Laplacian tile variance, global Laplacian variance, Tenengrad gradient, noise sigma, and S_norm.

    Returns:
        (lap_tile_denoised, lap_global_norm, tenengrad_sqrt, noise_sigma, sharpness_score)
    """
    h, w = gray.shape[:2]
    gray_f32 = gray.astype(np.float32)

    # 3x3 Laplacian response
    lap = cv2.Laplacian(gray_f32, cv2.CV_32F, ksize=1)
    # Resolution normalization factor so smaller images are comparable to 512px reference
    scale_sq = norm_scale * norm_scale
    lap_global_norm = float(lap.var()) * scale_sq

    # 4x4 spatial grid tile variances (top-3 tiles handle shallow DoF / portrait bokeh)
    tile_vars: list[float] = []
    for ty in range(4):
        y0, y1 = (ty * h) // 4, ((ty + 1) * h) // 4
        for tx in range(4):
            x0, x1 = (tx * w) // 4, ((tx + 1) * w) // 4
            tile = lap[y0:y1, x0:x1]
            if tile.size > 0:
                tile_vars.append(float(tile.var()) * scale_sq)

    if tile_vars:
        top3 = sorted(tile_vars)[-3:]
        raw_tile_sharp = float(sum(top3) / len(top3))
    else:
        raw_tile_sharp = lap_global_norm

    # Sobel gradients (reused for both Tenengrad and Immerkaer flat-region mask)
    gx = cv2.Sobel(gray_f32, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_f32, cv2.CV_32F, 0, 1, ksize=3)

    sigma_noise = estimate_immerkaer_noise(gray_f32, gx, gy)
    lap_tile_denoised = max(0.0, raw_tile_sharp - 20.0 * (sigma_noise * sigma_noise))

    # Central 60% subject region Tenengrad energy: T(I) = mean(Gx^2 + Gy^2)
    cy0, cy1 = int(h * 0.2), max(int(h * 0.2) + 1, int(h * 0.8))
    cx0, cx1 = int(w * 0.2), max(int(w * 0.2) + 1, int(w * 0.8))
    cgx = gx[cy0:cy1, cx0:cx1]
    cgy = gy[cy0:cy1, cx0:cx1]
    tenengrad_energy = float(np.mean(cgx * cgx + cgy * cgy))
    tenengrad_sqrt = math.sqrt(max(0.0, tenengrad_energy)) * norm_scale

    # Combined normalized sharpness S_norm in [0, 1]
    s_lap = min(1.0, lap_tile_denoised / max(1e-6, laplacian_ref))
    s_ten = min(1.0, tenengrad_sqrt / max(1e-6, tenengrad_ref))
    sharpness_score = float(np.clip(0.60 * s_lap + 0.40 * s_ten, 0.0, 1.0))

    return (
        lap_tile_denoised,
        lap_global_norm,
        tenengrad_sqrt,
        sigma_noise,
        sharpness_score,
    )


def compute_exposure_metrics(
    gray: np.ndarray,
    hist: np.ndarray | None = None,
    contrast_ref: float = DEFAULT_CONTRAST_REF,
) -> tuple[float, float, float, float, float, float, float]:
    """Compute exposure clipping, chiaroscuro-aware exposure score, and RMS contrast.

    Returns:
        (mean_luma, shadow_clip, highlight_clip, total_clip, exposure_score, rms_contrast, contrast_score)
    """
    total_pixels = float(max(1, gray.size))
    if hist is None:
        hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)

    # Shadow (< 5) and Highlight (> 250) clipping fractions from histogram
    shadow_clip = float(hist[:5].sum() / total_pixels)
    highlight_clip = float(hist[251:].sum() / total_pixels)
    total_clip = float(min(1.0, shadow_clip + highlight_clip))

    # Mean luminance and RMS contrast (standard deviation)
    bins = np.arange(256, dtype=np.float64)
    probs = hist / total_pixels
    mean_luma = float(np.dot(bins, probs))
    variance = float(np.dot((bins - mean_luma) ** 2, probs))
    rms_contrast = math.sqrt(max(0.0, variance))

    # Chiaroscuro-aware target luminance: low-key high-contrast studio lighting targets 95 instead of 128
    target_luma = 95.0 if (mean_luma < 128.0 and rms_contrast >= 35.0) else 128.0
    exposure_score = float(
        np.clip(
            1.0 - 0.5 * (abs(mean_luma - target_luma) / 128.0) - 1.5 * total_clip,
            0.0,
            1.0,
        )
    )
    contrast_score = float(np.clip(rms_contrast / max(1e-6, contrast_ref), 0.0, 1.0))

    return (
        mean_luma,
        shadow_clip,
        highlight_clip,
        total_clip,
        exposure_score,
        rms_contrast,
        contrast_score,
    )


def compute_entropy_shannon(
    gray: np.ndarray,
    hist: np.ndarray | None = None,
    entropy_ref: float = DEFAULT_ENTROPY_REF,
) -> tuple[float, float]:
    """Compute 256-bin Shannon entropy H(I) in [0, 8] bits and normalized entropy score in [0, 1]."""
    if hist is None:
        hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = float(hist.sum())
    if total <= 0.0:
        return 0.0, 0.0
    probs = hist / total
    nonzero = probs[probs > 0.0]
    entropy = float(-np.sum(nonzero * np.log2(nonzero)))
    entropy_score = float(np.clip(entropy / max(1e-6, entropy_ref), 0.0, 1.0))
    return entropy, entropy_score


def evaluate_hard_gates(
    mean_luma: float,
    total_clipping: float,
    entropy_shannon: float,
    border_black_frac: float,
    sharpness_laplacian: float,
    *,
    min_luma: float = GATE_MIN_LUMA,
    max_luma: float = GATE_MAX_LUMA,
    max_clipping: float = GATE_MAX_CLIPPING,
    min_entropy: float = GATE_MIN_ENTROPY,
    max_border_black: float = GATE_MAX_BORDER_BLACK,
    min_sharpness: float = GATE_MIN_LAPLACIAN_SHARP,
) -> tuple[bool, list[str]]:
    """Evaluate deterministic hard quality rejection gates.

    Gate identifiers:
        - `too_dark`: Mean luminance < 10.0
        - `blown_out`: Mean luminance > 248.0
        - `severe_clipping`: Shadow + highlight clipping fraction > 0.40
        - `low_entropy`: Shannon entropy < 2.20 bits (solid/empty transition frame)
        - `letterbox_card`: Uniform dark borders > 0.45 of frame area
        - `motion_blur`: Denoised top-3 tile Laplacian variance < 15.0
    """
    reasons: list[str] = []
    if mean_luma < min_luma:
        reasons.append("too_dark")
    elif mean_luma > max_luma:
        reasons.append("blown_out")

    if total_clipping > max_clipping:
        reasons.append("severe_clipping")

    if entropy_shannon < min_entropy:
        reasons.append("low_entropy")

    if border_black_frac > max_border_black:
        reasons.append("letterbox_card")

    if sharpness_laplacian < min_sharpness:
        reasons.append("motion_blur")

    return (len(reasons) == 0), reasons


def compute_quality_score(
    img: np.ndarray,
    weights: QualityWeights | None = None,
    *,
    max_eval_dim: int = 512,
    min_sharpness_gate: float = GATE_MIN_LAPLACIAN_SHARP,
) -> QualityScoreResult:
    """Compute complete mathematical quality metrics, hard gates, and composite score Q in [0, 1]."""
    gray, norm_scale = _prepare_eval_gray(img, max_eval_dim=max_eval_dim)

    # Single-pass 256-bin luminance histogram shared by exposure, contrast, and entropy
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)

    (
        lap_tile,
        lap_global,
        tenengrad_sqrt,
        sigma_noise,
        sharpness_score,
    ) = compute_sharpness_metrics(gray, norm_scale=norm_scale)

    (
        mean_luma,
        shadow_clip,
        highlight_clip,
        total_clip,
        exposure_score,
        rms_contrast,
        contrast_score,
    ) = compute_exposure_metrics(gray, hist=hist)

    entropy_shannon, entropy_score = compute_entropy_shannon(gray, hist=hist)
    border_black = border_black_fraction(gray)

    passed_gates, reject_reasons = evaluate_hard_gates(
        mean_luma=mean_luma,
        total_clipping=total_clip,
        entropy_shannon=entropy_shannon,
        border_black_frac=border_black,
        sharpness_laplacian=lap_tile,
        min_sharpness=min_sharpness_gate,
    )

    w_s, w_e, w_h, w_c = (weights or QualityWeights()).normalized()
    composite_q = float(
        np.clip(
            w_s * sharpness_score
            + w_e * exposure_score
            + w_h * entropy_score
            + w_c * contrast_score,
            0.0,
            1.0,
        )
    )

    curation_state: CurationState = "candidate" if passed_gates else "auto_rejected"
    primary_reason = reject_reasons[0] if reject_reasons else None

    return QualityScoreResult(
        sharpness_laplacian=lap_tile,
        sharpness_laplacian_global=lap_global,
        sharpness_tenengrad=tenengrad_sqrt,
        sharpness_score=sharpness_score,
        noise_sigma=sigma_noise,
        exposure_mean_luma=mean_luma,
        shadow_clipping_frac=shadow_clip,
        highlight_clipping_frac=highlight_clip,
        exposure_clipping_frac=total_clip,
        exposure_score=exposure_score,
        entropy_shannon=entropy_shannon,
        entropy_score=entropy_score,
        contrast_rms=rms_contrast,
        contrast_score=contrast_score,
        border_black_frac=border_black,
        quality_score=composite_q,
        passed_gates=passed_gates,
        reject_reasons=reject_reasons,
        primary_reject_reason=primary_reason,
        curation_state=curation_state,
    )


def compute_quality_scores_batch(
    images: Sequence[np.ndarray],
    weights: QualityWeights | None = None,
    *,
    max_eval_dim: int = 512,
    apply_relative_blur_gate: bool = False,
) -> list[QualityScoreResult]:
    """Evaluate a batch of images and optionally apply chunk-relative blur gating."""
    results = [
        compute_quality_score(img, weights=weights, max_eval_dim=max_eval_dim)
        for img in images
    ]
    if not apply_relative_blur_gate or len(results) < 4:
        return results

    valid_sharp = [
        r.sharpness_laplacian
        for r in results
        if GATE_MIN_LUMA <= r.exposure_mean_luma <= GATE_MAX_LUMA
    ]
    if not valid_sharp:
        return results

    rel_floor = max(
        GATE_MIN_LAPLACIAN_SHARP,
        GATE_RELATIVE_SHARP_FACTOR * float(np.median(valid_sharp)),
    )
    for r in results:
        if r.sharpness_laplacian < rel_floor and "motion_blur" not in r.reject_reasons:
            r.reject_reasons.append("motion_blur")
            r.passed_gates = False
            r.primary_reject_reason = r.reject_reasons[0]
            r.curation_state = "auto_rejected"

    return results
