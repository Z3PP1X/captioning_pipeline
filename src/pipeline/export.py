"""Finished dataset export: crop, multiples-of-16 alignment, sRGB PNG, caption stubs, and manifest."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB


def snap_to_multiple_of_16(img: np.ndarray) -> np.ndarray:
    """Crop up to 15 pixels from edges to make width and height multiples of 16 (0 aspect distortion)."""
    h, w = img.shape[:2]
    new_w = (w // 16) * 16
    new_h = (h // 16) * 16
    if new_w == w and new_h == h:
        return img

    dw = w - new_w
    dh = h - new_h
    x0 = dw // 2
    y0 = dh // 2
    return img[y0:y0 + new_h, x0:x0 + new_w]


def clamp_max_edge(img: np.ndarray, max_edge: int = 2048) -> np.ndarray:
    """Downscale with Lanczos if max dimension > max_edge. Never upscales."""
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_edge:
        return img
    scale = max_edge / long_edge
    new_w = max(16, int(round(w * scale)))
    new_h = max(16, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)


def apply_letterbox_crop(img: np.ndarray, letterbox: dict | None) -> np.ndarray:
    if not letterbox or not isinstance(letterbox, dict):
        return img
    h, w = img.shape[:2]
    top = int(letterbox.get("top", 0) * h)
    bottom = int(letterbox.get("bottom", 0) * h)
    left = int(letterbox.get("left", 0) * w)
    right = int(letterbox.get("right", 0) * w)
    if (top + bottom < h - 32) and (left + right < w - 32):
        return img[top:h - bottom, left:w - right]
    return img


def apply_watermark_crop(img: np.ndarray, watermark_rect: dict | None) -> np.ndarray:
    if not watermark_rect or not isinstance(watermark_rect, dict):
        return img
    h, w = img.shape[:2]
    x = max(0, int(watermark_rect.get("x", 0) * w))
    y = max(0, int(watermark_rect.get("y", 0) * h))
    rw = min(w - x, int(watermark_rect.get("width", 0) * w))
    rh = min(h - y, int(watermark_rect.get("height", 0) * h))
    if rw <= 0 or rh <= 0:
        return img
    # If the watermark is along the bottom edge, crop off the bottom strip
    if (y + rh >= int(h * 0.90)) and (rh < int(h * 0.35)):
        new_h = max(32, y)
        return img[:new_h, :]
    # If the watermark is along the top edge, crop off the top strip
    if (y <= int(h * 0.10)) and (rh < int(h * 0.35)):
        new_y = min(h - 32, y + rh)
        return img[new_y:, :]
    return img


def render_export_image(
    img: np.ndarray,
    crop: dict | None = None,
    letterbox: dict | None = None,
    watermark_rect: dict | None = None,
    min_edge: int = 0,
    max_edge: int = 2048,
) -> np.ndarray | None:
    """Standardized image processing pipeline for captioning and dataset export.
    
    1. Apply manual crop if present
    2. Apply letterbox removal if present
    3. Apply watermark crop if present
    4. Check min_edge constraint
    5. Clamp max_edge with Lanczos downscale
    6. Snap to multiple of 16 for DiT/MMDiT latent compatibility
    """
    if img is None or img.size == 0:
        return None

    # 1. Manual crop
    if crop and isinstance(crop, dict):
        h, w = img.shape[:2]
        cx0 = max(0, int(crop.get("x", 0) * w))
        cy0 = max(0, int(crop.get("y", 0) * h))
        cw = min(w - cx0, int(crop.get("width", 1) * w))
        ch = min(h - cy0, int(crop.get("height", 1) * h))
        if cw > 32 and ch > 32:
            img = img[cy0:cy0 + ch, cx0:cx0 + cw]

    # 2. Letterbox crop
    img = apply_letterbox_crop(img, letterbox)

    # 3. Watermark crop
    img = apply_watermark_crop(img, watermark_rect)

    # 4. Check min_edge
    h, w = img.shape[:2]
    if min_edge > 0 and min(h, w) < min_edge:
        return None

    # 5. Clamp max edge
    if max_edge > 0:
        img = clamp_max_edge(img, max_edge=max_edge)

    # 6. Snap to multiple of 16
    return snap_to_multiple_of_16(img)


def append_manifest_record(manifest_path: Path, meta_payload: dict[str, Any]) -> None:
    """Append a metadata record to manifest.jsonl without creating duplicate entries for the same file."""
    stem = meta_payload.get("file_stem") or (Path(meta_payload["image"]).stem if meta_payload.get("image") else "")
    vid_id = meta_payload.get("video_id")
    ts = meta_payload.get("timestamp_s")

    if manifest_path.is_file():
        try:
            lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
            found = False
            new_lines = []
            for line in lines:
                is_match = False
                if stem and f'"{stem}' in line:
                    is_match = True
                elif vid_id is not None and ts is not None and f'"{vid_id}"' in line:
                    try:
                        parsed = json.loads(line)
                        if parsed.get("video_id") == vid_id and abs(float(parsed.get("timestamp_s", -999)) - float(ts)) < 0.001:
                            is_match = True
                    except Exception:
                        pass

                if is_match:
                    new_lines.append(json.dumps(meta_payload) + "\n")
                    found = True
                else:
                    new_lines.append(line)

            if found:
                manifest_path.write_text("".join(new_lines), encoding="utf-8")
                return
        except Exception:
            pass

    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(meta_payload) + "\n")


def existing_caption_kind(txt_path: Path) -> str | None:
    """Classify a caption file on disk: None (absent/empty), 'stub' (exporter-generated) or 'caption' (VLM).

    Only the exporter writes a `.json` sidecar, and it marks stubs with `caption_source`. Legacy sidecars
    without the field were always stubs. A `.txt` without a sidecar comes from the caption job.
    """
    if not txt_path.is_file() or txt_path.stat().st_size <= 10:
        return None
    sidecar = txt_path.with_suffix(".json")
    if not sidecar.is_file():
        return "caption"
    try:
        source = json.loads(sidecar.read_text(encoding="utf-8")).get("caption_source", "stub")
    except Exception:
        source = "stub"
    return "stub" if source == "stub" else "caption"


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link src to dst (replacing dst); copy when hard links are unsupported."""
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        dst.write_bytes(src.read_bytes())


