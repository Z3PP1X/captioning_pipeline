"""Video indexing, metadata probing via PyAV, and lazy filmstrip/cut generation."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np

from pipeline.config import VIDEO_EXT, Settings, get_settings
from pipeline.db import DB
from pipeline.decode import safe_decode_frames


def make_slug(name: str) -> str:
    base = Path(name).stem
    slug = re.sub(r"[^\w\s-]", "", base).strip().lower()
    slug = re.sub(r"[-\s]+", "_", slug)
    return slug[:80] or "video"


def compute_video_id(path: Path, size: int) -> str:
    raw = f"{path.resolve()}:{size}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def compute_video_title_hash(title: str, prefix: str = "v_", length: int = 8) -> str:
    """Computes a deterministic compact hash from the video title/filename for LoRA scene tagging.

    Example: 'scene_01_bondage.mp4' -> 'v_3f8a1c02'
    """
    stem = Path(title).stem if title else "video"
    cleaned = stem.strip().lower().encode("utf-8")
    digest = hashlib.sha256(cleaned).hexdigest()[:length]
    return f"{prefix}{digest}" if prefix else digest



class Indexer:
    def __init__(self, db: DB, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def scan_and_sync(self) -> list[dict]:
        """Scan video directory, probe new/modified files, update SQLite."""
        self.settings.ensure_dirs()
        video_dir = self.settings.video_dir
        if not video_dir.exists():
            return []

        found_ids: set[str] = set()
        scanned_videos: list[dict] = []

        for p in sorted(video_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
                continue

            stat = p.stat()
            resolved_path = str(p.resolve())
            existing = self.db.one("SELECT * FROM videos WHERE path=?", (resolved_path,))
            if not existing:
                vid_id = compute_video_id(p, stat.st_size)
                existing = self.db.one("SELECT * FROM videos WHERE id=?", (vid_id,))
            else:
                vid_id = existing["id"]

            found_ids.add(vid_id)

            if existing and existing.get("size") == stat.st_size and existing.get("missing") == 0:
                scanned_videos.append(existing)
                continue

            # Probe file
            meta = self.probe_file(p, vid_id)
            with self.db.write() as conn:
                conn.execute(
                    """
                    INSERT INTO videos (
                        id, path, name, slug, title_hash, size, mtime, duration, fps,
                        width, height, codec, broken, broken_reason, status,
                        missing, updated_at
                    ) VALUES (
                        :id, :path, :name, :slug, :title_hash, :size, :mtime, :duration, :fps,
                        :width, :height, :codec, :broken, :broken_reason, :status,
                        0, :updated_at
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        path=excluded.path, name=excluded.name, slug=excluded.slug,
                        title_hash=excluded.title_hash, size=excluded.size,
                        mtime=excluded.mtime, duration=excluded.duration, fps=excluded.fps,
                        width=excluded.width, height=excluded.height, codec=excluded.codec,
                        broken=excluded.broken, broken_reason=excluded.broken_reason,
                        missing=0, updated_at=excluded.updated_at
                    """,
                    meta,
                )
            row = self.db.one("SELECT * FROM videos WHERE id=?", (vid_id,))
            if row:
                scanned_videos.append(row)

        # Mark missing files
        if found_ids:
            with self.db.write() as conn:
                placeholders = ",".join("?" for _ in found_ids)
                conn.execute(f"UPDATE videos SET missing=1 WHERE id NOT IN ({placeholders})", tuple(found_ids))

        return scanned_videos

    def probe_file(self, path: Path, vid_id: str) -> dict[str, Any]:
        stat = path.stat()
        meta: dict[str, Any] = {
            "id": vid_id,
            "path": str(path.resolve()),
            "name": path.name,
            "slug": make_slug(path.name),
            "title_hash": compute_video_title_hash(path.name),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "duration": 0.0,
            "fps": 0.0,
            "width": 0,
            "height": 0,
            "codec": "",
            "broken": 0,
            "broken_reason": None,
            "status": "new",
            "updated_at": time.time(),
        }

        try:
            with av.open(str(path)) as container:
                if not container.streams.video:
                    meta["broken"] = 1
                    meta["broken_reason"] = "No video stream"
                    return meta

                vstream = container.streams.video[0]
                codec_ctx = vstream.codec_context
                meta["codec"] = codec_ctx.name or "unknown"
                meta["width"] = codec_ctx.width or 0
                meta["height"] = codec_ctx.height or 0
                meta["fps"] = round(float(vstream.average_rate or 0.0), 3)

                dur = float(container.duration / av.time_base) if container.duration else (
                    float(vstream.duration * vstream.time_base) if vstream.duration else 0.0
                )
                meta["duration"] = round(dur, 2)

                # Test decoding first frame
                try:
                    next(container.decode(video=0))
                except Exception as ex:
                    meta["broken"] = 1
                    meta["broken_reason"] = f"Frame decode failure: {ex}"
                    return meta

                if dur < 1.0:
                    meta["broken"] = 1
                    meta["broken_reason"] = f"Truncated video ({dur:.1f}s)"
                elif dur < self.settings.min_duration_s:
                    meta["broken"] = 1
                    meta["broken_reason"] = f"Too short ({dur:.1f}s < {self.settings.min_duration_s:.0f}s)"

        except Exception as ex:
            meta["broken"] = 1
            meta["broken_reason"] = f"Container open failure: {ex}"

        return meta

    def get_or_build_filmstrip(self, video_id: str, max_thumbnails: int = 120) -> dict[str, Any]:
        """Extract keyframe thumbnails and detect scene cut markers."""
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video or video.get("broken"):
            return {"thumbnails": [], "cuts": []}

        strip_dir = self.settings.filmstrip_dir / video_id
        meta_file = strip_dir / "filmstrip.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                return data
            except Exception:
                pass

        strip_dir.mkdir(parents=True, exist_ok=True)
        vpath = Path(video["path"])
        if not vpath.exists():
            return {"thumbnails": [], "cuts": []}

        thumbnails = []
        cuts = []
        prev_hist = None

        try:
            with av.open(str(vpath)) as container:
                stream = container.streams.video[0]
                stream.codec_context.skip_frame = "NONKEY"
                stream.thread_type = "AUTO"
                tb = stream.time_base
                total_duration = float(video.get("duration") or 3600.0)

                # Sample at most ~100 keyframes across the duration
                min_interval = max(3.0, total_duration / max_thumbnails)
                last_t = -min_interval

                thumb_idx = 0
                for frame in safe_decode_frames(container, stream):
                    if frame.pts is None:
                        continue
                    t = float(frame.pts * tb)

                    # Scene cut check using tiny 64x36 histogram
                    raw_bgr = frame.to_ndarray(format="bgr24")
                    tiny = cv2.resize(raw_bgr, (64, 36), interpolation=cv2.INTER_AREA)
                    hist = cv2.calcHist([tiny], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
                    hist = cv2.normalize(hist, hist).flatten()

                    if prev_hist is not None:
                        sim = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                        if sim < 0.60:
                            cuts.append(round(t, 2))
                    prev_hist = hist

                    # Timeline thumbnail downscaling
                    if t - last_t >= min_interval:
                        last_t = t
                        thumb_path = strip_dir / f"thumb_{thumb_idx:04d}.webp"
                        th_w = 160
                        h, w = raw_bgr.shape[:2]
                        th_h = max(16, int(h * (th_w / w)))
                        small = cv2.resize(raw_bgr, (th_w, th_h), interpolation=cv2.INTER_AREA)
                        cv2.imwrite(str(thumb_path), small, [cv2.IMWRITE_WEBP_QUALITY, 75])
                        thumbnails.append({
                            "t": round(t, 2),
                            "file": thumb_path.name,
                            "url": f"/api/videos/{video_id}/filmstrip/{thumb_path.name}",
                        })
                        thumb_idx += 1
                    del raw_bgr, frame
        except Exception as e:
            print(f"Warning: Filmstrip decoding error for video {video_id}: {e}")

        result = {"thumbnails": thumbnails, "cuts": cuts}
        meta_file.write_text(json.dumps(result), encoding="utf-8")
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET filmstrip_ready=1 WHERE id=?", (video_id,))

        return result
