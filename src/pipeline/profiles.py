"""Per-video LoRA profile (HANDOFF decision 1): 'concept' (default) or 'character'.

A character LoRA needs the same identity in every training image. With profile 'character' the video's positive
person (the target person, see `video_persons`) is the character, and:
- identity gate: picks that do not show the character are demoted to runner-up and the best matching runners of the
  same chunk are promoted in their place (solo frames first);
- multi-person picks that do show the character get a portrait crop around the character's face, so the export
  (which applies `picks.crop`) is a solo image;
- captioning defaults to the 'character' caption mode.
Starred and already exported frames are never demoted, like in reselect.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.db import DB

PROFILES = ("concept", "character")
# Portrait crop around a face: the frame keeps head to mid-thigh (about 7 face heights), at a 2:3 aspect ratio
CROP_FACE_HEIGHTS = 7.0
CROP_ABOVE_FACE = 0.6
CROP_ASPECT = 2 / 3
MAX_CROP_AREA = 0.85  # a crop covering nearly the whole frame is pointless


def video_profile(db: DB, video_id: str) -> str:
    row = db.one("SELECT profile FROM videos WHERE id=?", (video_id,))
    profile = (row or {}).get("profile") or "concept"
    return profile if profile in PROFILES else "concept"


def caption_mode_for(db: DB, video_id: str, fallback: str = "concept") -> str:
    """Caption mode when the request does not set one: the character profile captions in 'character' mode."""
    return "character" if video_profile(db, video_id) == "character" else fallback


def _zs(row: dict[str, Any]) -> dict[str, Any]:
    zs = row.get("zs_scores")
    if isinstance(zs, str):
        try:
            zs = json.loads(zs)
        except Exception:
            zs = {}
    return zs if isinstance(zs, dict) else {}


def _image_size(row: dict[str, Any]) -> tuple[int, int] | None:
    """Size of the image the face boxes were detected on (preview first, as in face re-evaluation)."""
    from PIL import Image

    for key in ("preview_path", "full_path"):
        p = row.get(key)
        if p and Path(p).is_file():
            try:
                with Image.open(p) as im:
                    return im.size
            except Exception:
                continue
    return None


def character_crop(face_box: list[float], img_w: int, img_h: int) -> dict[str, float] | None:
    """Normalised portrait crop {x, y, width, height} around a face box [x, y, w, h] (pixels), or None."""
    fx, fy, fw, fh = (float(v) for v in face_box[:4])
    if fw <= 0 or fh <= 0 or img_w <= 0 or img_h <= 0:
        return None
    height = min(float(img_h), fh * CROP_FACE_HEIGHTS)
    width = min(float(img_w), height * CROP_ASPECT)
    cx = fx + fw / 2
    x0 = min(max(0.0, cx - width / 2), img_w - width)
    y0 = min(max(0.0, fy - fh * CROP_ABOVE_FACE), img_h - height)
    if (width * height) / float(img_w * img_h) > MAX_CROP_AREA:
        return None
    return {"x": round(x0 / img_w, 4), "y": round(y0 / img_h, 4),
            "width": round(width / img_w, 4), "height": round(height / img_h, 4)}


def _matched_face(zs: dict[str, Any]) -> list[float] | None:
    faces = [f for f in (zs.get("faces") or []) if isinstance(f, dict) and (f.get("pos_matched") or f.get("matched"))]
    if not faces:
        return None
    best = max(faces, key=lambda f: float(f.get("pos_sim", f.get("sim", 0.0)) or 0.0))
    return best.get("box")


def apply_character_profile(db: DB, video_id: str) -> dict[str, Any]:
    """Identity gate + solo crops for a video with profile 'character'. Idempotent; returns what changed."""
    if video_profile(db, video_id) != "character":
        return {"applied": False, "reason": "profile is not 'character'"}
    has_target = db.one(
        "SELECT 1 FROM video_persons WHERE video_id=? AND role='positive' LIMIT 1", (video_id,)
    ) or db.one("SELECT 1 FROM videos WHERE id=? AND target_person_id IS NOT NULL", (video_id,))
    if not has_target:
        return {"applied": False, "reason": "no target person: tag the character's face first"}

    rows = db.all(
        """
        SELECT f.id, f.chunk_id, f.zs_scores, f.preview_path, f.full_path, p.role, p.rank, p.state, p.crop,
               EXISTS (SELECT 1 FROM exports e WHERE e.frame_id=f.id) AS exported
        FROM frames f JOIN picks p ON p.frame_id=f.id
        WHERE f.video_id=?
        """,
        (video_id,),
    )
    by_chunk: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        zs = _zs(r)
        r["matched"] = bool(zs.get("target_person_matched")) or float(zs.get("target_person_sim", 0) or 0) >= 0.363
        r["face_count"] = int(zs.get("face_count", 0) or 0)
        r["quality"] = float(zs.get("composite", zs.get("quality", 0.5)) or 0.0)
        r["zs"] = zs
        by_chunk.setdefault(r["chunk_id"], []).append(r)

    demoted: list[int] = []
    promoted: list[int] = []
    cropped: list[int] = []
    unfilled = 0
    with db.write() as conn:
        for chunk_rows in by_chunk.values():
            picks = [r for r in chunk_rows if r["role"] == "pick"]
            off_target = [r for r in picks if not r["matched"] and r["state"] != "star" and not r["exported"]]
            if not off_target:
                continue
            runners = sorted(
                (r for r in chunk_rows if r["role"] != "pick" and r["matched"] and r["state"] != "reject"),
                # solo first, then quality
                key=lambda r: (r["face_count"] > 1, -r["quality"], r["rank"]),
            )
            for drop in off_target:
                conn.execute("UPDATE picks SET role='runner_up', state='candidate' WHERE frame_id=?", (drop["id"],))
                demoted.append(drop["id"])
                if runners:
                    best = runners.pop(0)
                    conn.execute("UPDATE picks SET role='pick', state='keep' WHERE frame_id=?", (best["id"],))
                    best["role"] = "pick"
                    promoted.append(best["id"])
                else:
                    unfilled += 1
        # Solo crops for multi-person picks that show the character (an existing manual crop wins)
        for r in rows:
            if r["role"] != "pick" or not r["matched"] or r["face_count"] <= 1 or r.get("crop"):
                continue
            box = _matched_face(r["zs"])
            size = _image_size(r) if box else None
            crop = character_crop(box, *size) if box and size else None
            if crop:
                conn.execute("UPDATE picks SET crop=? WHERE frame_id=?", (json.dumps(crop), r["id"]))
                cropped.append(r["id"])
    if promoted:
        from pipeline.decode import ensure_native_frames

        try:
            ensure_native_frames(db, promoted)
        except Exception:
            pass  # export and captioning decode native frames lazily as well
    return {"applied": True, "demoted": demoted, "promoted": promoted, "cropped": cropped, "unfilled": unfilled}
