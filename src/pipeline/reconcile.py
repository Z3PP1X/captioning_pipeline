"""Reconciliation between disk artifacts (captions, exports) and SQLite database state."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from pipeline.captions import import_caption
from pipeline.config import Settings, get_settings
from pipeline.db import DB
from pipeline.export import existing_caption_kind

logger = logging.getLogger("pipeline.reconcile")

# Matches: {slug}_c{chunk_num:02d}_{pts_ms:07d}.txt
CAPTION_FILE_REGEX = re.compile(r"^(.*)_c(\d+)_(\d+)\.txt$")

# Frames the caption job and exporter act on (any role; mirrors the caption job query)
APPROVED = "p.state IN ('keep', 'star')"


def reconcile_captions_and_state(db: DB, settings: Settings | None = None) -> dict[str, Any]:
    """
    1. Scans settings.finished_dir/per_video on disk for generated captions and PNGs.
    2. Matches each caption to its corresponding frame and inserts into `exports` table, and imports
       the text into `captions` (source 'imported', or 'stub' for exporter placeholders). Idempotent:
       a frame that already has a caption row keeps it (a VLM row is never replaced; a stub row is
       upgraded when a real caption file shows up).
    3. Auto-heals stuck video statuses (e.g. captioning / extracting_750 when no active jobs exist).
    4. Marks videos as 'exported' if all picks have captions.
    """
    settings = settings or get_settings()
    finished_dir = settings.finished_dir
    per_video_dir = finished_dir / "per_video"
    merged_dir = finished_dir / "merged"

    stats = {
        "captions_found_on_disk": 0,
        "exports_inserted_or_updated": 0,
        "captions_imported": 0,
        "videos_updated": 0,
        "videos_healed": [],
    }

    if not per_video_dir.exists():
        return stats

    now = time.time()
    txt_files = list(per_video_dir.glob("*/*.txt"))
    stats["captions_found_on_disk"] = len(txt_files)

    # Cache all video slugs and IDs for fast lookup
    videos = db.all("SELECT id, slug, name, status FROM videos")
    slug_to_id = {v["slug"]: v["id"] for v in videos}
    name_to_id = {v["name"]: v["id"] for v in videos}

    # 1. Reconcile exports from disk files
    for txt_path in txt_files:
        m = CAPTION_FILE_REGEX.match(txt_path.name)
        if not m:
            continue

        vslug, chunk_num_str, pts_ms_str = m.groups()
        t_sec = int(pts_ms_str) / 1000.0

        # Match video
        vid = slug_to_id.get(vslug) or slug_to_id.get(txt_path.parent.name)
        if not vid:
            # Fallback search
            match = db.one("SELECT id FROM videos WHERE slug=? OR name LIKE ?", (vslug, f"%{vslug}%"))
            if match:
                vid = match["id"]
                slug_to_id[vslug] = vid

        if not vid:
            continue

        # Match frame near timestamp t_sec (within 50ms)
        frame = db.one(
            """
            SELECT id, chunk_id FROM frames
            WHERE video_id=? AND ABS(t - ?) < 0.05
            ORDER BY ABS(t - ?) ASC LIMIT 1
            """,
            (vid, t_sec, t_sec),
        )
        if not frame:
            continue

        frame_id = frame["id"]
        png_path = txt_path.with_suffix(".png")
        merged_png = merged_dir / png_path.name
        merged_txt = merged_dir / txt_path.name
        kind = existing_caption_kind(txt_path)
        caption_text = txt_path.read_text(encoding="utf-8").strip() if kind else ""

        with db.write() as conn:
            if caption_text and import_caption(
                conn, frame_id, vid, caption_text,
                source="imported" if kind == "caption" else "stub",
                created_at=txt_path.stat().st_mtime,
            ):
                stats["captions_imported"] += 1
            conn.execute(
                """
                INSERT INTO exports (frame_id, per_video_path, merged_path, exported_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(frame_id) DO UPDATE SET
                    per_video_path=excluded.per_video_path,
                    merged_path=excluded.merged_path,
                    exported_at=excluded.exported_at
                """,
                (
                    frame_id,
                    str(png_path) if png_path.exists() else str(txt_path),
                    str(merged_png) if merged_png.exists() else str(merged_txt),
                    txt_path.stat().st_mtime if txt_path.exists() else now,
                ),
            )
        stats["exports_inserted_or_updated"] += 1

    # 2. Auto-heal stuck video statuses
    # Get all active job video IDs
    active_job_vids = {
        r["video_id"]
        for r in db.all("SELECT DISTINCT video_id FROM jobs WHERE status IN ('queued', 'running') AND video_id IS NOT NULL")
    }

    all_videos = db.all("SELECT id, name, status FROM videos WHERE missing = 0")
    for v in all_videos:
        vid = v["id"]
        current_status = v["status"]

        # If a job is actively running/queued for this video, do not alter its transitional status
        if vid in active_job_vids:
            continue

        picks_row = db.one(
            "SELECT COUNT(*) as cnt FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=? AND p.role='pick'",
            (vid,),
        )
        pick_count = picks_row["cnt"] if picks_row else 0

        # Only approved frames are captioned/exported (same filter as the caption job), so rejected
        # picks must not count towards "fully exported".
        approved_row = db.one(
            f"""
            SELECT COUNT(*) as approved,
                   SUM(CASE WHEN e.frame_id IS NOT NULL THEN 1 ELSE 0 END) as exported
            FROM picks p JOIN frames f ON p.frame_id=f.id
            LEFT JOIN exports e ON e.frame_id=f.id
            WHERE f.video_id=? AND {APPROVED}
            """,
            (vid,),
        )
        approved_count = approved_row["approved"] if approved_row else 0
        export_count = (approved_row["exported"] or 0) if approved_row else 0

        chunks_row = db.one("SELECT COUNT(*) as cnt FROM chunks WHERE video_id=?", (vid,))
        chunk_count = chunks_row["cnt"] if chunks_row else 0

        frames_row = db.one("SELECT COUNT(*) as cnt FROM frames WHERE video_id=?", (vid,))
        frame_count = frames_row["cnt"] if frames_row else 0

        # Determine target status
        target_status = current_status
        if current_status not in ("skipped", "ignored", "broken"):
            if approved_count > 0 and export_count >= approved_count:
                target_status = "exported"
            elif pick_count > 0:
                # Video has curated picks; if it was stuck in captioning or extracting_750, heal to curating_300
                target_status = "curating_300"
            elif frame_count > 0:
                target_status = "chunked"
            elif chunk_count > 0:
                target_status = "chunked"
            elif current_status in ("captioning", "extracting_750", "extracting", "curating_300"):
                target_status = "new"

        if target_status != current_status:
            with db.write() as conn:
                conn.execute("UPDATE videos SET status=?, updated_at=? WHERE id=?", (target_status, now, vid))
            stats["videos_updated"] += 1
            stats["videos_healed"].append({
                "id": vid,
                "name": v["name"],
                "from_status": current_status,
                "to_status": target_status,
                "picks": pick_count,
                "approved": approved_count,
                "exports": export_count,
            })

    return stats


def get_video_scenes_caption_stats(db: DB, video_id: str) -> list[dict[str, Any]]:
    """
    Returns per-chunk caption completion statistics for a video.
    Allows the UI to see exactly which scenes are fully captioned.
    """
    chunks = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC, start_s ASC", (video_id,))
    out = []
    for ch in chunks:
        cid = ch["id"]
        picks_cnt = db.one(
            "SELECT COUNT(*) as cnt FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.chunk_id=? AND p.role='pick'",
            (cid,),
        )["cnt"]
        # Approved (keep/star) frames are what gets captioned; rejected picks do not block "fully captioned"
        approved_cnt = db.one(
            f"SELECT COUNT(*) as cnt FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.chunk_id=? AND {APPROVED}",
            (cid,),
        )["cnt"]

        exports_cnt = db.one(
            f"""
            SELECT COUNT(*) as cnt FROM exports e JOIN frames f ON e.frame_id=f.id
            JOIN picks p ON p.frame_id=f.id
            WHERE f.chunk_id=? AND {APPROVED}
            """,
            (cid,),
        )["cnt"]

        total_frames = db.one("SELECT COUNT(*) as cnt FROM frames WHERE chunk_id=?", (cid,))["cnt"]

        is_full = (exports_cnt >= approved_cnt and approved_cnt > 0)
        progress = round((exports_cnt / approved_cnt) * 100, 1) if approved_cnt > 0 else 0.0

        item = dict(ch)
        item.update({
            "picks_count": picks_cnt,
            "approved_count": approved_cnt,
            "exports_count": exports_cnt,
            "captioned_count": exports_cnt,
            "total_frames": total_frames,
            "is_fully_captioned": is_full,
            "caption_progress_pct": min(100.0, progress),
        })
        out.append(item)
    return out
