"""Dual-path Media Ingestion & Non-ML Video Frame Extraction Pipeline (Module A.1).

Supports:
1. Path 1 — Pre-sorted Image Folder Ingestion (`ingest_image_folder`):
   - Parallel folder scan for `.png`, `.jpg`, `.jpeg`, `.webp`
   - Header/decode validation, native dimensions (W, H), aspect ratio, sidecar `.txt` detection
   - 384px WebP thumbnail generation
   - Parallel mathematical quality scoring (`quality_math.py`) and multi-hash fingerprinting
   - O(N log N) deduplication and Best-Representative selection (`deduplicator.py`)
2. Path 2 — Video-to-Dataset Pipeline (`extract_video_pipeline`):
   - Stage 1: FFmpeg CLI subprocess hardware/filtergraph pre-filtering (`scene`, `iframe`, `interval`)
     with automatic fallback to PyAV (`av`) container demuxing, HSV adaptive scene-cut detection,
     and window-peak sharpness/motion sub-sampling (`pick_window_peak`).
   - Stage 2: Mathematical quality scoring Q in [0, 1] and hard gate filtering.
   - Stage 3: O(N log N) BK-Tree / Multi-Index pHash+dHash+HSV clustering and representative selection.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import cv2
import numpy as np

from backend.app.models.domain import (
    ClusterRecord,
    ExtractionConfig,
    FrameExtractType,
    MediaItemRecord,
    QualityWeights,
    VideoMetadata,
)
from backend.app.modules.ingestion.deduplicator import (
    cluster_media_items,
    compute_fingerprint,
)
from backend.app.modules.ingestion.quality_math import compute_quality_score

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_SHOWINFO_PTS_TIME_RE = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")


def _has_cli_binary(name: str) -> bool:
    """Return True if `name` is executable on PATH."""
    return shutil.which(name) is not None


def generate_webp_thumbnail(
    img_bgr: np.ndarray,
    out_path: Path | str,
    max_dim: int = 384,
    quality: int = 82,
) -> Path:
    """Resize BGR image so longest edge <= `max_dim` and write WebP thumbnail."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    h, w = img_bgr.shape[:2]
    if max_dim > 0 and max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        new_w = max(16, int(round(w * scale)))
        new_h = max(16, int(round(h * scale)))
        thumb = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        thumb = img_bgr
    cv2.imwrite(str(out), thumb, [cv2.IMWRITE_WEBP_QUALITY, int(quality)])
    return out


def _ingest_single_image_file(
    idx: int,
    img_path: Path,
    thumbnails_dir: Path | None,
    thumbnail_dim: int,
    weights: QualityWeights | None,
) -> MediaItemRecord | None:
    """Worker function to decode, validate, thumbnail, score, and fingerprint a single image file."""
    try:
        raw_bytes = np.fromfile(str(img_path), dtype=np.uint8)
        if raw_bytes.size == 0:
            return None
        img_bgr = cv2.imdecode(raw_bytes, cv2.IMREAD_COLOR)
        if img_bgr is None or img_bgr.size == 0:
            return None
    except Exception:
        return None

    h, w = img_bgr.shape[:2]
    if h < 8 or w < 8:
        return None
    aspect_ratio = round(float(w) / float(h), 4)

    # Check for existing sidecar .txt caption
    sidecar_txt = img_path.with_suffix(".txt")
    existing_caption: str | None = None
    if sidecar_txt.is_file():
        try:
            text = sidecar_txt.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                existing_caption = text
        except Exception:
            pass

    thumb_path_str: str | None = None
    if thumbnails_dir is not None:
        thumb_file = thumbnails_dir / f"item_{idx:05d}_{img_path.stem}.webp"
        try:
            generate_webp_thumbnail(img_bgr, thumb_file, max_dim=thumbnail_dim)
            thumb_path_str = str(thumb_file)
        except Exception:
            thumb_path_str = None

    quality = compute_quality_score(img_bgr, weights=weights)
    fp = compute_fingerprint(img_bgr)

    return MediaItemRecord(
        item_id=idx,
        source_filename=img_path.name,
        source_path=str(img_path),
        extracted_path=str(img_path),
        thumbnail_path=thumb_path_str,
        timestamp_s=None,
        scene_index=0,
        frame_extract_type="image",
        width=w,
        height=h,
        aspect_ratio=aspect_ratio,
        phash=fp.phash_hex,
        dhash=fp.dhash_hex,
        hsv_hist=fp.hsv_hist,
        quality=quality,
        curation_state=quality.curation_state,
        existing_caption=existing_caption,
    )