class Exporter:
    def __init__(self, db: DB, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def export_video(
        self,
        video_id: str,
        custom_trigger: str | None = None,
    ) -> dict[str, Any]:
        """Export all approved ('keep' or 'star') frames for a video to per_video and merged folders."""
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            return {"exported": 0, "error": "Video not found"}

        user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))
        trigger_word = custom_trigger or user_settings.get("trigger_word", "")
        max_edge = int(user_settings.get("max_edge", 2048))
        min_edge = int(user_settings.get("min_export_edge", 0))

        vslug = video["slug"]
        per_video_dir = self.settings.finished_dir / "per_video" / vslug
        merged_dir = self.settings.finished_dir / "merged"
        per_video_dir.mkdir(parents=True, exist_ok=True)
        merged_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = merged_dir / "manifest.jsonl"

        # Fetch kept frames
        query = """
            SELECT f.id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.full_path,
                   c.position as chunk_pos, c.tags as chunk_tags,
                   p.crop, p.state
            FROM frames f
            JOIN picks p ON f.id = p.frame_id
            JOIN chunks c ON f.chunk_id = c.id
            WHERE f.video_id = ? AND p.state IN ('keep', 'star')
            ORDER BY c.position ASC, f.t ASC
        """
        kept_frames = self.db.all(query, (video_id,))
        if not kept_frames:
            return {"exported": 0, "msg": "No frames marked for keep"}

        # Ensure any promoted runners have their native high-res frames decoded on disk (C1/H1)
        from pipeline.decode import ensure_native_frames
        ensure_native_frames(self.db, [item["id"] for item in kept_frames])

        exported_count = 0
        kept_captions = 0
        now = time.time()

        for item in kept_frames:
            # File names
            pts_ms = int(item["t"] * 1000)
            chunk_num = item["chunk_pos"] + 1
            base_name = f"{vslug}_c{chunk_num:02d}_{pts_ms:07d}"
            img_filename = f"{base_name}.png"
            txt_filename = f"{base_name}.txt"
            json_filename = f"{base_name}.json"

            per_img_path = per_video_dir / img_filename
            per_txt_path = per_video_dir / txt_filename
            per_json_path = per_video_dir / json_filename
            merged_img_path = merged_dir / img_filename

            # Never overwrite a VLM caption: re-link it and record the export only
            has_caption = existing_caption_kind(per_txt_path) == "caption"
            if has_caption and per_img_path.is_file():
                for src_p, dst_p in [(per_img_path, merged_img_path), (per_txt_path, merged_dir / txt_filename)]:
                    if not dst_p.exists():
                        link_or_copy(src_p, dst_p)
                self._record_export(item["id"], per_img_path, merged_img_path, now)
                kept_captions += 1
                continue

            full_path = Path(item["full_path"])
            if not full_path.is_file():
                continue

            raw_img = cv2.imread(str(full_path))
            if raw_img is None:
                continue

            img = render_export_image(
                raw_img,
                crop=item.get("crop"),
                letterbox=video.get("letterbox"),
                watermark_rect=video.get("watermark_rect"),
                min_edge=min_edge,
                max_edge=max_edge,
            )
            if img is None:
                continue

            # Save PNG (sRGB, EXIF-free)
            cv2.imwrite(str(per_img_path), img)

            if has_caption:
                # The caption survived but its image did not: restore the PNG, leave the caption alone
                for src_p, dst_p in [(per_img_path, merged_img_path), (per_txt_path, merged_dir / txt_filename)]:
                    link_or_copy(src_p, dst_p)
                self._record_export(item["id"], per_img_path, merged_img_path, now)
                kept_captions += 1
                continue

            # Generate caption stub
            raw_tags = item.get("chunk_tags")
            if isinstance(raw_tags, str):
                try:
                    chunk_tags = json.loads(raw_tags)
                except Exception:
                    chunk_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            elif isinstance(raw_tags, list):
                chunk_tags = raw_tags
            else:
                chunk_tags = []
            zs = item.get("zs_scores") or {}
            frame_tags = zs.get("tags") or []
            caption_parts = []
            if trigger_word:
                caption_parts.append(trigger_word)
            seen_tags = set(t.lower() for t in caption_parts)
            for t in (chunk_tags + frame_tags):
                t_clean = t.strip()
                if t_clean and t_clean.lower() not in seen_tags:
                    seen_tags.add(t_clean.lower())
                    caption_parts.append(t_clean)
            if zs.get("best_label") and zs["best_label"].lower() not in seen_tags:
                caption_parts.append(zs["best_label"])

            caption_text = ", ".join(caption_parts)
            per_txt_path.write_text(caption_text, encoding="utf-8")

            # JSON metadata sidecar
            meta_payload = {
                "source_video": video["name"],
                "video_id": video_id,
                "chunk_id": item["chunk_id"],
                "timestamp_s": item["t"],
                "width": img.shape[1],
                "height": img.shape[0],
                "caption": caption_text,
                # Marks this .txt as a placeholder so the caption job re-captions it and later exports keep it
                "caption_source": "stub",
                "metrics": item.get("metrics"),
                "zero_shot_scores": zs,
                "exported_at": now,
            }
            per_json_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

            # Link or copy to merged directory
            for src_p, dst_p in [
                (per_img_path, merged_img_path),
                (per_txt_path, merged_dir / txt_filename),
                (per_json_path, merged_dir / json_filename),
            ]:
                link_or_copy(src_p, dst_p)

            # Append to JSONL manifest without duplicate entries
            append_manifest_record(manifest_path, meta_payload)

            self._record_export(item["id"], per_img_path, merged_img_path, now)
            exported_count += 1

        # Update video status to exported
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET status='exported' WHERE id=?", (video_id,))

        return {
            "exported": exported_count + kept_captions,
            "stubs_written": exported_count,
            "captions_kept": kept_captions,
            "per_video_dir": str(per_video_dir),
            "merged_dir": str(merged_dir),
        }

    def _record_export(self, frame_id: int, per_img_path: Path, merged_img_path: Path, now: float) -> None:
        with self.db.write() as conn:
            conn.execute(
                """
                INSERT INTO exports (frame_id, per_video_path, merged_path, exported_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(frame_id) DO UPDATE SET
                    per_video_path=excluded.per_video_path,
                    merged_path=excluded.merged_path,
                    exported_at=excluded.exported_at
                """,
                (frame_id, str(per_img_path), str(merged_img_path), now),
            )
