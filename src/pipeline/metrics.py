"""Tier-1 image quality metrics: tile sharpness, exposure, detail entropy, pHash, and hard filtering."""
from __future__ import annotations

import cv2
import numpy as np


def compute_phash(gray: np.ndarray) -> str:
    """Compute 64-bit DCT perceptual hash as 16-char hex string."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct = cv2.dct(small)[:8, :8]
    med = np.median(dct)
    bits = (dct > med).flatten()
    val = 0
    for b in bits:
        val = (val << 1) | int(b)
    return f"{val:016x}"


def phash_hamming_distance(h1: str, h2: str) -> int:
    """Compute Hamming distance between two 16-char hex pHash strings."""
    v1 = int(h1, 16)
    v2 = int(h2, 16)
    return bin(v1 ^ v2).count("1")


def compute_tier1_metrics(img_bgr: np.ndarray) -> dict:
    """Evaluate Tier-1 metrics on 512px downscaled frame (~4.5 ms per frame)."""
    h, w = img_bgr.shape[:2]
    scale = 512.0 / max(h, w)
    small_w = max(16, int(w * scale))
    small_h = max(16, int(h * scale))
    small_bgr = cv2.resize(img_bgr, (small_w, small_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)

    # 1. Tile sharpness on 4x4 grid (average of top 3 sharpest tiles to handle shallow DoF)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    gh, gw = gray.shape
    tiles: list[float] = []
    for y in range(4):
        y0, y1 = y * gh // 4, (y + 1) * gh // 4
        for x in range(4):
            x0, x1 = x * gw // 4, (x + 1) * gw // 4
            tiles.append(float(lap[y0:y1, x0:x1].var()))
    tile_sharp = float(np.mean(sorted(tiles)[-3:]))

    # 2. Exposure & Contrast
    mean_luma = float(gray.mean())
    clipping = float(((gray <= 2) | (gray >= 253)).mean())
    contrast = float(gray.std())

    # 3. Detail (Shannon entropy of luminance histogram)
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    probs = hist / hist.sum()
    nonzero = probs[probs > 0]
    entropy = float(-(nonzero * np.log2(nonzero)).sum())

    # 4. Perceptual Hash
    phash = compute_phash(gray)

    return {
        "tile_sharp": round(tile_sharp, 2),
        "mean_luma": round(mean_luma, 2),
        "clipping": round(clipping, 4),
        "contrast": round(contrast, 2),
        "entropy": round(entropy, 3),
        "phash": phash,
    }


def hard_filter_check(metrics: dict, sharpness_floor: float = 15.0) -> tuple[bool, str | None]:
    """Check if a frame fails hard acceptance filters."""
    if metrics["mean_luma"] < 10.0:
        return False, "Black / dark void frame"
    if metrics["mean_luma"] > 248.0:
        return False, "Blown-out white frame"
    if metrics["clipping"] > 0.40:
        return False, f"Severe pixel clipping ({metrics['clipping']*100:.0f}%)"
    if metrics["tile_sharp"] < sharpness_floor:
        return False, f"Below sharpness floor ({metrics['tile_sharp']:.1f} < {sharpness_floor:.1f})"
    return True, None


def percentile_ranks(values: list[float]) -> list[float]:
    """Compute percentile ranks [0.0, 1.0] for a list of values."""
    if not values:
        return []
    n = len(values)
    if n == 1:
        return [1.0]
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    for rank_idx, (orig_idx, _) in enumerate(indexed):
        ranks[orig_idx] = rank_idx / (n - 1)
    return ranks


def filter_and_rank_tier1(
    candidates: list[dict],
    weights: dict | None = None,
    survivor_frac: float = 0.5,
    min_survivors: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Filter hard rejects, rank by Tier-1 metrics, deduplicate by pHash.
    
    Returns (survivors, rejected).
    """
    w_s = (weights or {}).get("sharpness", 0.45)
    w_e = (weights or {}).get("exposure", 0.35)
    w_d = (weights or {}).get("detail", 0.20)

    passed: list[dict] = []
    rejected: list[dict] = []

    for c in candidates:
        ok, reason = hard_filter_check(c["metrics"])
        if ok:
            passed.append(c)
        else:
            c["rejected_reason"] = reason
            rejected.append(c)

    if not passed:
        return [], rejected

    # Relative percentile ranks within chunk
    sharp_ranks = percentile_ranks([c["metrics"]["tile_sharp"] for c in passed])
    contrast_ranks = percentile_ranks([c["metrics"]["contrast"] for c in passed])
    detail_ranks = percentile_ranks([c["metrics"]["entropy"] for c in passed])

    for i, c in enumerate(passed):
        m = c["metrics"]
        # Exposure score: rewards contrast rank, penalizes distance from mid-grey & clipping
        mid_dist_penalty = abs(m["mean_luma"] - 128.0) / 128.0  # [0, 1]
        exp_score = max(0.0, 1.0 - (0.5 * mid_dist_penalty + 1.5 * m["clipping"])) * 0.5 + contrast_ranks[i] * 0.5
        tier1_score = w_s * sharp_ranks[i] + w_e * exp_score + w_d * detail_ranks[i]
        c["tier1_score"] = round(tier1_score, 4)

    # Sort descending by tier1_score
    passed.sort(key=lambda x: x["tier1_score"], reverse=True)

    # pHash deduplication among candidates (drop duplicates with Hamming distance <= 4)
    deduped: list[dict] = []
    for c in passed:
        is_dup = False
        for kept in deduped:
            if phash_hamming_distance(c["metrics"]["phash"], kept["metrics"]["phash"]) <= 4:
                is_dup = True
                c["rejected_reason"] = f"Near-duplicate of frame at {kept['t']:.2f}s"
                rejected.append(c)
                break
        if not is_dup:
            deduped.append(c)

    # Keep survivor fraction
    cutoff = max(min_survivors, int(len(deduped) * survivor_frac))
    survivors = deduped[:cutoff]
    for c in deduped[cutoff:]:
        c["rejected_reason"] = "Dropped by survivor cutoff"
        rejected.append(c)

    return survivors, rejected