def ingest_image_folder(
    folder_path: Path | str,
    *,
    thumbnails_dir: Path | str | None = None,
    thumbnail_dim: int = 384,
    recursive: bool = True,
    max_workers: int | None = None,
    weights: QualityWeights | None = None,
    dedup_hamming_threshold: int = 6,
    dedup_dhash_threshold: int = 8,
    dedup_hsv_min_cosine: float = 0.85,
) -> tuple[list[MediaItemRecord], list[ClusterRecord], dict[str, Any]]:
    """Path 1: Ingest a folder of pre-sorted images, score quality, and deduplicate in O(N log N)."""
    root = Path(folder_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Image folder does not exist: {root}")

    iterator = root.rglob("*") if recursive else root.glob("*")
    image_files = sorted(
        p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )
    if not image_files:
        return [], [], {
            "source_type": "image_folder",
            "total_scanned": 0,
            "valid_images": 0,
            "auto_rejected": 0,
            "clusters_count": 0,
            "duplicates_collapsed": 0,
        }

    thumb_dir_path = Path(thumbnails_dir) if thumbnails_dir is not None else None
    workers = max_workers or min(8, max(2, (os.cpu_count() or 4)))

    items: list[MediaItemRecord] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _ingest_single_image_file,
                idx,
                img_path,
                thumb_dir_path,
                thumbnail_dim,
                weights,
            )
            for idx, img_path in enumerate(image_files, start=1)
        ]
        for fut in futures:
            rec = fut.result()
            if rec is not None:
                items.append(rec)

    # Re-index item_ids sequentially 1..N
    for new_id, item in enumerate(items, start=1):
        item.item_id = new_id

    clusters, assignments, cluster_stats = cluster_media_items(
        items,
        phash_threshold=dedup_hamming_threshold,
        dhash_threshold=dedup_dhash_threshold,
        hsv_min_cosine=dedup_hsv_min_cosine,
        temporal_window_pass=False,
    )
    for item, assign in zip(items, assignments):
        item.cluster_id = assign.cluster_id
        item.is_cluster_representative = assign.is_cluster_representative
        item.curation_state = assign.curation_state

    auto_rejected_count = sum(1 for item in items if not item.quality.passed_gates)
    stats = {
        "source_type": "image_folder",
        "total_scanned": len(image_files),
        "valid_images": len(items),
        "auto_rejected": auto_rejected_count,
        **cluster_stats,
    }
    return items, clusters, stats


