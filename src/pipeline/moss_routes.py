"""FastAPI router for MOSS-Image Multi-Tier Captioning, Disentanglement, and Leakage Sanitization."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pipeline.config import Settings, get_settings
from pipeline.db import DB
from pipeline.moss_openrouter import (
    MossCaptionOptions,
    MossCascadeExhaustedError,
    MossOpenRouterClient,
)
from pipeline.moss_validator import (
    detect_refusal,
    extract_json_object,
    sanitize_leaks,
    scan_trait_leakage,
    validate_and_inject_triggers,
)


class MossCaptionFrameRequest(BaseModel):
    mode: str = "character"
    class_noun: str = "woman"
    locked_traits: list[str] = Field(default_factory=list)
    trigger_phrase: str | None = None
    training_mode: str | None = None
    auto_sanitize: bool = False
    api_key: str | None = None
    primary_model: str | None = None
    fallback_1: str | None = None
    fallback_2: str | None = None
    save_to_db: bool = False


class SanitizeLeaksRequest(BaseModel):
    text: str | None = None
    caption: dict[str, str] | None = None
    locked_traits: list[str] = Field(default_factory=list)
    frame_id: int | None = None
    update_db: bool = False


class ValidateCaptionRequest(BaseModel):
    caption: dict[str, Any] | str
    locked_traits: list[str] = Field(default_factory=list)
    trigger_phrase: str | None = None
    class_noun: str | None = None


def _extract_word_counts(cap_obj: dict[str, Any]) -> dict[str, int]:
    """Helper to compute tier word counts."""
    return {k: len(str(cap_obj.get(k) or "").split()) for k in ("long", "medium", "short")}


def _resolve_frame_file(db: DB | None, frame_id: int) -> tuple[dict[str, Any] | None, Path]:
    """Resolves database record and local file path for a frame id."""
    if not db:
        raise HTTPException(status_code=500, detail="Database instance not configured")
    frame = db.one("SELECT id, video_id, preview_path, full_path FROM frames WHERE id=?", (frame_id,))
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame {frame_id} not found")

    raw_path = frame.get("full_path") or frame.get("preview_path") or ""
    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Frame image file not found on disk: {raw_path}")
    return frame, path


def _persist_caption(
    db: DB | None,
    frame_id: int,
    video_id: str | None,
    caption_res: Any,
    opts: MossCaptionOptions,
) -> None:
    """Stores generated MOSS long caption into captions table."""
    if not db or not video_id:
        return
    with db.write() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO captions (
                frame_id, video_id, text, model, layer, mode, prompt_version,
                trigger, word_count, created_at, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'moss')""",
            (
                frame_id,
                video_id,
                caption_res.caption.long,
                caption_res.model_name,
                caption_res.cascade_tier,
                opts.mode,
                caption_res.prompt_version,
                opts.trigger_phrase,
                len(caption_res.caption.long.split()),
                time.time(),
            ),
        )


def _sanitize_dict_tiers(caption_dict: dict[str, str], traits: list[str]) -> dict[str, str]:
    """Sanitizes each tier in a caption dictionary."""
    return {tier: sanitize_leaks(text, traits) for tier, text in caption_dict.items()}


def _sanitize_db_frame(db: DB, frame_id: int, traits: list[str]) -> int:
    """Sanitizes text in DB captions for a single frame id."""
    row = db.one("SELECT text FROM captions WHERE frame_id=?", (frame_id,))
    if not row or not row.get("text"):
        return 0
    clean = sanitize_leaks(str(row["text"]), traits)
    with db.write() as conn:
        conn.execute(
            "UPDATE captions SET text=?, word_count=? WHERE frame_id=?",
            (clean, len(clean.split()), frame_id),
        )
    return 1


def _sanitize_db_dataset(db: DB, video_id: str, traits: list[str]) -> int:
    """Sanitizes text in DB captions for all frames in a video dataset."""
    rows = db.all("SELECT frame_id, text FROM captions WHERE video_id=?", (video_id,))
    count = 0
    with db.write() as conn:
        for r in rows:
            fid = r.get("frame_id")
            text = r.get("text")
            if fid and text:
                clean = sanitize_leaks(str(text), traits)
                conn.execute(
                    "UPDATE captions SET text=?, word_count=? WHERE frame_id=?",
                    (clean, len(clean.split()), fid),
                )
                count += 1
    return count


def _parse_caption_input(raw: dict[str, Any] | str) -> dict[str, Any]:
    """Ensures caption payload is a dictionary structure containing a 'caption' dict."""
    if isinstance(raw, dict):
        if "caption" in raw and isinstance(raw["caption"], dict):
            return raw
        return {"caption": raw}
    try:
        data = extract_json_object(raw)
        if "caption" in data and isinstance(data["caption"], dict):
            return data
        return {"caption": data}
    except Exception:
        return {"caption": {"long": raw, "medium": "", "short": ""}}


