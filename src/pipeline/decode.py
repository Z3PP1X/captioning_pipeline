"""Video decoding via PyAV: fast chunk sampling and exact-PTS frame extraction."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Generator

import cv2
import av
import numpy as np


def safe_decode_frames(container: av.container.Container, stream: av.video.VideoStream) -> Generator[av.VideoFrame, None, None]:
    """Safely decode frames from container stream, gracefully handling corrupted packets/frames."""
    try:
        for packet in container.demux(stream):
            # The last packet has neither pts nor dts: it is the flush packet. Decoding it drains the frames the
            # decoder still buffers (B-frame reordering); skipping it lost the final frames of every video.
            try:
                for frame in packet.decode():
                    yield frame
            except GeneratorExit:
                return
            except (av.error.FFmpegError, Exception):
                continue
    except GeneratorExit:
        return
    except (av.error.FFmpegError, Exception):
        pass


def _frame_to_bgr(frame: av.VideoFrame, max_dim: int) -> np.ndarray:
    """Downscale in swscale (no full-resolution array for 4K sources), falling back to numpy + cv2."""
    w, h = int(frame.width), int(frame.height)
    if max_dim > 0 and max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_w, new_h = max(16, int(round(w * scale))), max(16, int(round(h * scale)))
        try:
            return frame.reformat(width=new_w, height=new_h, format="bgr24", interpolation="AREA").to_ndarray()
        except Exception:
            img = frame.to_ndarray(format="bgr24")
            return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return frame.to_ndarray(format="bgr24")


def _motion_thumb(frame: av.VideoFrame) -> np.ndarray | None:
    try:
        return frame.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.float32)
    except Exception:
        return None


def pick_window_peak(members: list[dict[str, Any]], motion_weight: float = 0.5) -> dict[str, Any]:
    """Pick the sharpest / stillest sub-sample of one window.

    Each member has `sharp` (Laplacian variance) and `motion` (mean |frame difference| to the previous decoded
    frame, grey levels; None if unknown). Both are normalised within the window, so the absolute scale of a
    shot does not matter: score = sharp / max_sharp - motion_weight * motion / max(max_motion, 2).
    The 2-grey-level floor keeps sensor noise in a static shot from being amplified into "motion".
    """
    if len(members) == 1:
        return members[0]
    max_sharp = max(1e-6, max(float(m["sharp"]) for m in members))
    motions = [float(m["motion"]) for m in members if m.get("motion") is not None]
    max_motion = max(2.0, max(motions) if motions else 0.0)
    best, best_score = members[0], -float("inf")
    for m in members:
        mot = float(m["motion"]) if m.get("motion") is not None else max_motion
        score = float(m["sharp"]) / max_sharp - motion_weight * (mot / max_motion)
        if score > best_score:
            best, best_score = m, score
    return best


def sample_chunk_frames(
    path: Path,
    start_s: float,
    end_s: float,
    sample_fps: float = 4.0,
    max_samples: int = 120,
    max_dim: int = 720,
    window_peak: int = 0,
    motion_weight: float = 0.5,
    stats_out: dict[str, Any] | None = None,
) -> list[tuple[float, np.ndarray]]:
    """Decode frames in [start_s, end_s] sampled at sample_fps.

    Caps candidate frame count at max_samples to prevent unbounded memory spikes
    on long scene chunks. Downscales candidates to max_dim (default 720) to maintain
    minimal RAM consumption (~100MB per chunk) while preserving full visual fidelity
    for Tier-1 quality scoring and CLIP embeddings.

    `window_peak >= 2` switches to window-peak sampling (review §4 Stage B): the timeline is cut into windows
    of one sampling interval, `window_peak` sub-samples are taken per window, and only the sharpest/stillest
    one is kept (`pick_window_peak`). The number of returned samples is unchanged; only one window's frames are
    held in memory. `stats_out`, if given, receives `{mode, windows, subsamples, motion: {t: energy}}`.
    """
    if window_peak and window_peak >= 2:
        return _sample_chunk_frames_window_peak(
            path, start_s, end_s, sample_fps, max_samples, max_dim, int(window_peak), motion_weight, stats_out
        )
    sampled: list[tuple[float, np.ndarray]] = []
    chunk_dur = end_s - start_s
    if chunk_dur <= 0:
        return sampled

    # Adapt sample_fps so total candidate frames don't exceed max_samples
    if chunk_dur * sample_fps > max_samples:
        sample_interval = chunk_dur / max_samples
    else:
        sample_interval = 1.0 / max(0.2, sample_fps)

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        tb = stream.time_base

        # Seek to keyframe just before start_s
        seek_pts = int(max(0.0, start_s - 0.2) / tb)
        try:
            container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
        except Exception:
            pass

        next_sample_t = start_s

        for frame in safe_decode_frames(container, stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * tb)
            if t < start_s:
                continue
            if t > end_s:
                break

            if t >= next_sample_t:
                img_bgr = frame.to_ndarray(format="bgr24")
                if max_dim > 0:
                    h, w = img_bgr.shape[:2]
                    if max(h, w) > max_dim:
                        scale = max_dim / max(h, w)
                        new_w = max(16, int(round(w * scale)))
                        new_h = max(16, int(round(h * scale)))
                        img_bgr = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
                sampled.append((round(t, 3), img_bgr))
                next_sample_t = t + sample_interval

    if stats_out is not None:
        stats_out.update({"mode": "uniform", "windows": len(sampled), "subsamples": len(sampled)})
    return sampled


def _sample_chunk_frames_window_peak(
    path: Path,
    start_s: float,
    end_s: float,
    sample_fps: float,
    max_samples: int,
    max_dim: int,
    oversample: int,
    motion_weight: float,
    stats_out: dict[str, Any] | None,
) -> list[tuple[float, np.ndarray]]:
    sampled: list[tuple[float, np.ndarray]] = []
    motion_by_t: dict[float, float] = {}
    chunk_dur = end_s - start_s
    if chunk_dur <= 0:
        return sampled

    # Same window count as the uniform path, so the downstream budget does not change
    if chunk_dur * sample_fps > max_samples:
        window_s = chunk_dur / max_samples
    else:
        window_s = 1.0 / max(0.2, sample_fps)
    sub_interval = window_s / oversample

    window: list[dict[str, Any]] = []
    window_idx = -1
    windows = 0
    subsamples = 0

    def flush() -> None:
        nonlocal windows
        if not window:
            return
        best = pick_window_peak(window, motion_weight)
        sampled.append((best["t"], best["img"]))
        if best.get("motion") is not None:
            motion_by_t[best["t"]] = round(float(best["motion"]), 3)
        windows += 1
        window.clear()

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        tb = stream.time_base
        seek_pts = int(max(0.0, start_s - 0.2) / tb)
        try:
            container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
        except Exception:
            pass

        next_sub_t = start_s
        prev_frame: av.VideoFrame | None = None
        for frame in safe_decode_frames(container, stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * tb)
            if t < start_s:
                prev_frame = frame
                continue
            if t > end_s:
                break
            if t >= next_sub_t:
                idx = min(int((t - start_s) / window_s), max(0, max_samples - 1))
                if idx != window_idx:
                    flush()
                    window_idx = idx
                img_bgr = _frame_to_bgr(frame, max_dim)
                gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
                sharp = float(cv2.Laplacian(gray, cv2.CV_32F).var())
                motion = None
                if prev_frame is not None:
                    cur_small, prev_small = _motion_thumb(frame), _motion_thumb(prev_frame)
                    if cur_small is not None and prev_small is not None:
                        motion = float(np.abs(cur_small - prev_small).mean())
                window.append({"t": round(t, 3), "img": img_bgr, "sharp": sharp, "motion": motion})
                subsamples += 1
                next_sub_t = t + sub_interval
            prev_frame = frame
        flush()

    if stats_out is not None:
        stats_out.update({
            "mode": "window_peak",
            "windows": windows,
            "subsamples": subsamples,
            "window_s": round(window_s, 3),
            "motion": motion_by_t,
        })
    return sampled


def sample_frames_at(
    path: Path,
    timestamps: list[float],
    max_dim: int = 512,
) -> list[tuple[float, np.ndarray]]:
    """Decode one frame at (or just after) each timestamp, seeking between them.

    Unlike `sample_chunk_frames` this does not decode the whole range, so probing a handful of frames per
    scene of a long video is cheap. Frames are downscaled immediately, so memory stays small even for 4K
    sources. Timestamps that cannot be decoded are skipped.
    """
    out: list[tuple[float, np.ndarray]] = []
    if not timestamps:
        return out

    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            tb = stream.time_base
            for target in sorted(timestamps):
                try:
                    container.seek(int(max(0.0, target - 0.5) / tb), stream=stream, backward=True, any_frame=False)
                except Exception:
                    pass
                for frame in safe_decode_frames(container, stream):
                    if frame.pts is None:
                        continue
                    t = float(frame.pts * tb)
                    if t < target - 0.05:
                        continue
                    img_bgr = frame.to_ndarray(format="bgr24")
                    h, w = img_bgr.shape[:2]
                    if max_dim > 0 and max(h, w) > max_dim:
                        scale = max_dim / max(h, w)
                        img_bgr = cv2.resize(
                            img_bgr,
                            (max(16, int(round(w * scale))), max(16, int(round(h * scale)))),
                            interpolation=cv2.INTER_AREA,
                        )
                    out.append((round(t, 3), img_bgr))
                    break
    except Exception:
        pass
    return out


def decode_exact_frames(
    path: Path,
    timestamps: list[float],
    on_frame: Any = None,
) -> dict[float, np.ndarray]:
    """Fast cluster-seek and exact decode for multiple target timestamps at full native resolution.
    
    If on_frame callback is provided, on_frame(target_t, img_bgr) is called for each decoded frame,
    and the frame is not accumulated in the return dictionary (keeping memory minimal).
    """
    if not timestamps:
        return {}

    sorted_ts = sorted(set(timestamps))

    # Group timestamps into clusters separated by > 2.5s so we seek over large gaps
    clusters: list[list[float]] = []
    for t in sorted_ts:
        if not clusters or (t - clusters[-1][-1]) > 2.5:
            clusters.append([t])
        else:
            clusters[-1].append(t)

    best_matches: dict[float, tuple[float, np.ndarray | None]] = {tgt: (float("inf"), None) for tgt in sorted_ts}

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        tb = stream.time_base

        for cluster in clusters:
            c_start = cluster[0]
            c_end = cluster[-1]
            seek_pts = int(max(0.0, c_start - 0.5) / tb)
            try:
                container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
            except Exception:
                pass

            for frame in safe_decode_frames(container, stream):
                if frame.pts is None:
                    continue
                t = float(frame.pts * tb)

                if t < c_start - 0.25:
                    continue

                img_bgr: np.ndarray | None = None
                for tgt in cluster:
                    diff = abs(t - tgt)
                    if diff <= 0.25 and diff < best_matches[tgt][0]:
                        if img_bgr is None:
                            img_bgr = frame.to_ndarray(format="bgr24")
                        best_matches[tgt] = (diff, img_bgr)

                if t > c_end + 0.15:
                    break

    results: dict[float, np.ndarray] = {}
    for tgt, (diff, img) in best_matches.items():
        if img is None:
            img = decode_exact_frame(path, tgt)
        if img is not None:
            if on_frame is not None and callable(on_frame):
                on_frame(tgt, img)
            else:
                results[tgt] = img

    return results


def ensure_native_frame(db: Any, frame_id: int) -> bool:
    """Ensure a frame has been decoded at full native resolution and saved to full_path.
    If frame.native == 1, already native.
    If frame.native == 0, re-decodes from the source video, writes full_path, and updates native=1."""
    row = db.one(
        """
        SELECT f.id, f.t, f.full_path, f.native, v.path as video_path
        FROM frames f
        JOIN videos v ON f.video_id = v.id
        WHERE f.id = ?
        """,
        (frame_id,),
    )
    if not row:
        return False
    if row.get("native") == 1:
        return True

    vpath = Path(row["video_path"])
    if not vpath.is_file():
        return False

    img = decode_exact_frame(vpath, float(row["t"]))
    if img is None:
        return False

    out_path = Path(row["full_path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    with db.write() as conn:
        conn.execute("UPDATE frames SET native=1 WHERE id=?", (frame_id,))
    return True


def ensure_native_frames(db: Any, frame_ids: list[int]) -> int:
    """Batch re-decodes native frames for any frames in frame_ids that currently have native=0."""
    if not frame_ids:
        return 0
    placeholders = ",".join("?" for _ in frame_ids)
    rows = db.all(
        f"""
        SELECT f.id, f.t, f.full_path, f.native, f.video_id, v.path as video_path
        FROM frames f
        JOIN videos v ON f.video_id = v.id
        WHERE f.id IN ({placeholders}) AND f.native = 0
        """,
        tuple(frame_ids),
    )
    if not rows:
        return 0

    by_video: dict[str, list[dict]] = {}
    for r in rows:
        by_video.setdefault(r["video_path"], []).append(r)

    updated_ids: list[int] = []
    for vpath_str, vrows in by_video.items():
        vpath = Path(vpath_str)
        if not vpath.is_file():
            continue
        ts_map = {float(r["t"]): r for r in vrows}

        def write_frame(t: float, img: np.ndarray):
            r = ts_map.get(t)
            if r:
                out_p = Path(r["full_path"])
                out_p.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(out_p), img)
                updated_ids.append(r["id"])

        decode_exact_frames(vpath, list(ts_map.keys()), on_frame=write_frame)

    if updated_ids:
        with db.write() as conn:
            up_ph = ",".join("?" for _ in updated_ids)
            conn.execute(f"UPDATE frames SET native=1 WHERE id IN ({up_ph})", tuple(updated_ids))
    return len(updated_ids)


def decode_exact_frame(path: Path, target_t: float) -> np.ndarray | None:
    """Seek and decode the frame at or closest to exact target_t."""
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            tb = stream.time_base

            seek_pts = int(max(0.0, target_t - 0.5) / tb)
            try:
                container.seek(seek_pts, stream=stream, backward=True, any_frame=False)
            except Exception:
                pass

            best_frame: np.ndarray | None = None
            min_diff = float("inf")

            for frame in safe_decode_frames(container, stream):
                if frame.pts is None:
                    continue
                t = float(frame.pts * tb)
                diff = abs(t - target_t)
                if diff <= 0.35 and diff < min_diff:
                    min_diff = diff
                    best_frame = frame.to_ndarray(format="bgr24")

                if t > target_t + 0.15:
                    break

            return best_frame
    except Exception:
        return None

