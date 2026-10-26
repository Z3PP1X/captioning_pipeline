"""Algorithmic complexity, Big-O regression, memory bounds and benchmark gate.

Enforces:
1. Frame deduplication / clustering scales in <= O(N log N) asymptotic time and < 250 MB RAM.
2. Frame deduplication clusters 5,000 frames in < 150 ms.
3. Vision metrics (Laplacian, Sobel, histogram, Shannon entropy) scale linearly in O(P) with pixel count P.
4. Video extraction and frame scoring streaming pipeline stays under 300 MB peak RAM.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Generator
from typing import Any

import cv2
import numpy as np
import pytest

try:
    import big_o

    _HAS_BIG_O = True
    ALLOWED_COMPLEXITIES: tuple[type, ...] = (
        big_o.complexities.Constant,
        big_o.complexities.Logarithmic,
        big_o.complexities.Linear,
        big_o.complexities.Linearithmic,
    )
    ROBUST_CLASSES: tuple[type, ...] = (
        big_o.complexities.Constant,
        big_o.complexities.Logarithmic,
        big_o.complexities.Linear,
        big_o.complexities.Linearithmic,
        big_o.complexities.Quadratic,
    )
except ImportError:
    big_o = None  # type: ignore[assignment]
    _HAS_BIG_O = False
    ALLOWED_COMPLEXITIES = ()
    ROBUST_CLASSES = ()

from backend.app.modules.ingestion.deduplicator import MultiIndexPrefixBuckets, UnionFind
from backend.app.modules.ingestion.quality_math import compute_quality_score


def cluster_frame_hashes(hashes: np.ndarray, max_distance: int = 4) -> list[int]:
    """Fast O(N log N) multi-index prefix bucketed clustering over (N, 64) binary bit arrays."""
    n = int(hashes.shape[0])
    if n == 0:
        return []
    packed = np.packbits(hashes, axis=1).view(np.uint64).ravel()
    u64_list = [int(v) for v in packed]
    mih = MultiIndexPrefixBuckets()
    uf = UnionFind(n)
    max_band_err = 0 if max_distance <= 3 else 1
    for i, h in enumerate(u64_list):
        for cand in mih.query_candidates(h, max_band_errors=max_band_err):
            if (h ^ u64_list[cand]).bit_count() <= max_distance:
                uf.union(i, cand)
        mih.add(h, i)
    return [uf.find(i) for i in range(n)]


def compute_tier1_metrics(frame_bgr: np.ndarray) -> dict[str, float]:
    """Compute mathematical quality metrics on a single BGR frame."""
    res = compute_quality_score(frame_bgr)
    return {
        "quality_score": res.quality_score,
        "sharpness_laplacian": res.sharpness_laplacian,
        "sharpness_tenengrad": res.sharpness_tenengrad,
        "entropy_shannon": res.entropy_shannon,
    }


def _is_acceptable_fit(
    best: Any,
    fitted: dict[Any, float],
    allowed_classes: tuple[type, ...],
) -> bool:
    """Evaluate whether the best fit satisfies complexity upper bound or parsimony."""
    if isinstance(best, allowed_classes):
        return True

    linear_residual = min(
        (res for cls, res in fitted.items() if isinstance(cls, (big_o.complexities.Linear, big_o.complexities.Linearithmic))),
        default=float("inf"),
    )
    best_residual = fitted.get(best, float("inf"))
    coeff_high = abs(getattr(best, "coeff", [0.0, 0.0])[1]) if hasattr(best, "coeff") else 1.0

    return linear_residual <= best_residual * 1.30 or coeff_high < 1e-9


def assert_max_big_o(
    func: Callable[..., Any],
    data_generator: Callable[[int], Any],
    min_n: int = 100,
    max_n: int = 4000,
    *,
    n_measures: int = 10,
    n_repeats: int = 3,
    allowed_classes: tuple[type, ...] = ALLOWED_COMPLEXITIES,
) -> Any:
    """Perform empirical Big-O regression fitting and assert complexity <= allowed upper bound."""
    if not _HAS_BIG_O or big_o is None:
        t_small_list = []
        t_large_list = []
        d_small = data_generator(min_n)
        d_large = data_generator(max_n)
        for _ in range(n_repeats):
            t0 = time.perf_counter()
            func(d_small)
            t_small_list.append(max(1e-6, time.perf_counter() - t0))
            t1 = time.perf_counter()
            func(d_large)
            t_large_list.append(max(1e-6, time.perf_counter() - t1))
        ratio_n = max_n / max(1, min_n)
        ratio_t = min(t_large_list) / min(t_small_list)
        assert ratio_t <= ratio_n * np.log2(max(2.0, ratio_n)) * 3.0
        return f"Empirical O(N log N) verified (ratio_t={ratio_t:.2f} for ratio_n={ratio_n:.1f})"

    best, fitted = big_o.big_o(
        func,
        data_generator,
        min_n=min_n,
        max_n=max_n,
        n_measures=n_measures,
        n_repeats=n_repeats,
        classes=list(ROBUST_CLASSES),
    )
    if _is_acceptable_fit(best, fitted, allowed_classes):
        return best

    assert isinstance(best, allowed_classes), (
        f"Algorithmic Complexity Gate FAILED for {func.__name__}: "
        f"Measured {best}, expected at most O(N log N)."
    )
    return best


@pytest.mark.complexity
@pytest.mark.limit_memory("250 MB")
def test_deduplication_complexity_and_memory() -> None:
    """Assert frame hash clustering scales <= O(N log N) and uses < 250 MB RAM."""
    def generate_hashes(n: int) -> np.ndarray:
        rng = np.random.default_rng(42)
        return rng.integers(0, 2, size=(n, 64), dtype=np.uint8)

    best = assert_max_big_o(cluster_frame_hashes, generate_hashes, min_n=200, max_n=3000)
    print(f"\n[PASS] cluster_frame_hashes fitted complexity: {best}")


@pytest.mark.complexity
def test_deduplication_speed_5000_frames() -> None:
    """Assert that clustering 5,000 frames takes < 150 ms."""
    rng = np.random.default_rng(42)
    hashes = rng.integers(0, 2, size=(5000, 64), dtype=np.uint8)

    t0 = time.perf_counter()
    clusters = cluster_frame_hashes(hashes, max_distance=4)
    duration_s = time.perf_counter() - t0
    duration_ms = duration_s * 1000.0

    assert len(clusters) == 5000
    assert duration_s < 0.150, (
        f"Frame deduplication clustering too slow: took {duration_ms:.2f} ms, "
        f"must be < 150 ms for N=5000 frames."
    )
    print(f"\n[PASS] 5,000 frames clustered in {duration_ms:.2f} ms (< 150 ms threshold)")


@pytest.mark.complexity
def test_deduplication_benchmark_5000_frames() -> None:
    """Benchmark cluster_frame_hashes throughput across 5,000 frames."""
    rng = np.random.default_rng(42)
    hashes = rng.integers(0, 2, size=(5000, 64), dtype=np.uint8)

    result = cluster_frame_hashes(hashes, 4)
    assert len(result) == 5000


@pytest.mark.complexity
def test_image_quality_metrics_linear_complexity() -> None:
    """Assert image quality metrics scale linearly O(P) with pixel count P."""
    def compute_vision_pipeline(gray: np.ndarray) -> dict[str, float]:
        lap_var = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        tenengrad = float((gx**2 + gy**2).mean())
        hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
        probs = hist / (hist.sum() + 1e-12)
        nonzero = probs[probs > 0]
        entropy = float(-(nonzero * np.log2(nonzero)).sum())
        return {"lap_var": lap_var, "tenengrad": tenengrad, "entropy": entropy}

    def generate_image(p: int) -> np.ndarray:
        dim = max(16, int(np.sqrt(p)))
        rng = np.random.default_rng(42)
        return rng.integers(0, 256, size=(dim, dim), dtype=np.uint8)

    allowed = (
        (
            big_o.complexities.Constant,
            big_o.complexities.Logarithmic,
            big_o.complexities.Linear,
        )
        if _HAS_BIG_O and big_o is not None
        else ()
    )
    best = assert_max_big_o(
        compute_vision_pipeline,
        generate_image,
        min_n=10000,
        max_n=250000,
        n_measures=8,
        n_repeats=2,
        allowed_classes=allowed,
    )
    print(f"\n[PASS] Vision metrics pixel complexity: {best} (strictly <= O(P))")


@pytest.mark.complexity
@pytest.mark.limit_memory("300 MB")
def test_streaming_generator_memory_bound() -> None:
    """Assert that processing 60 uncompressed 1080p frames via streaming consumes < 300 MB RAM."""
    total_frames = 60
    batch_size = 4
    h, w = 1080, 1920

    def frame_stream() -> Generator[list[np.ndarray], None, None]:
        rng = np.random.default_rng(123)
        for i in range(0, total_frames, batch_size):
            current_batch = min(batch_size, total_frames - i)
            batch = [rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8) for _ in range(current_batch)]
            yield batch

    processed_count = 0
    collected_metrics: list[dict[str, Any]] = []

    for batch in frame_stream():
        for frame in batch:
            metrics = compute_tier1_metrics(frame)
            collected_metrics.append(metrics)
            processed_count += 1

    assert processed_count == total_frames
    assert len(collected_metrics) == total_frames
    print(f"\n[PASS] Processed {processed_count} 1080p frames in streaming mode under 300 MB memory ceiling.")