def _handle_sanitize_logic(
    req: SanitizeLeaksRequest,
    dataset_id: str | None,
    db: DB | None,
) -> dict[str, Any]:
    """Core logic for sanitizing leaks on text, captions, or database entries."""
    sanitized_text = sanitize_leaks(req.text, req.locked_traits) if req.text else None
    sanitized_caption = _sanitize_dict_tiers(req.caption, req.locked_traits) if req.caption else None
    updated_db_count = 0

    if req.update_db and db:
        if req.frame_id is not None:
            updated_db_count += _sanitize_db_frame(db, req.frame_id, req.locked_traits)
        elif dataset_id:
            updated_db_count += _sanitize_db_dataset(db, dataset_id, req.locked_traits)

    return {
        "status": "ok",
        "sanitized_text": sanitized_text,
        "sanitized_caption": sanitized_caption,
        "locked_traits": req.locked_traits,
        "updated_db_count": updated_db_count,
    }


def _handle_validate_logic(req: ValidateCaptionRequest) -> dict[str, Any]:
    """Core validation logic for MOSS captions."""
    caption_payload = _parse_caption_input(req.caption)
    is_refusal, ref_reason = detect_refusal(caption_payload)
    leaked = scan_trait_leakage(caption_payload, req.locked_traits)
    updated, warnings = validate_and_inject_triggers(caption_payload, req.trigger_phrase, req.class_noun)

    cap_obj = updated.get("caption") if isinstance(updated.get("caption"), dict) else updated
    word_counts = _extract_word_counts(cap_obj)
    is_valid = (not is_refusal) and (len(leaked) == 0) and (len(warnings) == 0)

    return {
        "is_valid": is_valid,
        "is_refusal": is_refusal,
        "refusal_reason": ref_reason,
        "leaked_traits": leaked,
        "hierarchy_warnings": warnings,
        "caption": cap_obj,
        "word_counts": word_counts,
    }


def build_moss_router(db: DB | None = None, settings: Settings | None = None) -> APIRouter:
    """Builds and returns the APIRouter for MOSS-Image endpoints."""
    router = APIRouter(tags=["moss"])
    cfg = settings or get_settings()
    client_instance = MossOpenRouterClient(settings=cfg)

    @router.post("/api/moss/caption/frame/{frame_id}")
    async def caption_frame_endpoint(frame_id: int, req: MossCaptionFrameRequest):
        frame, file_path = _resolve_frame_file(db, frame_id)
        opts = MossCaptionOptions(
            mode=req.mode,
            class_noun=req.class_noun,
            locked_traits=req.locked_traits,
            trigger_phrase=req.trigger_phrase,
            training_mode=req.training_mode,
            auto_sanitize=req.auto_sanitize,
            primary_model=req.primary_model,
            fallback_1=req.fallback_1,
            fallback_2=req.fallback_2,
        )

        async with httpx.AsyncClient(timeout=65.0) as http_client:
            try:
                res = await client_instance.caption_image(
                    client=http_client,
                    image_input=file_path,
                    api_key=req.api_key,
                    options=opts,
                )
            except MossCascadeExhaustedError as ex:
                raise HTTPException(status_code=502, detail=str(ex)) from ex
            except Exception as ex:
                raise HTTPException(status_code=500, detail=f"Caption generation error: {ex}") from ex

        if req.save_to_db and db and frame:
            _persist_caption(db, frame_id, frame.get("video_id"), res, opts)

        return {
            "frame_id": frame_id,
            "video_id": frame.get("video_id") if frame else None,
            "caption": res.caption.model_dump(),
            "watermark": [w.model_dump() for w in res.watermark],
            "borders": res.borders,
            "model_used": res.model_used,
            "model_name": res.model_name,
            "cascade_tier": res.cascade_tier,
            "leaked_traits": res.leaked_traits,
            "warnings": res.warnings,
            "prompt_version": res.prompt_version,
        }

    @router.post("/api/datasets/{dataset_id}/sanitize-leaks")
    @router.post("/api/moss/sanitize-leaks")
    def sanitize_leaks_endpoint(req: SanitizeLeaksRequest, dataset_id: str | None = None):
        return _handle_sanitize_logic(req, dataset_id, db)

    @router.post("/api/moss/validate-caption")
    def validate_caption_endpoint(req: ValidateCaptionRequest):
        return _handle_validate_logic(req)

    return router


__all__ = [
    "MossCaptionFrameRequest",
    "SanitizeLeaksRequest",
    "ValidateCaptionRequest",
    "build_moss_router",
]