def probe_video_metadata(video_path: Path | str) -> VideoMetadata:
    """Extract video container & stream metadata via `ffprobe` CLI with PyAV fallback."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    if _has_cli_binary("ffprobe"):
        try:
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
            data = json.loads(proc.stdout)
            vstreams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
            if vstreams:
                vs = vstreams[0]
                fmt = data.get("format", {})
                dur = float(vs.get("duration") or fmt.get("duration") or 0.0)
                r_fps = str(vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "30/1")
                if "/" in r_fps:
                    num, den = r_fps.split("/", 1)
                    fps = float(num) / max(1.0, float(den))
                else:
                    fps = float(r_fps or 30.0)
                width = int(vs.get("width") or 0)
                height = int(vs.get("height") or 0)
                codec = str(vs.get("codec_name") or "unknown")
                pix_fmt = str(vs.get("pix_fmt") or "unknown")
                nb_frames = int(vs.get("nb_frames") or int(round(dur * fps)))
                return VideoMetadata(
                    path=str(path),
                    duration_s=dur,
                    fps=fps,
                    width=width,
                    height=height,
                    codec=codec,
                    pix_fmt=pix_fmt,
                    frame_count=nb_frames,
                )
        except Exception:
            pass

    # Fallback to PyAV or OpenCV VideoCapture container inspection
    try:
        import av

        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            fps = float(stream.average_rate or stream.guessed_rate or 30.0)
            if stream.duration is not None and stream.time_base is not None:
                dur = float(stream.duration * stream.time_base)
            elif container.duration is not None:
                dur = float(container.duration) / 1_000_000.0
            else:
                dur = 0.0
            width = int(stream.width or 0)
            height = int(stream.height or 0)
            codec = str(stream.codec_context.name if stream.codec_context else "unknown")
            pix_fmt = str(stream.pix_fmt or "unknown")
            frames = int(stream.frames or int(round(dur * fps)))
            return VideoMetadata(
                path=str(path),
                duration_s=dur,
                fps=fps,
                width=width,
                height=height,
                codec=codec,
                pix_fmt=pix_fmt,
                frame_count=frames,
            )
    except ImportError:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open video container with OpenCV: {path}")
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
            if fps <= 0:
                fps = 30.0
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            dur = round(frames / fps, 3) if frames > 0 else 0.0
            return VideoMetadata(
                path=str(path),
                duration_s=dur,
                fps=fps,
                width=width,
                height=height,
                codec="opencv_cap",
                pix_fmt="bgr24",
                frame_count=frames,
            )
        finally:
            cap.release()


def pick_window_peak(
    members: list[dict[str, Any]],
    motion_weight: float = 0.5,
) -> dict[str, Any]:
    """Select the sharpest, stillest frame within a sampling window.

    Maximizes: (sharp / max_sharp) - motion_weight * (motion / max(max_motion, 2.0)).
    """
    if not members:
        raise ValueError("Window members list cannot be empty")
    if len(members) == 1:
        return members[0]

    max_sharp = max(1e-6, max(float(m["sharp"]) for m in members))
    motions = [float(m["motion"]) for m in members if m.get("motion") is not None]
    max_motion = max(2.0, max(motions) if motions else 0.0)

    best = members[0]
    best_score = -float("inf")
    for m in members:
        mot = float(m["motion"]) if m.get("motion") is not None else max_motion
        score = (float(m["sharp"]) / max_sharp) - motion_weight * (mot / max_motion)
        if score > best_score:
            best = m
            best_score = score
    return best


def _extract_via_ffmpeg_cli(
    video_path: Path,
    frames_dir: Path,
    config: ExtractionConfig,
) -> list[tuple[float, Path, FrameExtractType]]:
    """Run FFmpeg CLI subprocess for high-speed scene or keyframe extraction."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str(frames_dir / "ff_frame_%05d.jpg")

    cmd: list[str] = ["ffmpeg", "-nostdin", "-y", "-hwaccel", "auto"]
    extract_type: FrameExtractType = "scene_cut"

    scale_filter = ""
    if config.max_extract_dim > 0:
        d = int(config.max_extract_dim)
        scale_filter = f",scale='min({d},iw)':'min({d},ih)':force_original_aspect_ratio=decrease"

    if config.mode == "scene":
        thr = float(config.scene_threshold)
        vf = f"select='gt(scene,{thr:.4f})'{scale_filter},showinfo"
        cmd.extend(["-i", str(video_path), "-vf", vf, "-vsync", "vfr", "-q:v", "2", out_pattern])
        extract_type = "scene_cut"
    elif config.mode == "iframe":
        vf = f"showinfo{scale_filter}" if not scale_filter else f"{scale_filter.lstrip(',')},showinfo"
        cmd.extend([
            "-skip_frame",
            "nokey",
            "-i",
            str(video_path),
            "-vf",
            vf,
            "-vsync",
            "vfr",
            "-q:v",
            "2",
            out_pattern,
        ])
        extract_type = "iframe"
    else:
        fps_val = 1.0 / max(0.05, float(config.interval_s))
        vf = f"fps={fps_val:.4f}{scale_filter},showinfo"
        cmd.extend(["-i", str(video_path), "-vf", vf, "-vsync", "vfr", "-q:v", "2", out_pattern])
        extract_type = "interval_peak"

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg exited with code {proc.returncode}: {proc.stderr[-400:]}")

    timestamps: list[float] = [
        round(float(m.group(1)), 3) for m in _SHOWINFO_PTS_TIME_RE.finditer(proc.stderr)
    ]
    extracted_files = sorted(frames_dir.glob("ff_frame_*.jpg"))
    if not extracted_files:
        return []

    results: list[tuple[float, Path, FrameExtractType]] = []
    for idx, fpath in enumerate(extracted_files):
        if idx >= config.target_candidates:
            try:
                fpath.unlink(missing_ok=True)
            except Exception:
                pass
            continue
        ts = timestamps[idx] if idx < len(timestamps) else round(idx * config.interval_s, 3)
        results.append((ts, fpath, extract_type))

    return results


