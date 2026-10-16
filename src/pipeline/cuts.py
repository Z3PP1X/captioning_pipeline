"""Frame-accurate shot-cut detection without new dependencies (Phase F2, review §4 Stage A).

A PyAV pass decodes every frame at the native frame rate, downscaled in swscale (~96 px wide), and computes per
frame the PySceneDetect ContentDetector score: the mean absolute difference of H, S and V to the previous frame
(OpenCV HSV, 0-255 units). Cuts use the AdaptiveDetector rule: a frame is a cut when its score is at least
`min_content_val` and at least `adaptive_threshold` times the mean score of the `window` frames on either side
(so fast pans and flicker, which raise every neighbour too, do not fire). Cuts closer than `min_scene_s` keep
the stronger one. Fades through black (ThresholdDetector, mean intensity below `fade_threshold`) are a cut in the
middle of the dark run plus a `fade` transition zone covering the ramp; content cuts inside a fade zone are
dropped. Cross-dissolves that never spike between two frames are not detected (no second-order detector).

The cut time is the timestamp of the first frame of the new shot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import av
import cv2
import numpy as np


DEFAULTS = {
    "adaptive_threshold": 3.0,
    "min_content_val": 15.0,
    "min_scene_s": 0.5,
    "fade_threshold": 12.0,
}


def _decode_all(container: Any, stream: Any):
    """Every frame, including the ones the decoder still buffers at EOF (the empty flush packet is decoded;
    `decode.safe_decode_frames` skips it). Corrupt packets are skipped."""
    try:
        for packet in container.demux(stream):
            try:
                for frame in packet.decode():
                    yield frame
            except GeneratorExit:
                return
            except Exception:
                continue
    except GeneratorExit:
        return
    except Exception:
        pass


def content_score(prev_hsv: np.ndarray, hsv: np.ndarray) -> float:
    """PySceneDetect ContentDetector delta: mean of |dH|, |dS|, |dV| (equal weights, no edges)."""
    d = np.abs(hsv.astype(np.int16) - prev_hsv.astype(np.int16))
    return float(d.reshape(-1, 3).mean(axis=0).mean())


def find_cuts(
    t: np.ndarray,
    scores: np.ndarray,
    luma: np.ndarray,
    adaptive_threshold: float = 3.0,
    min_content_val: float = 15.0,
    min_scene_s: float = 0.5,
    fade_threshold: float = 12.0,
    window: int = 2,
) -> tuple[list[float], list[dict[str, Any]]]:
    """Cuts and transition zones from per-frame signals (scores[i] = change from frame i-1 to i)."""
    n = int(t.size)
    if n < 2:
        return [], []
    t = np.asarray(t, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    luma = np.asarray(luma, dtype=np.float64)

    # --- fades through black
    zones: list[dict[str, Any]] = []
    fade_cuts: list[tuple[float, float]] = []
    dark = luma < fade_threshold if fade_threshold > 0 else np.zeros(n, dtype=bool)
    i = 0
    max_ramp = 2.0
    while i < n:
        if not dark[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and dark[j + 1]:
            j += 1
        if i > 0 and j < n - 1:  # a dark run touching the start or the end is not a transition
            left = i
            # The ramp: frames getting darker towards the run (fade-out) and brighter after it (fade-in)
            while left > 0 and t[i] - t[left - 1] <= max_ramp and luma[left - 1] > luma[left] + 0.5:
                left -= 1
            right = j
            while right < n - 1 and t[right + 1] - t[j] <= max_ramp and luma[right + 1] > luma[right] + 0.5:
                right += 1
            mid = (i + j) // 2 if j > i else i
            fade_cuts.append((float(t[mid]), 1e9))
            zones.append({"start": round(float(t[left]), 3), "end": round(float(t[right]), 3), "kind": "fade"})
        i = j + 1

    # --- adaptive content cuts
    content: list[tuple[float, float]] = []
    for k in range(1, n):
        s = scores[k]
        if s < min_content_val:
            continue
        lo, hi = max(1, k - window), min(n, k + window + 1)
        neigh = np.concatenate([scores[lo:k], scores[k + 1:hi]])
        avg = float(neigh.mean()) if neigh.size else 0.0
        ratio = s / avg if avg > 1e-6 else float("inf")
        if ratio >= adaptive_threshold:
            if any(z["start"] - 1e-6 <= t[k] <= z["end"] + 1e-3 for z in zones):
                continue
            content.append((float(t[k]), float(s)))

    # --- minimum scene length: keep the stronger of two close cuts (fades win)
    merged: list[tuple[float, float]] = []
    for ct, strength in sorted(content + fade_cuts):
        if ct - float(t[0]) < min_scene_s:
            continue
        if merged and ct - merged[-1][0] < min_scene_s:
            if strength > merged[-1][1]:
                merged[-1] = (ct, strength)
            continue
        merged.append((ct, strength))
    return [round(c, 3) for c, _ in merged], zones


def detect_content_cuts(
    path: Path | str,
    *,
    adaptive_threshold: float = 3.0,
    min_content_val: float = 15.0,
    min_scene_s: float = 0.5,
    fade_threshold: float = 12.0,
    width: int = 96,
    on_frame: Callable[[float, Any], None] | None = None,
    check_cancel: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Decode the whole video once and return `{cuts, transitions, frames, duration, signals}`.

    `on_frame(t, av_frame)` (optional) sees every decoded frame, e.g. to write filmstrip thumbnails from the same
    pass. Raises when the file cannot be opened or no frame decodes (callers fall back to the legacy path).
    """
    ts: list[float] = []
    scores: list[float] = []
    lumas: list[float] = []
    prev = None
    size: tuple[int, int] | None = None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = stream.time_base
        for idx, frame in enumerate(_decode_all(container, stream)):
            if frame.pts is None:
                continue
            if check_cancel is not None and idx % 500 == 0:
                check_cancel()
            t = float(frame.pts * tb)
            if size is None:
                w0, h0 = int(frame.width), int(frame.height)
                h = max(8, int(round(width * h0 / max(1, w0))) // 2 * 2)
                size = (int(width), h)
            try:
                small = frame.reformat(width=size[0], height=size[1], format="bgr24").to_ndarray()
            except Exception:
                small = cv2.resize(frame.to_ndarray(format="bgr24"), size, interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            scores.append(content_score(prev, hsv) if prev is not None else 0.0)
            lumas.append(float(small.mean()))
            ts.append(t)
            prev = hsv
            if on_frame is not None:
                on_frame(t, frame)
    if not ts:
        raise RuntimeError("No frame could be decoded")
    order = np.argsort(np.asarray(ts), kind="stable")  # B-frame pts can arrive slightly out of order
    t_arr = np.asarray(ts)[order]
    s_arr = np.asarray(scores)[order]
    l_arr = np.asarray(lumas)[order]
    cuts, zones = find_cuts(
        t_arr, s_arr, l_arr,
        adaptive_threshold=adaptive_threshold, min_content_val=min_content_val,
        min_scene_s=min_scene_s, fade_threshold=fade_threshold,
    )
    return {
        "cuts": cuts,
        "transitions": zones,
        "frames": len(ts),
        "duration": round(float(t_arr[-1] - t_arr[0]), 3) if t_arr.size else 0.0,
        "signals": {"t": t_arr, "score": s_arr, "luma": l_arr},
    }
