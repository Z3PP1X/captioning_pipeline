"""Multi-line dataset exporter for MOSS-Image."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2

from pipeline.db import DB
from pipeline.geometry.cropper import apply_moss_crop, compute_optimal_moss_crop

CAPTION_SEPARATOR = "---===---"


def _format_caption_tier(trigger: str, text: str) -> str:
    """Ensure trigger phrase prefixes the caption line if present."""
    clean_text = (text or "").strip()
    clean_trig = (trigger or "").strip()
    if not clean_trig:
        return clean_text
    if clean_text.lower().startswith(clean_trig.lower()):
        return clean_text
    return f"{clean_trig}, {clean_text}" if clean_text else clean_trig


def _process_frame_image(
    row: dict[str, Any],
    output_dir: Path,
    stem: str,
    target_format: str,
) -> tuple[dict[str, Any] | None, Path | None]:
    """Load, crop, and write the frame image if available."""
    img_path = Path(row["full_path"]) if row.get("full_path") else (
        Path(row["preview_path"]) if row.get("preview_path") else None
    )
    if not img_path or not img_path.is_file():
        return row.get("applied_crop") or row.get("suggested_crop"), None

    img = cv2.imread(str(img_path))
    if img is None or img.size == 0:
        return row.get("applied_crop") or row.get("suggested_crop"), None

    crop_info = row.get("applied_crop") or row.get("suggested_crop")
    if crop_info and isinstance(crop_info, dict) and "crop_box" in crop_info:
        crop_box = crop_info["crop_box"]
        target_bucket = crop_info.get("target_bucket")
        img = apply_moss_crop(img, crop_box, target_bucket)
    else:
        h, w = img.shape[:2]
        crop_info = compute_optimal_moss_crop(w, h, row.get("watermark") or [], row.get("borders") or [])
        img = apply_moss_crop(img, crop_info["crop_box"], crop_info.get("target_bucket"))

    out_img_path = output_dir / f"{stem}.{target_format}"
    cv2.imwrite(str(out_img_path), img)
    return crop_info, out_img_path


def _write_single_frame_files(
    row: dict[str, Any],
    tags: list[str],
    output_dir: Path,
    target_format: str,
) -> list[Path]:
    """Write image, multiline txt, and sidecar json for one frame."""
    stem = Path(row["full_path"]).stem if row.get("full_path") else f"frame_{row['id']:06d}"
    crop_info, out_img_path = _process_frame_image(row, output_dir, stem, target_format)

    created_files: list[Path] = []
    if out_img_path:
        created_files.append(out_img_path)

    trigger = str(row.get("c_trigger") or row.get("v_trigger") or "")
    caption_long = str(row.get("caption_long") or row.get("text") or "")
    caption_medium = str(row.get("caption_medium") or caption_long)
    caption_short = str(row.get("caption_short") or caption_medium)

    long_line = _format_caption_tier(trigger, caption_long)
    med_line = _format_caption_tier(trigger, caption_medium)
    short_line = _format_caption_tier(trigger, caption_short)

    txt_content = f"{long_line}\n{CAPTION_SEPARATOR}\n{med_line}\n{CAPTION_SEPARATOR}\n{short_line}\n"
    txt_path = output_dir / f"{stem}.txt"
    txt_path.write_text(txt_content, encoding="utf-8")
    created_files.append(txt_path)

    sidecar = {
        "frame_id": int(row["id"]),
        "video_id": row.get("video_id"),
        "trigger_phrase": trigger,
        "captions": {
            "long": long_line,
            "medium": med_line,
            "short": short_line,
        },
        "watermark": row.get("watermark"),
        "borders": row.get("borders"),
        "crop": crop_info,
        "model_used": row.get("model_used"),
        "tags": tags,
        "timestamp": float(row.get("t") or 0.0),
    }
    json_path = output_dir / f"{stem}_caption.json"
    json_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    created_files.append(json_path)

    return created_files


def _write_moss_toml(output_dir: Path) -> Path:
    toml_path = output_dir / "moss_dataset_config.toml"
    toml_content = (
        "[dataset]\n"
        "enable_multiline_caption = true\n"
        f'caption_separator = "{CAPTION_SEPARATOR}"\n'
        'target_architecture = "MOSS_IMAGE"\n'
        "bucket_step = 64\n"
        "target_area = 1048576\n"
    )
    toml_path.write_text(toml_content, encoding="utf-8")
    return toml_path


def export_moss_dataset(
    db: DB,
    video_id_or_filter: str | None,
    output_dir: Path,
    target_format: str = "png",
) -> dict[str, Any]:
    """Export dataset in MOSS-Image multi-line caption and bucket format."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    where_clause = "WHERE f.video_id = ?" if video_id_or_filter else ""
    args = (video_id_or_filter,) if video_id_or_filter else ()
    sql = f"""
        SELECT f.id, f.video_id, f.t, f.full_path, f.preview_path,
               v.slug, v.trigger_phrase as v_trigger,
               c.text, c.trigger as c_trigger,
               c.caption_long, c.caption_medium, c.caption_short,
               c.watermark, c.borders, c.suggested_crop, c.applied_crop,
               c.model_used, c.review_status, c.created_at as caption_created_at
        FROM frames f
        LEFT JOIN captions c ON f.id = c.frame_id
        LEFT JOIN videos v ON f.video_id = v.id
        {where_clause}
        ORDER BY f.video_id ASC, f.t ASC
    """
    rows = db.all(sql, args)
    all_files: list[Path] = []

    for row in rows:
        tag_rows = db.all(
            "SELECT it.name FROM frame_tags ft JOIN image_tags it ON ft.tag_id = it.id WHERE ft.frame_id = ?",
            (row["id"],),
        )
        tags = [tr["name"] for tr in tag_rows]
        files = _write_single_frame_files(row, tags, out_dir, target_format)
        all_files.extend(files)

    toml_path = _write_moss_toml(out_dir)
    all_files.append(toml_path)

    return {
        "exported_count": len(rows),
        "output_dir": str(out_dir),
        "files": [str(p) for p in all_files],
    }
