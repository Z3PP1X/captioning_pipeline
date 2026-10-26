"""Standardized Dataset Exporter (Module D.1).

Exports curated (`keep` / `star`) and captioned media items into a normalized training directory
and optional `.zip` archive:
- Normalized images (`.png` or `.jpg`, crop geometry applied, longest edge clamped via Lanczos-4,
  optional multiple-of-16 latent grid snapping).
- Paired `<item_stem>.txt` caption files with verified `trigger_word` placement.
- `metadata.jsonl` (HuggingFace / AI-Toolkit / Kohya compatible JSONL manifest).
- `dataset_card.json` (Audit report: resolution distribution, aspect buckets, tag distribution,
  cascade model statistics, word/token distribution, and triggerword configuration).
- `<dataset_slug>.zip` archive for one-click download.
"""
from __future__ import annotations

import json
import time
import zipfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np

from backend.app.config import get_app_settings
from backend.app.db.session import DatabaseManager, get_db, slugify_text
from backend.app.modules.captioning.skills import TriggerPosition, apply_trigger_word

MIN_DATASET_BUCKETS: int = 3
MAX_DATASET_BUCKETS: int = 4
BUCKET_DIM_LEN: int = 2

BUCKETS_1MP_4: tuple[tuple[int, int], ...] = (
    (1344, 768),
    (1152, 896),
    (896, 1152),
    (768, 1344),
)

BUCKETS_1MP_3: tuple[tuple[int, int], ...] = (
    (1344, 768),
    (1024, 1024),
    (768, 1344),
)

DEFAULT_1MP_BUCKETS: tuple[tuple[int, int], ...] = BUCKETS_1MP_4

BUCKET_PRESET_MAP: dict[str, tuple[tuple[int, int], ...]] = {
    "4_buckets": BUCKETS_1MP_4,
    "3_buckets": BUCKETS_1MP_3,
}


def _is_valid_bucket_pair(bucket: Any) -> bool:
    return bool(
        isinstance(bucket, (tuple, list))
        and len(bucket) == BUCKET_DIM_LEN
        and int(bucket[0]) > 0
        and int(bucket[1]) > 0
    )