def _extract_via_opencv_cap(
    video_path: Path,
    frames_dir: Path,
    config: ExtractionConfig,
) -> list[tuple[float, Path, FrameExtractType]]:
    """OpenCV VideoCapture extraction fallback when neither FFmpeg CLI nor PyAV is installed."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[float, Path, FrameExtractType]] = []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video with OpenCV VideoCapture: {video_path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        if fps <= 0:
            fps = 30.0
        prev_hsv: np.ndarray | None = None
        prev_gray_small: np.ndarray | None = None
        last_kept_t = -1e9
        window_buf: list[dict[str, Any]] = []
        window_idx = -1
        window_s = max(0.05, float(config.interval_s))
        oversample = max(1, int(config.window_peak))
        sub_interval = window_s / float(oversample)
        next_sample_t = 0.0
        frame_counter = 0
        frame_idx = 0

        def _save_candidate(ts: float, img_bgr: np.ndarray, ftype: FrameExtractType) -> None:
            nonlocal frame_counter
            if len(results) >= config.target_candidates:
                return
            frame_counter += 1
            out_file = frames_dir / f"cv_frame_{frame_counter:05d}.jpg"
            cv2.imwrite(str(out_file), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            results.append((round(ts, 3), out_file, ftype))

        def _flush_window() -> None:
            if not window_buf:
                return
            best = pick_window_peak(window_buf, motion_weight=config.motion_weight)
            _save_candidate(best["t"], best["img"], "interval_peak")
            window_buf.clear()

        while len(results) < config.target_candidates:
            ok, img_bgr = cap.read()
            if not ok or img_bgr is None or img_bgr.size == 0:
                break
            t = frame_idx / fps
            frame_idx += 1

            h0, w0 = img_bgr.shape[:2]
            if config.max_extract_dim > 0 and max(w0, h0) > config.max_extract_dim:
                scale = config.max_extract_dim / float(max(w0, h0))
                nw = max(16, int(round(w0 * scale)))
                nh = max(16, int(round(h0 * scale)))
                img_bgr = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)

            if config.mode == "iframe":
                if (t - last_kept_t) >= max(0.25, float(config.interval_s)):
                    _save_candidate(t, img_bgr, "iframe")
                    last_kept_t = t
            elif config.mode == "scene":
                small = cv2.resize(img_bgr, (96, 54), interpolation=cv2.INTER_AREA)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                if prev_hsv is None:
                    _save_candidate(t, img_bgr, "scene_cut")
                    last_kept_t = t
                else:
                    delta = float(np.abs(hsv.astype(np.int16) - prev_hsv.astype(np.int16)).mean())
                    hsv_thr = max(5.0, float(config.scene_threshold) * 100.0)
                    if delta >= hsv_thr and (t - last_kept_t) >= 0.40:
                        _save_candidate(t, img_bgr, "scene_cut")
                        last_kept_t = t
                prev_hsv = hsv
            else:
                if t >= next_sample_t:
                    cur_win_idx = int(t / window_s)
                    if cur_win_idx != window_idx:
                        _flush_window()
                        window_idx = cur_win_idx
                    gray_small = cv2.resize(
                        cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY),
                        (128, 72),
                        interpolation=cv2.INTER_AREA,
                    )
                    sharp = float(cv2.Laplacian(gray_small, cv2.CV_32F).var())
                    motion = (
                        float(np.abs(gray_small.astype(np.float32) - prev_gray_small).mean())
                        if prev_gray_small is not None
                        else 0.0
                    )
                    prev_gray_small = gray_small.astype(np.float32)
                    window_buf.append(
                        {"t": round(t, 3), "img": img_bgr, "sharp": sharp, "motion": motion}
                    )
                    next_sample_t = t + sub_interval

        if config.mode == "interval":
            _flush_window()
    finally:
        cap.release()

    return results


def _extract_via_pyav(
    video_path: Path,
    frames_dir: Path,
    config: ExtractionConfig,
) -> list[tuple[float, Path, FrameExtractType]]:
    """Pure-Python PyAV extraction engine supporting scene cuts, I-frames, and window-peak interval sampling."""
    try:
        import av
    except ImportError:
        return _extract_via_opencv_cap(video_path, frames_dir, config)

    frames_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[float, Path, FrameExtractType]] = []

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = stream.time_base

        if config.mode == "iframe":
            stream.codec_context.skip_frame = "NONKEY"

        prev_hsv: np.ndarray | None = None
        prev_gray_small: np.ndarray | None = None
        last_kept_t = -1e9
        window_buf: list[dict[str, Any]] = []
        window_idx = -1
        window_s = max(0.05, float(config.interval_s))
        oversample = max(1, int(config.window_peak))
        sub_interval = window_s / float(oversample)
        next_sample_t = 0.0
        frame_counter = 0

        def _save_candidate(ts: float, img_bgr: np.ndarray, ftype: FrameExtractType) -> None:
            nonlocal frame_counter
            if len(results) >= config.target_candidates:
                return
            frame_counter += 1
            out_file = frames_dir / f"pyav_frame_{frame_counter:05d}.jpg"
            cv2.imwrite(str(out_file), img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            results.append((round(ts, 3), out_file, ftype))

        def _flush_window() -> None:
            if not window_buf:
                return
            best = pick_window_peak(window_buf, motion_weight=config.motion_weight)
            _save_candidate(best["t"], best["img"], "interval_peak")
            window_buf.clear()

        for packet in container.demux(stream):
            try:
                decoded_frames = packet.decode()
            except Exception:
                continue
            for frame in decoded_frames:
                if frame.pts is None:
                    continue
                t = float(frame.pts * tb)
                if len(results) >= config.target_candidates:
                    break

                w0, h0 = int(frame.width), int(frame.height)
                if config.max_extract_dim > 0 and max(w0, h0) > config.max_extract_dim:
                    scale = config.max_extract_dim / float(max(w0, h0))
                    nw = max(16, int(round(w0 * scale)))
                    nh = max(16, int(round(h0 * scale)))
                    img_bgr = frame.reformat(width=nw, height=nh, format="bgr24").to_ndarray()
                else:
                    img_bgr = frame.to_ndarray(format="bgr24")

                if config.mode == "iframe":
                    if frame.key_frame or (t - last_kept_t) >= 0.25:
                        _save_candidate(t, img_bgr, "iframe")
                        last_kept_t = t
                elif config.mode == "scene":
                    small = cv2.resize(img_bgr, (96, 54), interpolation=cv2.INTER_AREA)
                    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                    if prev_hsv is None:
                        _save_candidate(t, img_bgr, "scene_cut")
                        last_kept_t = t
                    else:
                        delta = float(
                            np.abs(hsv.astype(np.int16) - prev_hsv.astype(np.int16)).mean()
                        )
                        # Scale scene_threshold (default 0.15 -> ~15.0 HSV delta)
                        hsv_thr = max(5.0, float(config.scene_threshold) * 100.0)
                        if delta >= hsv_thr and (t - last_kept_t) >= 0.40:
                            _save_candidate(t, img_bgr, "scene_cut")
                            last_kept_t = t
                    prev_hsv = hsv
                else:
                    # Interval + Window-Peak sampling
                    if t >= next_sample_t:
                        cur_win_idx = int(t / window_s)
                        if cur_win_idx != window_idx:
                            _flush_window()
                            window_idx = cur_win_idx
                        gray_small = cv2.resize(
                            cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY),
                            (128, 72),
                            interpolation=cv2.INTER_AREA,
                        )
                        sharp = float(cv2.Laplacian(gray_small, cv2.CV_32F).var())
                        motion = (
                            float(np.abs(gray_small.astype(np.float32) - prev_gray_small).mean())
                            if prev_gray_small is not None
                            else 0.0
                        )
                        prev_gray_small = gray_small.astype(np.float32)
                        window_buf.append(
                            {"t": round(t, 3), "img": img_bgr, "sharp": sharp, "motion": motion}
                        )
                        next_sample_t = t + sub_interval

        if config.mode == "interval":
            _flush_window()

    return results


def extract_video_pipeline(
    video_path: Path | str,
    output_frames_dir: Path | str,
    *,
    thumbnails_dir: Path | str | None = None,
    config: ExtractionConfig | None = None,
    weights: QualityWeights | None = None,
    max_workers: int | None = None,
) -> tuple[list[MediaItemRecord], list[ClusterRecord], VideoMetadata, dict[str, Any]]:
    """Execute the 3-stage Non-ML Video-to-Dataset Extraction, Scoring, and Clustering Pipeline.

    Stage 1: FFmpeg subprocess scene/keyframe filtergraph (or PyAV container demux + window-peak).
    Stage 2: Pure mathematical quality scoring Q in [0, 1] and 384px WebP thumbnail creation.
    Stage 3: O(N log N) BK-Tree / Multi-Index pHash+dHash+HSV deduplication & representative selection.
    """
    cfg = config or ExtractionConfig()
    vpath = Path(video_path)
    frames_dir = Path(output_frames_dir)
    thumb_dir = Path(thumbnails_dir) if thumbnails_dir is not None else (frames_dir / "thumbnails")

    video_meta = probe_video_metadata(vpath)

    # Stage 1: Frame Extraction
    used_backend = "pyav"
    raw_candidates: list[tuple[float, Path, FrameExtractType]] = []

    use_ffmpeg = (
        not cfg.prefer_pyav
        and _has_cli_binary("ffmpeg")
        and not (cfg.mode == "interval" and cfg.window_peak >= 2)
    )
    if use_ffmpeg:
        try:
            raw_candidates = _extract_via_ffmpeg_cli(vpath, frames_dir, cfg)
            used_backend = "ffmpeg_cli"
        except Exception:
            raw_candidates = []

    # Fallback to PyAV if FFmpeg CLI was skipped, failed, or scene threshold produced 0 cuts
    if not raw_candidates:
        fallback_cfg = cfg
        if cfg.mode == "scene":
            # If scene filter yielded 0 frames on a short/static clip, sample via interval window-peak
            fallback_cfg = cfg.model_copy(update={"mode": "interval"})
        raw_candidates = _extract_via_pyav(vpath, frames_dir, fallback_cfg)
        used_backend = "pyav"

    # Stage 2: Parallel Mathematical Quality Scoring & Multi-Hash Fingerprinting
    def _score_candidate(args: tuple[int, float, Path, FrameExtractType]) -> MediaItemRecord | None:
        idx, ts, fpath, ftype = args
        img_bgr = cv2.imread(str(fpath), cv2.IMREAD_COLOR)
        if img_bgr is None or img_bgr.size == 0:
            return None
        h, w = img_bgr.shape[:2]
        aspect = round(float(w) / float(max(1, h)), 4)

        t_file = thumb_dir / f"thumb_{idx:05d}.webp"
        generate_webp_thumbnail(img_bgr, t_file, max_dim=cfg.thumbnail_dim)

        q_res = compute_quality_score(img_bgr, weights=weights)
        fp = compute_fingerprint(img_bgr)
        return MediaItemRecord(
            item_id=idx,
            source_filename=fpath.name,
            source_path=str(vpath),
            extracted_path=str(fpath),
            thumbnail_path=str(t_file),
            timestamp_s=ts,
            scene_index=0,
            frame_extract_type=ftype,
            width=w,
            height=h,
            aspect_ratio=aspect,
            phash=fp.phash_hex,
            dhash=fp.dhash_hex,
            hsv_hist=fp.hsv_hist,
            quality=q_res,
            curation_state=q_res.curation_state,
        )

    workers = max_workers or min(8, max(2, (os.cpu_count() or 4)))
    scored_items: list[MediaItemRecord] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [
            (idx, ts, fpath, ftype)
            for idx, (ts, fpath, ftype) in enumerate(raw_candidates, start=1)
        ]
        for rec in pool.map(_score_candidate, tasks):
            if rec is not None:
                scored_items.append(rec)

    # Stage 3: O(N log N) Deduplication & Best-Representative Selection
    clusters, assignments, cluster_stats = cluster_media_items(
        scored_items,
        phash_threshold=cfg.dedup_hamming_threshold,
        dhash_threshold=cfg.dedup_dhash_threshold,
        hsv_min_cosine=cfg.dedup_hsv_min_cosine,
        temporal_window_pass=True,
    )
    for item, assign in zip(scored_items, assignments):
        item.cluster_id = assign.cluster_id
        item.is_cluster_representative = assign.is_cluster_representative
        item.curation_state = assign.curation_state

    stats = {
        "source_type": "video",
        "extraction_backend": used_backend,
        "extraction_mode": cfg.mode,
        "candidates_extracted": len(scored_items),
        "auto_rejected": sum(1 for i in scored_items if not i.quality.passed_gates),
        **cluster_stats,
    }
    return scored_items, clusters, video_meta, stats
