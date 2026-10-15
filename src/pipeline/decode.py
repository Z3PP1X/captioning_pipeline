"""Video decoding via PyAV: fast chunk sampling and exact-PTS frame extraction."""
from __future__ import annotations

from pathlib import Path
from typing import Generator

import cv2
import av
import numpy as np


def safe_decode_frames(container: av.container.Container, stream: av.video.VideoStream) -> Generator[av.VideoFrame, None, None]:
    """Safely decode frames from container stream, gracefully handling corrupted packets/frames."""
    try:
        for packet in container.demux(stream):
            if packet.dts is None and packet.pts is None:
                continue
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


def sample_chunk_frames(
    path: Path,
    start_s: float,
    end_s: float,
    sample_fps: float = 4.0,
    max_samples: int = 120,
    max_dim: int = 720,
) -> list[tuple[float, np.ndarray]]:
    """Decode frames in [start_s, end_s] sampled at sample_fps.
    
    Caps candidate frame count at max_samples to prevent unbounded memory spikes
    on long scene chunks. Downscales candidates to max_dim (default 720) to maintain
    minimal RAM consumption (~100MB per chunk) while preserving full visual fidelity
    for Tier-1 quality scoring and CLIP embeddings.
    """
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

    return sampled


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

