"""Finished dataset export: 1MP center-crop bucket normalization, sRGB PNG, caption stubs, and manifest."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from pipeline.captions import get_accepted_caption, import_caption
from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.geometry.cropper import (
    BUCKETS_1MP_3,
    BUCKETS_1MP_4,
    DEFAULT_1MP_BUCKETS,
    center_crop_box,
    center_crop_to_bucket,
    resolve_buckets,
    select_nearest_bucket,
)

__all__ = [
    "BUCKETS_1MP_3",
    "BUCKETS_1MP_4",
    "DEFAULT_1MP_BUCKETS",
    "Exporter",
    "append_manifest_record",
    "apply_letterbox_crop",
    "apply_watermark_crop",
    "center_crop_box",
    "center_crop_to_bucket",
    "clamp_max_edge",
    "existing_caption_kind",
    "link_or_copy",
    "render_export_image",
    "resolve_buckets",
    "select_nearest_bucket",
    "snap_to_multiple_of_16",
    "write_caption_files",
]


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


def apply_letterbox_crop(img: np.ndarray, letterbox: dict[str, Any] | None) -> np.ndarray:
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


def apply_watermark_crop(img: np.ndarray, watermark_rect: dict[str, Any] | None) -> np.ndarray:
    if not watermark_rect or not isinstance(watermark_rect, dict):
        return img
    h, w = img.shape[:2]
    x = max(0, int(watermark_rect.get("x", 0) * w))
    y = max(0, int(watermark_rect.get("y", 0) * h))
    rw = min(w - x, int(watermark_rect.get("width", 0) * w))
    rh = min(h - y, int(watermark_rect.get("height", 0) * h))
    if min(rw, rh) <= 0 or rh >= int(h * 0.35):
        return img
    # If the watermark is along the bottom edge, crop off the bottom strip
    if y + rh >= int(h * 0.90):
        return img[:max(32, y), :]
    # If the watermark is along the top edge, crop off the top strip
    if y <= int(h * 0.10):
        return img[min(h - 32, y + rh):, :]
    return img


def _apply_manual_crop(img: np.ndarray, crop: dict[str, Any] | None) -> np.ndarray:
    if not crop or not isinstance(crop, dict):
        return img
    h, w = img.shape[:2]
    cx0 = max(0, int(crop.get("x", 0) * w))
    cy0 = max(0, int(crop.get("y", 0) * h))
    cw = min(w - cx0, int(crop.get("width", 1) * w))
    ch = min(h - cy0, int(crop.get("height", 1) * h))
    if min(cw, ch) > 32:
        return img[cy0:cy0 + ch, cx0:cx0 + cw]
    return img


def render_export_image(  # noqa: PLR0913
    img: np.ndarray,
    crop: dict[str, Any] | None = None,
    letterbox: dict[str, Any] | None = None,
    watermark_rect: dict[str, Any] | None = None,
    min_edge: int = 0,
    max_edge: int = 2048,
    *,
    normalize_buckets: bool = True,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> np.ndarray | None:
    """Standardized image processing pipeline for captioning and dataset export.

    1. Apply manual crop if present
    2. Apply letterbox removal if present
    3. Apply watermark crop if present
    4. Check min_edge constraint
    5. Center-crop and Lanczos-4 resize to nearest 1MP bucket (max 4 buckets)
    6. Clamp max_edge with Lanczos downscale
    7. Snap to multiple of 16 for DiT/MMDiT latent compatibility
    """
    if img is None or img.size == 0:
        return None

    img = _apply_manual_crop(img, crop)
    img = apply_letterbox_crop(img, letterbox)
    img = apply_watermark_crop(img, watermark_rect)

    if min_edge > 0 and min(img.shape[:2]) < min_edge:
        return None

    if normalize_buckets:
        img, _ = center_crop_to_bucket(img, buckets=buckets)

    if max_edge > 0:
        img = clamp_max_edge(img, max_edge=max_edge)

    return snap_to_multiple_of_16(img)


def _manifest_stem(meta_payload: dict[str, Any]) -> str:
    if meta_payload.get("file_stem"):
        return str(meta_payload["file_stem"])
    if meta_payload.get("image"):
        return Path(str(meta_payload["image"])).stem
    return ""


def _line_matches_record(line: str, stem: str, vid_id: Any, ts: Any) -> bool:
    if stem and f'"{stem}' in line:
        return True
    if vid_id is None or ts is None or f'"{vid_id}"' not in line:
        return False
    try:
        parsed = json.loads(line)
        return bool(
            parsed.get("video_id") == vid_id
            and abs(float(parsed.get("timestamp_s", -999)) - float(ts)) < 0.001
        )
    except Exception:
        return False


def _replace_matching_manifest_line(
    manifest_path: Path, meta_payload: dict[str, Any], stem: str, vid_id: Any, ts: Any
) -> bool:
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines(keepends=True)
        replacement = json.dumps(meta_payload) + "\n"
        new_lines = [replacement if _line_matches_record(ln, stem, vid_id, ts) else ln for ln in lines]
        if new_lines != lines:
            manifest_path.write_text("".join(new_lines), encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def append_manifest_record(manifest_path: Path, meta_payload: dict[str, Any]) -> None:
    """Append a metadata record to manifest.jsonl without creating duplicate entries for the same file."""
    stem = _manifest_stem(meta_payload)
    vid_id = meta_payload.get("video_id")
    ts = meta_payload.get("timestamp_s")

    if manifest_path.is_file() and _replace_matching_manifest_line(manifest_path, meta_payload, stem, vid_id, ts):
        return

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


def _remove_stale_files(paths: tuple[Path, ...]) -> bool:
    removed = False
    for stale in paths:
        if stale.exists():
            stale.unlink()
            removed = True
    return removed


def write_caption_files(
    text: str, per_txt: Path, merged_txt: Path | None = None, stale_sidecars: tuple[Path, ...] = ()
) -> bool:
    """Writes a caption from the captions table to its .txt artefact (and the merged link) when it differs.

    Stub sidecars are removed: they would mark the real caption as a stub again. Returns True on change.
    """
    text = (text or "").strip()
    changed = False
    if not per_txt.is_file() or per_txt.read_text(encoding="utf-8").strip() != text:
        per_txt.parent.mkdir(parents=True, exist_ok=True)
        per_txt.write_text(text, encoding="utf-8")
        changed = True
    if _remove_stale_files(stale_sidecars):
        changed = True
    if merged_txt is not None and (changed or not merged_txt.exists()):
        merged_txt.parent.mkdir(parents=True, exist_ok=True)
        link_or_copy(per_txt, merged_txt)
    return changed


def _parse_chunk_tags(raw_tags: Any) -> list[str]:
    if isinstance(raw_tags, list):
        return [str(t) for t in raw_tags]
    if not isinstance(raw_tags, str):
        return []
    try:
        parsed = json.loads(raw_tags)
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    except Exception:
        return [t.strip() for t in raw_tags.split(",") if t.strip()]


def _compose_stub_caption(
    trigger_word: str,
    title_hash: str,
    chunk_tags: list[str],
    frame_tags: list[str],
    best_label: str | None,
) -> str:
    seed = [p for p in (trigger_word, title_hash if trigger_word else "") if p]
    candidates = [*seed, *chunk_tags, *frame_tags, *( [best_label] if best_label else [] )]
    caption_parts: list[str] = []
    seen_tags: set[str] = set()
    for tag in candidates:
        clean = tag.strip()
        if clean and clean.lower() not in seen_tags:
            seen_tags.add(clean.lower())
            caption_parts.append(clean)
    return ", ".join(caption_parts)


def _render_frame_from_disk(
    src_path: Path,
    fallback_img: np.ndarray | None,
    item: dict[str, Any],
    video: dict[str, Any],
    cfg: dict[str, Any],
) -> np.ndarray | None:
    raw_img = cv2.imread(str(src_path)) if src_path.is_file() else fallback_img
    if raw_img is None:
        return None
    return render_export_image(
        raw_img,
        crop=item.get("crop"),
        letterbox=video.get("letterbox"),
        watermark_rect=video.get("watermark_rect"),
        min_edge=cfg["min_edge"],
        max_edge=cfg["max_edge"],
        normalize_buckets=cfg["normalize_buckets"],
        buckets=cfg["active_buckets"],
    )


def _ensure_bucket_normalized_png(
    per_img_path: Path,
    full_path: Path,
    item: dict[str, Any],
    video: dict[str, Any],
    cfg: dict[str, Any],
) -> tuple[int, int] | None:
    """Ensure existing per_img_path is normalized to one of active_buckets; re-render if needed."""
    existing = cv2.imread(str(per_img_path)) if per_img_path.is_file() else None
    if existing is not None:
        eh, ew = existing.shape[:2]
        if not cfg["normalize_buckets"] or (ew, eh) in cfg["active_buckets"]:
            return ew, eh

    src_path = full_path if full_path.is_file() else per_img_path
    rendered = _render_frame_from_disk(src_path, existing, item, video, cfg)
    if rendered is None:
        return None
    cv2.imwrite(str(per_img_path), rendered)
    return int(rendered.shape[1]), int(rendered.shape[0])


class Exporter:
    def __init__(self, db: DB, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def _build_stub_caption(
        self,
        video: dict[str, Any],
        item: dict[str, Any],
        trigger_word: str,
    ) -> tuple[str, list[str], dict[str, Any]]:
        chunk_tags = _parse_chunk_tags(item.get("chunk_tags"))
        zs = item.get("zs_scores") or {}
        image_tags = [
            r["name"]
            for r in self.db.all(
                "SELECT t.name FROM frame_tags ft JOIN image_tags t ON t.id=ft.tag_id "
                "WHERE ft.frame_id=? AND ft.status='confirmed' ORDER BY t.name",
                (item["id"],),
            )
        ]
        frame_tags = image_tags + list(zs.get("tags") or [])
        from pipeline.indexer import compute_video_title_hash

        title_hash = video.get("title_hash") or compute_video_title_hash(video["name"])
        caption_text = _compose_stub_caption(
            trigger_word=trigger_word,
            title_hash=title_hash,
            chunk_tags=chunk_tags,
            frame_tags=frame_tags,
            best_label=zs.get("best_label"),
        )
        return caption_text, image_tags, zs

    def _sync_existing_caption(
        self,
        video_id: str,
        item: dict[str, Any],
        per_txt_path: Path,
        merged_txt_path: Path,
        stale_sidecars: tuple[Path, ...],
    ) -> bool:
        db_caption = get_accepted_caption(self.db, item["id"])
        if db_caption:
            write_caption_files(
                db_caption["text"],
                per_txt_path,
                merged_txt_path,
                stale_sidecars=stale_sidecars,
            )
        has_caption = existing_caption_kind(per_txt_path) == "caption"
        if has_caption and not db_caption:
            with self.db.write() as conn:
                import_caption(
                    conn,
                    item["id"],
                    video_id,
                    per_txt_path.read_text(encoding="utf-8"),
                    source="imported",
                    created_at=per_txt_path.stat().st_mtime,
                )
        return has_caption

    def _keep_existing_captioned_frame(
        self,
        video: dict[str, Any],
        item: dict[str, Any],
        cfg: dict[str, Any],
        paths: tuple[Path, Path, Path, Path, Path],
    ) -> tuple[str, str | None]:
        full_path, per_img, per_txt, merged_img, merged_txt = paths
        dims = _ensure_bucket_normalized_png(per_img, full_path, item, video, cfg)
        b_key = f"{dims[0]}x{dims[1]}" if dims else None
        link_or_copy(per_img, merged_img)
        if not merged_txt.exists():
            link_or_copy(per_txt, merged_txt)
        self._record_export(item["id"], per_img, merged_img, cfg["now"])
        return "kept", b_key

    def _export_single_frame(
        self,
        video: dict[str, Any],
        item: dict[str, Any],
        cfg: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Export one kept frame; returns (outcome: 'stub' | 'kept' | None, bucket_key)."""
        base_name = f"{video['slug']}_c{item['chunk_pos'] + 1:02d}_{int(item['t'] * 1000):07d}"
        per_video_dir: Path = cfg["per_video_dir"]
        merged_dir: Path = cfg["merged_dir"]

        per_img = per_video_dir / f"{base_name}.png"
        per_txt = per_video_dir / f"{base_name}.txt"
        per_json = per_video_dir / f"{base_name}.json"
        merged_img = merged_dir / f"{base_name}.png"
        merged_txt = merged_dir / f"{base_name}.txt"
        merged_json = merged_dir / f"{base_name}.json"

        has_caption = self._sync_existing_caption(
            video["id"], item, per_txt, merged_txt, (per_json, merged_json)
        )
        full_path = Path(item["full_path"] or "")

        if has_caption and per_img.is_file():
            return self._keep_existing_captioned_frame(
                video, item, cfg, (full_path, per_img, per_txt, merged_img, merged_txt)
            )

        img = _render_frame_from_disk(full_path, None, item, video, cfg)
        if img is None:
            return None, None

        cv2.imwrite(str(per_img), img)
        bucket_key = f"{img.shape[1]}x{img.shape[0]}"

        if has_caption:
            link_or_copy(per_img, merged_img)
            link_or_copy(per_txt, merged_txt)
            self._record_export(item["id"], per_img, merged_img, cfg["now"])
            return "kept", bucket_key

        self._write_stub_artefacts(
            video, item, img, bucket_key, cfg, (per_img, per_txt, per_json, merged_img, merged_txt, merged_json)
        )
        return "stub", bucket_key

    def _write_stub_artefacts(  # noqa: PLR0913
        self,
        video: dict[str, Any],
        item: dict[str, Any],
        img: np.ndarray,
        bucket_key: str,
        cfg: dict[str, Any],
        paths: tuple[Path, Path, Path, Path, Path, Path],
    ) -> None:
        per_img, per_txt, per_json, merged_img, merged_txt, merged_json = paths
        trigger_word: str = cfg["trigger_word"]
        now: float = cfg["now"]
        video_id = video["id"]

        caption_text, image_tags, zs = self._build_stub_caption(video, item, trigger_word)
        per_txt.write_text(caption_text, encoding="utf-8")
        with self.db.write() as conn:
            import_caption(
                conn,
                item["id"],
                video_id,
                caption_text,
                source="stub",
                trigger=trigger_word or None,
                created_at=now,
            )

        meta_payload = {
            "source_video": video["name"],
            "video_id": video_id,
            "chunk_id": item["chunk_id"],
            "timestamp_s": item["t"],
            "width": img.shape[1],
            "height": img.shape[0],
            "bucket": bucket_key,
            "caption": caption_text,
            "caption_source": "stub",
            "metrics": item.get("metrics"),
            "zero_shot_scores": zs,
            "image_tags": image_tags,
            "exported_at": now,
        }
        per_json.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

        for src_p, dst_p in [(per_img, merged_img), (per_txt, merged_txt), (per_json, merged_json)]:
            link_or_copy(src_p, dst_p)

        append_manifest_record(cfg["manifest_path"], meta_payload)
        self._record_export(item["id"], per_img, merged_img, now)

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
        vslug = video["slug"]
        per_video_dir = self.settings.finished_dir / "per_video" / vslug
        merged_dir = self.settings.finished_dir / "merged"
        per_video_dir.mkdir(parents=True, exist_ok=True)
        merged_dir.mkdir(parents=True, exist_ok=True)

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

        from pipeline.decode import ensure_native_frames

        ensure_native_frames(self.db, [item["id"] for item in kept_frames])

        cfg: dict[str, Any] = {
            "trigger_word": custom_trigger or user_settings.get("trigger_word", ""),
            "max_edge": int(user_settings.get("max_edge", 2048)),
            "min_edge": int(user_settings.get("min_export_edge", 0)),
            "normalize_buckets": bool(user_settings.get("normalize_to_buckets", True)),
            "active_buckets": resolve_buckets(str(user_settings.get("export_bucket_preset", "4_buckets"))),
            "per_video_dir": per_video_dir,
            "merged_dir": merged_dir,
            "manifest_path": merged_dir / "manifest.jsonl",
            "now": time.time(),
        }

        exported_count = 0
        kept_captions = 0
        bucket_counts: Counter[str] = Counter()

        for item in kept_frames:
            outcome, bucket_key = self._export_single_frame(video, item, cfg)
            if bucket_key:
                bucket_counts[bucket_key] += 1
            if outcome == "stub":
                exported_count += 1
            elif outcome == "kept":
                kept_captions += 1

        with self.db.write() as conn:
            conn.execute("UPDATE videos SET status='exported' WHERE id=?", (video_id,))

        return {
            "exported": exported_count + kept_captions,
            "stubs_written": exported_count,
            "captions_kept": kept_captions,
            "buckets": dict(bucket_counts),
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