def resolve_buckets(
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[tuple[int, int], ...]:
    """Resolve bucket preset or custom bucket sequence, enforcing at most 4 buckets."""
    if buckets is None or isinstance(buckets, str):
        return BUCKET_PRESET_MAP.get(str(buckets), BUCKETS_1MP_4)
    cleaned = [(int(b[0]), int(b[1])) for b in buckets if _is_valid_bucket_pair(b)]
    return tuple(cleaned[:MAX_DATASET_BUCKETS]) if cleaned else BUCKETS_1MP_4


def select_nearest_bucket(
    width: float,
    height: float,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[int, int]:
    """Select the 1MP bucket that maximizes retained center-crop area (closest aspect ratio)."""
    active_buckets = resolve_buckets(buckets)
    safe_w = max(1.0, float(width))
    safe_h = max(1.0, float(height))
    src_ar = safe_w / safe_h

    def _retained_fraction(bucket: tuple[int, int]) -> float:
        b_ar = float(bucket[0]) / float(bucket[1])
        return min(src_ar / b_ar, b_ar / src_ar)

    return max(active_buckets, key=_retained_fraction)


def center_crop_box(
    img_w: int,
    img_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, int, int]:
    """Compute exact centered crop rectangle (x0, y0, crop_w, crop_h) matching target aspect ratio."""
    safe_w = max(1, int(img_w))
    safe_h = max(1, int(img_h))
    target_ar = float(target_w) / float(max(1, target_h))
    src_ar = float(safe_w) / float(safe_h)

    if src_ar >= target_ar:
        crop_h = safe_h
        crop_w = max(1, min(safe_w, int(round(safe_h * target_ar))))
    else:
        crop_w = safe_w
        crop_h = max(1, min(safe_h, int(round(safe_w / target_ar))))

    x0 = max(0, (safe_w - crop_w) // 2)
    y0 = max(0, (safe_h - crop_h) // 2)
    return x0, y0, crop_w, crop_h


def center_crop_to_bucket(
    img: np.ndarray,
    bucket: tuple[int, int] | None = None,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Center-crop image to the nearest 1MP bucket aspect ratio and resize with Lanczos-4."""
    h, w = img.shape[:2]
    target_bucket = (
        (int(bucket[0]), int(bucket[1]))
        if bucket is not None
        else select_nearest_bucket(w, h, buckets=buckets)
    )
    bw, bh = target_bucket
    x0, y0, crop_w, crop_h = center_crop_box(w, h, bw, bh)
    cropped = img[y0 : y0 + crop_h, x0 : x0 + crop_w]
    if cropped.shape[1] != bw or cropped.shape[0] != bh:
        cropped = cv2.resize(cropped, (bw, bh), interpolation=cv2.INTER_LANCZOS4)
    return cropped, target_bucket


def snap_to_multiple_of_16(img: np.ndarray) -> np.ndarray:
    """Center-crop up to 15 pixels from width/height so both dimensions are multiples of 16."""
    h, w = img.shape[:2]
    new_w = max(16, (w // 16) * 16)
    new_h = max(16, (h // 16) * 16)
    if (new_w == w and new_h == h) or w < 16 or h < 16:
        return img
    dw = w - new_w
    dh = h - new_h
    x0 = dw // 2
    y0 = dh // 2
    return img[y0 : y0 + new_h, x0 : x0 + new_w]


def clamp_max_edge_lanczos(img: np.ndarray, max_edge: int = 2048) -> np.ndarray:
    """Downscale image with Lanczos-4 if max(w, h) > max_edge. Never upscales."""
    if max_edge <= 0:
        return img
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge <= max_edge:
        return img
    scale = float(max_edge) / float(long_edge)
    new_w = max(16, int(round(w * scale)))
    new_h = max(16, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)


def _ensure_3ch_bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _apply_crop_geometry(img: np.ndarray, crop_geometry: dict[str, float] | None) -> np.ndarray:
    if not isinstance(crop_geometry, dict):
        return img
    h, w = img.shape[:2]
    cx = max(0, int(float(crop_geometry.get("x", 0.0)) * w))
    cy = max(0, int(float(crop_geometry.get("y", 0.0)) * h))
    cw = min(w - cx, int(float(crop_geometry.get("width", 1.0)) * w))
    ch = min(h - cy, int(float(crop_geometry.get("height", 1.0)) * h))
    if min(cw, ch) >= 16:
        return img[cy : cy + ch, cx : cx + cw]
    return img


def normalize_export_image(  # noqa: PLR0913
    img_bgr: np.ndarray,
    *,
    crop_geometry: dict[str, float] | None = None,
    max_edge: int = 2048,
    snap_multiple_16: bool = True,
    normalize_buckets: bool = True,
    buckets: Sequence[tuple[int, int]] | str | None = None,
) -> np.ndarray:
    """Apply non-destructive crop, center-crop to 1MP bucket (max 4 buckets), clamp max_edge, and snap to 16."""
    if img_bgr is None or img_bgr.size == 0:
        raise ValueError("Cannot normalize empty image")

    img = _ensure_3ch_bgr(img_bgr)
    img = _apply_crop_geometry(img, crop_geometry)

    if normalize_buckets:
        img, _ = center_crop_to_bucket(img, buckets=buckets)

    if max_edge > 0:
        img = clamp_max_edge_lanczos(img, max_edge=max_edge)

    if snap_multiple_16:
        img = snap_to_multiple_of_16(img)

    return img


def _load_item_bgr(item: dict[str, Any]) -> np.ndarray | None:
    for key in ("extracted_path", "source_path", "thumbnail_path"):
        p_str = item.get(key)
        if p_str and Path(p_str).is_file():
            raw = np.fromfile(str(p_str), dtype=np.uint8)
            if raw.size > 0:
                img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
                if img is not None and img.size > 0:
                    return img
    return None


def _extract_item_tags(item: dict[str, Any]) -> list[str]:
    return [
        str(t.get("name") or t.get("slug"))
        for t in (item.get("tags") or [])
        if isinstance(t, dict) and (t.get("name") or t.get("slug"))
    ]


def _classify_aspect_bucket(width: int, height: int) -> str:
    ar = float(width) / float(max(1, height))
    if ar < 0.80:
        return "portrait_tall"
    if ar > 1.20:
        return "landscape_wide"
    return "square"


def _write_exported_image(img_out_path: Path, norm_img: np.ndarray, ext: str) -> None:
    if ext == "jpg":
        cv2.imwrite(str(img_out_path), norm_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    else:
        cv2.imwrite(str(img_out_path), norm_img)


def _numeric_stats(values: list[int] | list[float], precision: int) -> dict[str, int | float]:
    if not values:
        zero: int | float = 0 if precision == 0 else 0.0
        return {"min": zero, "max": zero, "mean": 0.0}
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(float(np.mean(values)), precision),
    }


def _summarize_manifest_records(manifest_records: list[dict[str, Any]]) -> dict[str, Any]:
    resolutions: Counter[str] = Counter()
    aspect_buckets: Counter[str] = Counter({"portrait_tall": 0, "square": 0, "landscape_wide": 0})
    tag_counts: Counter[str] = Counter()
    cascade_counts: Counter[str] = Counter()
    word_counts: list[int] = []
    quality_scores: list[float] = []

    for rec in manifest_records:
        resolutions[rec["bucket"]] += 1
        aspect_buckets[_classify_aspect_bucket(rec["width"], rec["height"])] += 1
        tag_counts.update(rec["tags"])
        cascade_counts[rec["cascade_layer"]] += 1
        word_counts.append(len(rec["text"].split()))
        quality_scores.append(rec["quality_score"])

    w_stats = _numeric_stats(word_counts, 2)
    return {
        "resolution_distribution": dict(resolutions),
        "aspect_ratio_buckets": dict(aspect_buckets),
        "tag_distribution": dict(tag_counts.most_common(50)),
        "cascade_layer_stats": dict(cascade_counts),
        "caption_word_stats": {
            "min_words": int(w_stats["min"]),
            "max_words": int(w_stats["max"]),
            "mean_words": float(w_stats["mean"]),
        },
        "quality_score_stats": _numeric_stats(quality_scores, 4),
    }


def _resolve_export_format_and_dir(
    image_format: str, output_dir: Path | str | None, export_root: Path, slug: str
) -> tuple[str, Path]:
    ext = "jpg" if str(image_format).lower() in ("jpg", "jpeg") else "png"
    export_dir = Path(output_dir) if output_dir is not None else (export_root / slug)
    export_dir.mkdir(parents=True, exist_ok=True)
    return ext, export_dir


def _resolve_dataset_slug(dataset: dict[str, Any], ds_id: str) -> str:
    raw_slug = dataset.get("slug") or dataset.get("name") or ds_id
    return slugify_text(str(raw_slug))


def _resolve_trigger_config(
    dataset: dict[str, Any],
    trigger_word: str | None,
    trigger_position: TriggerPosition | None,
) -> tuple[str, TriggerPosition]:
    raw_trig = trigger_word if trigger_word is not None else dataset.get("trigger_word")
    eff_trigger = str(raw_trig or "ohwx_subject").strip()
    eff_pos_raw = trigger_position or dataset.get("trigger_position") or "prefix"
    eff_pos: TriggerPosition = (
        eff_pos_raw if eff_pos_raw in ("prefix", "suffix", "natural_integration") else "prefix"
    )
    return eff_trigger, eff_pos


def _create_export_zip(export_root: Path, export_dir: Path, slug: str) -> Path:
    zip_path = export_root / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(export_dir.iterdir()):
            if file_path.is_file():
                zf.write(file_path, arcname=f"{slug}/{file_path.name}")
    return zip_path


class DatasetExporter:
    """Exports a curated & captioned dataset to disk and packages a `.zip` archive."""

    def __init__(self, db: DatabaseManager | None = None, export_root: Path | str | None = None) -> None:
        self.db = db or get_db()
        app_settings = get_app_settings()
        self.export_root = Path(export_root) if export_root is not None else app_settings.export_dir
        self.export_root.mkdir(parents=True, exist_ok=True)

    def _export_single_item(  # noqa: PLR0913
        self,
        idx: int,
        item: dict[str, Any],
        slug: str,
        ext: str,
        export_dir: Path,
        cfg: dict[str, Any],
    ) -> dict[str, Any] | None:
        cap_status = str(item.get("caption_status") or "uncaptioned")
        raw_caption = str(item.get("active_caption") or "").strip()
        item_tags = _extract_item_tags(item)

        if not raw_caption:
            if cap_status == "needs_manual_caption" or not cfg["include_uncaptioned_stubs"]:
                return None
            tag_clause = ", ".join(item_tags) if item_tags else "studio scene"
            raw_caption = f"Photograph of a subject, {tag_clause}."

        final_caption, _ = apply_trigger_word(
            raw_caption,
            trigger_word=cfg["eff_trigger"],
            position=cfg["eff_pos"],
        )

        raw_img = _load_item_bgr(item)
        if raw_img is None:
            return None

        norm_img = normalize_export_image(
            raw_img,
            crop_geometry=item.get("crop_geometry"),
            max_edge=cfg["max_edge"],
            snap_multiple_16=cfg["snap_multiple_16"],
            normalize_buckets=cfg["normalize_buckets"],
            buckets=cfg["active_buckets"],
        )
        out_h, out_w = norm_img.shape[:2]

        stem = f"{slug}_{idx:04d}"
        img_filename = f"{stem}.{ext}"
        txt_filename = f"{stem}.txt"
        _write_exported_image(export_dir / img_filename, norm_img, ext)
        (export_dir / txt_filename).write_text(final_caption, encoding="utf-8")

        return {
            "file_name": img_filename,
            "caption_file": txt_filename,
            "text": final_caption,
            "width": out_w,
            "height": out_h,
            "bucket": f"{out_w}x{out_h}",
            "tags": item_tags,
            "quality_score": round(float(item.get("quality_score") or 0.0), 4),
            "media_id": int(item["id"]),
            "source_filename": item.get("source_filename"),
            "timestamp_s": item.get("timestamp_s"),
            "caption_model": item.get("active_caption_model"),
            "cascade_layer": str(item.get("active_caption_layer") or "manual"),
        }

    def export_dataset(  # noqa: PLR0913
        self,
        dataset_id: str,
        *,
        image_format: Literal["png", "jpg"] = "png",
        max_edge: int = 2048,
        snap_multiple_16: bool = True,
        normalize_buckets: bool = True,
        bucket_preset: str = "4_buckets",
        trigger_word: str | None = None,
        trigger_position: TriggerPosition | None = None,
        create_zip: bool = True,
        include_uncaptioned_stubs: bool = True,
        output_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """Export approved items into normalized images, `.txt` files, `metadata.jsonl`, `dataset_card.json`, and `.zip`."""
        dataset = self.db.get_dataset(dataset_id)
        if dataset is None:
            raise ValueError(f"Dataset '{dataset_id}' not found")

        ds_id = dataset["id"]
        slug = _resolve_dataset_slug(dataset, ds_id)
        eff_trigger, eff_pos = _resolve_trigger_config(dataset, trigger_word, trigger_position)
        ext, export_dir = _resolve_export_format_and_dir(image_format, output_dir, self.export_root, slug)
        active_buckets = resolve_buckets(bucket_preset)

        items, _ = self.db.list_media_items(
            ds_id, curation_state="keep,star", include_duplicates=False, limit=10000
        )

        cfg: dict[str, Any] = {
            "eff_trigger": eff_trigger,
            "eff_pos": eff_pos,
            "max_edge": max_edge,
            "snap_multiple_16": snap_multiple_16,
            "normalize_buckets": normalize_buckets,
            "active_buckets": active_buckets,
            "include_uncaptioned_stubs": include_uncaptioned_stubs,
        }

        manifest_records = [
            rec
            for idx, item in enumerate(items, start=1)
            if (rec := self._export_single_item(idx, item, slug, ext, export_dir, cfg)) is not None
        ]
        exported_count = len(manifest_records)

        metadata_jsonl_path = export_dir / "metadata.jsonl"
        metadata_jsonl_path.write_text(
            "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in manifest_records),
            encoding="utf-8",
        )

        dataset_card = {
            "dataset_id": ds_id,
            "name": dataset.get("name"),
            "slug": slug,
            "source_type": dataset.get("source_type"),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_exported": exported_count,
            "image_format": ext,
            "max_edge": max_edge,
            "snap_multiple_16": snap_multiple_16,
            "normalize_buckets": normalize_buckets,
            "bucket_preset": bucket_preset,
            "max_buckets": MAX_DATASET_BUCKETS,
            "target_buckets": [f"{bw}x{bh}" for bw, bh in active_buckets],
            "trigger_word": eff_trigger,
            "trigger_position": eff_pos,
            "active_skill_id": dataset.get("active_skill_id"),
            **_summarize_manifest_records(manifest_records),
        }
        dataset_card_path = export_dir / "dataset_card.json"
        dataset_card_path.write_text(json.dumps(dataset_card, indent=2, ensure_ascii=False), encoding="utf-8")

        zip_path = _create_export_zip(self.export_root, export_dir, slug) if create_zip else None
        self.db.update_dataset(ds_id, status="exported")
        zip_str = str(zip_path) if zip_path else None
        dl_url = f"/api/v1/datasets/{ds_id}/export/download" if zip_str else None

        return {
            "dataset_id": ds_id,
            "exported_count": exported_count,
            "export_dir": str(export_dir),
            "metadata_jsonl_path": str(metadata_jsonl_path),
            "dataset_card_path": str(dataset_card_path),
            "dataset_card": dataset_card,
            "zip_path": zip_str,
            "zip_download_url": dl_url,
        }

    def get_export_zip_path(self, dataset_id: str) -> Path:
        """Locate or build the `.zip` archive for a dataset."""
        dataset = self.db.get_dataset(dataset_id)
        if dataset is None:
            raise FileNotFoundError(f"Dataset '{dataset_id}' not found")
        slug = slugify_text(dataset.get("slug") or dataset.get("name") or dataset["id"])
        zip_path = self.export_root / f"{slug}.zip"
        if not zip_path.is_file():
            self.export_dataset(dataset["id"], create_zip=True)
        if not zip_path.is_file():
            raise FileNotFoundError(f"Export archive not found at {zip_path}")
        return zip_path
