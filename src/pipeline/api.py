"""FastAPI web application and REST API for the video frame captioning pipeline."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.autochunk import PRIORITIES, AutoChunker, DestructiveRechunkError
from pipeline.autopilot import AutopilotConfig
from pipeline.config import (
    DEFAULT_USER_SETTINGS,
    VIDEO_STATUSES,
    Settings,
    get_settings,
    merged_user_settings,
    validate_user_settings,
)
from pipeline.db import DB
from pipeline.decode import ensure_native_frame, sample_frames_at
from pipeline.export import Exporter
from pipeline.face import person_embedding
from pipeline.indexer import Indexer
from pipeline.jobs import JobConflictError, JobManager
from pipeline.learned_weights import (
    NotEnoughEvents,
    active_learned_weights,
    fit_learned_weights,
    learned_status,
    log_curation_events,
    state_action,
)
from pipeline.moss_routes import build_moss_router
from pipeline.openrouter import DEFAULT_CAPTION_MAX_TOKENS
from pipeline.rag import (
    CHARACTERS,
    CURATED_PRESETS,
    TAXONOMY_CATEGORIES,
    RagIndexJob,
    RagIndexNotReady,
    RagPromptGenerator,
    RagRetriever,
    RagUpstreamError,
    expand_moss_prompt,
    index_moss_caption,
    retrieve_moss_exemplars,
)
from pipeline.profiles import PROFILES, apply_character_profile, caption_mode_for, video_profile
from pipeline.reconcile import get_video_scenes_caption_stats, reconcile_captions_and_state
from pipeline.select import select_diverse_top_k
from pipeline.streaming import stream_video
from pipeline.tag_routes import build_tag_router

settings = get_settings()
db = DB(settings.db_path)
indexer = Indexer(db, settings)
jobs = JobManager(db, settings)
exporter = Exporter(db, settings)
autochunker = AutoChunker(db, settings)
# RAG shares the CLIP text model with the job manager (one copy in memory)
rag_retriever = RagRetriever(settings, classifier=jobs.classifier)
rag_generator = RagPromptGenerator(settings)
rag_index_job = RagIndexJob(settings, classifier=jobs.classifier, on_finished=lambda _res: rag_retriever.invalidate())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure dirs and initial video scan on startup
    settings.ensure_dirs()
    # Clean up any orphaned jobs left in running/queued state across server restarts
    with db.write() as conn:
        conn.execute(
            "UPDATE jobs SET status='error', error='Server restarted while job was running', finished_at=? WHERE status IN ('running', 'queued')",
            (time.time(),),
        )
    # An autopilot run whose job died with the server must not stay "running"
    jobs.autopilot.heal_stale()
    # Sync disk captions and auto-heal stuck statuses
    reconcile_captions_and_state(db, settings)
    indexer.scan_and_sync()
    yield


app = FastAPI(title="Captioning Pipeline API", version="0.1.0", lifespan=lifespan)

# The UI is same-origin (served below, or proxied by `ng serve`). Only the dev server origin is allowed
# cross-origin; a wildcard would let any website in the browser start paid jobs or change settings.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Learn-as-you-tag image tagging (registered before the SPA catch-all at the end of this module)
app.include_router(build_tag_router(jobs.image_tags, jobs))
app.include_router(build_moss_router(db, settings))


@app.exception_handler(JobConflictError)
def job_conflict_handler(request: Request, exc: JobConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ----------------------------------------------------------------------------- Models
class VideoPatch(BaseModel):
    status: str | None = None
    watermark_rect: dict[str, float] | None = None
    letterbox: dict[str, float] | None = None
    # LoRA profile (HANDOFF decision 1): 'concept' | 'character' (identity gate + solo crops + character captions)
    profile: str | None = None


class ChunkItem(BaseModel):
    id: int | None = None
    position: int = 0
    start_s: float
    end_s: float
    k: int = 5
    budget_pct: float = 0.0
    caption_model: str = "google/gemma-4-31b-it"
    rule: str = "person"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    # Autopilot review: how much of the frame budget the scene deserves, and how much the target person shows
    priority: str = "normal"
    person_presence: float | None = None
    face_presence: float | None = None


class ChunkPatch(BaseModel):
    caption_model: str | None = None
    budget_pct: float | None = None
    rule: str | None = None
    tags: list[str] | None = None
    notes: str | None = None


class ChunksUpdate(BaseModel):
    chunks: list[ChunkItem]
    # Required to delete chunks that hold picks/exports (the delete cascades to frames, picks and exports)
    confirm_destructive: bool = False


class SuggestChunksRequest(BaseModel):
    min_len_s: float = 4.0
    k: int = 5
    rule: str = "person"
    confirm_destructive: bool = False


class JobCreate(BaseModel):
    video_id: str
    chunk_id: int | None = None


class PickPatch(BaseModel):
    state: str | None = None  # keep, reject, star, candidate
    role: str | None = None   # pick, runner_up
    crop: dict[str, float] | None = None
    # Demoting a pick to runner_up with rebalance=true promotes the best replacement of its chunk (like swap-frame)
    rebalance: bool = False


class CandidateStateRequest(BaseModel):
    state: str | None = None


class OpenRouterCaptionRequest(BaseModel):
    api_key: str | None = None
    model: str = "google/gemma-4-31b-it"
    fallback_layer2: str = "deepseek/deepseek-v4.1-flash"
    fallback_layer3: str = "x-ai/grok-4.7"
    # "none" by default: reasoning shares max_tokens with the caption and truncated it (review C3)
    reasoning_effort: str = "none"
    use_chunk_models: bool = True
    # None = from the video's profile ('character' profile -> 'character', else the openrouter_mode setting)
    mode: str | None = None
    trigger: str = "restrained_elegance"
    class_noun: str = "woman"
    max_tokens: int = DEFAULT_CAPTION_MAX_TOKENS
    # 0.3-0.5 keeps captions consistent across a dataset (Phase G); was 0.80
    temperature: float = 0.4
    top_p: float = 0.90


class SwapFrameRequest(BaseModel):
    drop_frame_id: int | None = None
    deselected_frame_id: int | None = None
    pick_frame_id: int | None = None

    @property
    def target_drop_id(self) -> int:
        if self.drop_frame_id is not None:
            return self.drop_frame_id
        if self.deselected_frame_id is not None:
            return self.deselected_frame_id
        raise ValueError("Must provide either drop_frame_id or deselected_frame_id")


class PersonCreate(BaseModel):
    name: str


class PersonPatch(BaseModel):
    name: str | None = None


class VideoTargetPersonRequest(BaseModel):
    person_id: int | None = None


class TagFaceRequest(BaseModel):
    frame_id: int
    person_name: str | None = None
    person_id: int | None = None
    face_index: int = 0


class AutoExtractRequest(BaseModel):
    target_person_id: int | None = None


class AutopilotApproveRequest(BaseModel):
    # chunk id -> skip | low | normal | high; applied before extraction (budgets are re-derived from them)
    priorities: dict[int, str] | None = None


class AutopilotPrioritiesRequest(BaseModel):
    priorities: dict[int, str]


class AutopilotRefineRequest(BaseModel):
    dup_similarity: float | None = Field(default=None, ge=0.5, le=1.0)
    person_focus: float | None = Field(default=None, ge=0.0, le=1.0)


class AutopilotBatchRequest(BaseModel):
    video_ids: list[str]
    config: AutopilotConfig = Field(default_factory=AutopilotConfig)
    # video id -> config fields that replace the shared ones for that video (typically exclude_ranges)
    per_video: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PersonEnrollRequest(BaseModel):
    name: str
    video_id: str
    time_s: float | None = None
    times: list[float] | None = None
    person_id: int | None = None
    face_index: int = 0  # 0 = the most prominent face in the frame


# ----------------------------------------------------------------------------- Videos
@app.get("/api/videos")
def list_videos(status: str | None = None, search: str | None = None):
    query = """
        SELECT v.*,
            (SELECT COUNT(*) FROM chunks c WHERE c.video_id = v.id) as chunk_count,
            (SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id = f.id WHERE f.video_id = v.id) as export_count,
            (SELECT COUNT(*) FROM frames f JOIN picks p ON p.frame_id = f.id WHERE f.video_id = v.id AND (p.state IN ('keep', 'star') OR (p.role = 'pick' AND (p.state IS NULL OR p.state != 'reject')))) as kept_count,
            (SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id = f.id WHERE f.video_id = v.id) as captioned_count
        FROM videos v
        WHERE v.missing = 0
    """
    params: list[Any] = []

    if status:
        if status in ("curating", "curating_300"):
            query += " AND v.status IN ('curating', 'curating_300')"
        elif status in ("extracting", "extracting_750"):
            query += " AND v.status IN ('extracting', 'extracting_750')"
        else:
            query += " AND v.status = ?"
            params.append(status)

    if search:
        query += " AND (v.name LIKE ? OR v.slug LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    query += " ORDER BY v.name ASC"
    videos = db.all(query, tuple(params))
    from pipeline.indexer import compute_video_title_hash
    for v in videos:
        if not v.get("title_hash") and v.get("name"):
            v["title_hash"] = compute_video_title_hash(v["name"])
        v_persons = db.get_video_persons(v["id"])
        v["positive_persons"] = v_persons.get("positive", [])
        v["negative_persons"] = v_persons.get("negative", [])
        v["positive_person_ids"] = [p["id"] for p in v["positive_persons"]]
        v["negative_person_ids"] = [p["id"] for p in v["negative_persons"]]
        # The full run (report, swap list) is fetched per video; the list only needs the stage
        ap = v.get("autopilot") if isinstance(v.get("autopilot"), dict) else None
        v["autopilot_stage"] = ap.get("stage") if ap else None
        v["autopilot"] = {"stage": ap["stage"], "message": ap.get("message", "")} if ap else None
    return {"videos": videos, "total": len(videos)}


@app.post("/api/videos/scan")
@app.post("/api/videos/rescan")
def scan_videos():
    sync_stats = reconcile_captions_and_state(db, settings)
    vids = indexer.scan_and_sync()
    return {"scanned": len(vids), "sync_stats": sync_stats}


@app.post("/api/videos/sync-captions")
def sync_captions_endpoint():
    sync_stats = reconcile_captions_and_state(db, settings)
    return {"status": "ok", "sync_stats": sync_stats}


@app.get("/api/videos/next")
def get_next_video():
    """Find the first unprocessed video (new, not broken)."""
    vid = db.one(
        """
        SELECT v.* FROM videos v
        WHERE v.missing = 0 AND v.broken = 0 AND v.status = 'new'
        ORDER BY v.name ASC LIMIT 1
        """
    )
    if not vid:
        # Fallback to chunked or extracting
        vid = db.one(
            """
            SELECT v.* FROM videos v
            WHERE v.missing = 0 AND v.broken = 0 AND v.status IN ('chunked', 'extracting')
            ORDER BY v.name ASC LIMIT 1
            """
        )
    return {"video": vid}


@app.get("/api/videos/{video_id}")
def get_video(video_id: str):
    vid = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    if not vid.get("title_hash") and vid.get("name"):
        from pipeline.indexer import compute_video_title_hash
        vid["title_hash"] = compute_video_title_hash(vid["name"])
    v_persons = db.get_video_persons(video_id)
    vid["positive_persons"] = v_persons.get("positive", [])
    vid["negative_persons"] = v_persons.get("negative", [])
    vid["positive_person_ids"] = [p["id"] for p in vid["positive_persons"]]
    vid["negative_person_ids"] = [p["id"] for p in vid["negative_persons"]]
    return {"video": vid}


@app.patch("/api/videos/{video_id}")
def patch_video(video_id: str, patch: VideoPatch):
    vid = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    updates = []
    params = []
    if patch.status is not None:
        if patch.status not in VIDEO_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{patch.status}'. Allowed statuses: {list(VIDEO_STATUSES)}"
            )
        updates.append("status = ?")
        params.append(patch.status)
    if patch.watermark_rect is not None:
        updates.append("watermark_rect = ?")
        params.append(json.dumps(patch.watermark_rect))
    if patch.letterbox is not None:
        updates.append("letterbox = ?")
        params.append(json.dumps(patch.letterbox))
    if patch.profile is not None:
        if patch.profile not in PROFILES:
            raise HTTPException(status_code=400, detail=f"profile must be one of {list(PROFILES)}")
        updates.append("profile = ?")
        params.append(patch.profile)

    if updates:
        params.append(video_id)
        with db.write() as conn:
            conn.execute(f"UPDATE videos SET {', '.join(updates)} WHERE id = ?", tuple(params))

    out: dict[str, Any] = {"video": db.one("SELECT * FROM videos WHERE id=?", (video_id,))}
    if patch.profile == "character":
        out["profile_result"] = apply_character_profile(db, video_id)
    return out


@app.post("/api/videos/{video_id}/apply-profile")
def apply_profile_endpoint(video_id: str):
    """Re-run the character profile (identity gate + solo crops), e.g. after tagging more faces."""
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    jobs._assert_no_active_job(video_id)
    return {"video_id": video_id, "profile": video_profile(db, video_id), **apply_character_profile(db, video_id)}


@app.get("/api/videos/{video_id}/stream")
def stream_video_file(video_id: str, request: Request):
    vid = db.one("SELECT path FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    return stream_video(Path(vid["path"]), request)


@app.get("/api/videos/{video_id}/filmstrip")
def get_filmstrip(video_id: str):
    data = indexer.get_or_build_filmstrip(video_id)
    return data


@app.get("/api/videos/{video_id}/filmstrip/{filename}")
def get_filmstrip_thumb(video_id: str, filename: str):
    root = settings.filmstrip_dir.resolve()
    p = (root / video_id / filename).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(p, media_type="image/webp")


# ----------------------------------------------------------------------------- Persons & Face Recognition
def _enrich_person_dict(p: dict[str, Any]) -> dict[str, Any]:
    p["face_crop_url"] = f"/api/persons/{p['id']}/crop" if p.get("face_crop_path") else None
    p["has_embedding"] = bool(p.get("embedding"))
    p.pop("embedding", None)
    return p


@app.get("/api/persons")
def list_persons_endpoint():
    rows = db.list_persons()
    for p in rows:
        _enrich_person_dict(p)
    return {"persons": rows, "total": len(rows)}


@app.post("/api/persons")
def create_person_endpoint(req: PersonCreate):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    # Initial empty person with zero embedding
    person = db.create_or_update_person(name, embedding=np.zeros(128, dtype=np.float32))
    return {"person": _enrich_person_dict(person)}


@app.get("/api/persons/{person_id}")
def get_person_endpoint(person_id: int):
    p = db.get_person(person_id)
    if not p:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"person": _enrich_person_dict(p)}


@app.patch("/api/persons/{person_id}")
def patch_person_endpoint(person_id: int, req: PersonPatch):
    p = db.get_person(person_id)
    if not p:
        raise HTTPException(status_code=404, detail="Person not found")
    if req.name is not None and req.name.strip():
        with db.write() as conn:
            conn.execute("UPDATE persons SET name=?, updated_at=? WHERE id=?", (req.name.strip(), time.time(), person_id))
            conn.execute("UPDATE videos SET target_person_name=? WHERE target_person_id=?", (req.name.strip(), person_id))
    return {"person": _enrich_person_dict(db.get_person(person_id))}


@app.delete("/api/persons/{person_id}")
def delete_person_endpoint(person_id: int):
    ok = db.delete_person(person_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"status": "ok", "deleted": True}


@app.get("/api/persons/{person_id}/crop")
def get_person_crop_endpoint(person_id: int):
    p = db.get_person(person_id)
    if not p or not p.get("face_crop_path") or not Path(p["face_crop_path"]).is_file():
        raise HTTPException(status_code=404, detail="Face crop not found")
    return FileResponse(Path(p["face_crop_path"]), media_type="image/jpeg")


class VideoPersonRoleRequest(BaseModel):
    role: str = Field(default="positive")


def _re_evaluate_video_faces(video_id: str) -> dict[str, Any]:
    video_persons = db.get_video_persons(video_id)
    pos_ids = [p["id"] for p in video_persons.get("positive", [])]
    neg_ids = [p["id"] for p in video_persons.get("negative", [])]

    from pipeline.face import person_embedding
    pos_embeddings = [person_embedding(db.get_person(pid)) for pid in pos_ids]
    pos_embeddings = [p for p in pos_embeddings if p is not None]
    neg_embeddings = [person_embedding(db.get_person(pid)) for pid in neg_ids]
    neg_embeddings = [n for n in neg_embeddings if n is not None]

    frames = db.all(
        "SELECT id, preview_path, full_path, metrics, zs_scores FROM frames WHERE video_id=?",
        (video_id,),
    )
    if not frames:
        return {"updated_frames": 0, "pos_matches": 0, "neg_matches": 0, "excluded": 0, "demoted_picks": 0}

    updated_count = 0
    pos_matches = 0
    neg_matches = 0
    excluded_count = 0
    demoted_picks = []

    with db.write() as conn:
        for f in frames:
            img_path = Path(f.get("preview_path") or f.get("full_path") or "")
            if not img_path.is_file():
                continue
            img_bgr = cv2.imread(str(img_path))
            if img_bgr is None:
                continue

            metrics = f.get("metrics")
            if isinstance(metrics, str):
                try:
                    metrics = json.loads(metrics)
                except Exception:
                    metrics = {}
            metrics = metrics or {}

            zs = f.get("zs_scores")
            if isinstance(zs, str):
                try:
                    zs = json.loads(zs)
                except Exception:
                    zs = {}
            zs = zs or {}

            if pos_embeddings or neg_embeddings:
                match_res = jobs.face_engine.match_frame_multi(
                    img_bgr,
                    positive_features=pos_embeddings,
                    negative_features=neg_embeddings,
                )
                metrics["target_person_sim"] = match_res["max_pos_sim"]
                metrics["target_person_matched"] = match_res["pos_matched"]
                metrics["negative_person_matched"] = match_res["neg_matched"]
                metrics["excluded"] = match_res["excluded"]
                metrics["face_count"] = match_res["face_count"]
                metrics["faces"] = match_res["faces"]

                zs["target_person_sim"] = match_res["max_pos_sim"]
                zs["target_person_matched"] = match_res["pos_matched"]
                zs["negative_person_matched"] = match_res["neg_matched"]
                zs["excluded"] = match_res["excluded"]
                zs["face_count"] = match_res["face_count"]
                zs["faces"] = match_res["faces"]

                if match_res["pos_matched"]:
                    pos_matches += 1
                if match_res["neg_matched"]:
                    neg_matches += 1
                if match_res["excluded"]:
                    excluded_count += 1
                    demoted_picks.append(f["id"])
            else:
                metrics["target_person_sim"] = 0.0
                metrics["target_person_matched"] = False
                metrics["negative_person_matched"] = False
                metrics["excluded"] = False
                zs["target_person_sim"] = 0.0
                zs["target_person_matched"] = False
                zs["negative_person_matched"] = False
                zs["excluded"] = False

            conn.execute(
                "UPDATE frames SET metrics=?, zs_scores=? WHERE id=?",
                (json.dumps(metrics), json.dumps(zs), f["id"]),
            )
            updated_count += 1

        if demoted_picks:
            for fid in demoted_picks:
                conn.execute(
                    "UPDATE picks SET role='runner_up', state='reject' WHERE frame_id=?",
                    (fid,),
                )

    if video_profile(db, video_id) == "character":
        apply_character_profile(db, video_id)

    jobs.broadcast_event("faces_re_evaluated", {
        "video_id": video_id,
        "updated_frames": updated_count,
        "pos_matches": pos_matches,
        "neg_matches": neg_matches,
        "excluded": excluded_count,
    })

    return {
        "updated_frames": updated_count,
        "pos_matches": pos_matches,
        "neg_matches": neg_matches,
        "excluded": excluded_count,
        "demoted_picks": len(demoted_picks),
    }


@app.get("/api/videos/{video_id}/persons")
def get_video_persons_endpoint(video_id: str):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"video_id": video_id, **db.get_video_persons(video_id)}


@app.post("/api/videos/{video_id}/persons/{person_id}")
def set_video_person_role_endpoint(video_id: str, person_id: int, req: VideoPersonRoleRequest):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    p = db.get_person(person_id)
    if not p:
        raise HTTPException(status_code=404, detail="Person not found")
    role = req.role.strip().lower()
    if role not in ("positive", "negative", "none"):
        raise HTTPException(status_code=400, detail="Role must be 'positive', 'negative', or 'none'")

    updated_persons = db.set_video_person_role(video_id, person_id, role if role != "none" else None)
    re_eval_res = _re_evaluate_video_faces(video_id)
    return {
        "video_id": video_id,
        "persons": updated_persons,
        "re_evaluation": re_eval_res,
    }


@app.post("/api/videos/{video_id}/re-evaluate-faces")
def re_evaluate_video_faces_endpoint(video_id: str):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    res = _re_evaluate_video_faces(video_id)
    return {"video_id": video_id, **res}


@app.post("/api/videos/{video_id}/target-person")
def set_video_target_person_endpoint(video_id: str, req: VideoTargetPersonRequest):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    updated = db.set_video_target_person(video_id, req.person_id)
    _re_evaluate_video_faces(video_id)
    return {"video": updated}


@app.post("/api/videos/{video_id}/tag-face-from-frame")
def tag_face_from_frame_endpoint(video_id: str, req: TagFaceRequest):
    vid = db.one("SELECT id, name FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    frame = db.one("SELECT * FROM frames WHERE id=? AND video_id=?", (req.frame_id, video_id))
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found for this video")

    # Load image (try native full resolution first, fallback to preview)
    img_path = Path(frame.get("full_path") or frame.get("preview_path") or "")
    if not img_path.is_file():
        raise HTTPException(status_code=400, detail="Frame image file not found on disk")

    img_bgr = cv2.imread(str(img_path))
    if img_bgr is None:
        raise HTTPException(status_code=400, detail="Could not read frame image")

    # Detect faces
    faces = jobs.face_engine.detect_faces(img_bgr)
    if not faces:
        raise HTTPException(status_code=400, detail="No faces detected in this frame to tag")

    idx = req.face_index if 0 <= req.face_index < len(faces) else 0
    face = faces[idx]

    # Extract 128-dim normalized embedding and face crop
    emb = jobs.face_engine.extract_feature(img_bgr, face)
    crop = jobs.face_engine.crop_face(img_bgr, face)

    person_name = (req.person_name or "Person").strip()
    crop_dir = settings.cache_dir / "persons"
    crop_dir.mkdir(parents=True, exist_ok=True)
    temp_crop_name = f"face_{int(time.time()*1000)}.jpg"
    temp_crop_path = crop_dir / temp_crop_name
    cv2.imwrite(str(temp_crop_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

    person = db.create_or_update_person(
        name=person_name,
        embedding=emb,
        face_crop_path=str(temp_crop_path),
        person_id=req.person_id,
    )

    final_crop_path = crop_dir / f"person_{person['id']}.jpg"
    if temp_crop_path.is_file():
        temp_crop_path.replace(final_crop_path)
        with db.write() as conn:
            conn.execute("UPDATE persons SET face_crop_path=? WHERE id=?", (str(final_crop_path), person["id"]))
        person["face_crop_path"] = str(final_crop_path)

    # Link video to this target person
    db.set_video_target_person(video_id, person["id"])

    # Re-evaluate all candidate frames for this video with the updated person embeddings
    re_eval_res = _re_evaluate_video_faces(video_id)
    updated_count = re_eval_res["updated_frames"]

    jobs.broadcast_event("candidates_updated", {
        "video_id": video_id,
        "person_id": person["id"],
        "person_name": person["name"],
        "exemplar_count": person["exemplar_count"],
        "updated_frames": updated_count,
    })

    return {
        "status": "ok",
        "person": _enrich_person_dict(person),
        "updated_frames": updated_count,
    }


@app.get("/api/frames/{frame_id}/faces")
def get_frame_faces_endpoint(frame_id: int):
    frame = db.one("SELECT id, video_id, preview_path, full_path, zs_scores FROM frames WHERE id=?", (frame_id,))
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    target_emb = None
    vid = db.one("SELECT target_person_id FROM videos WHERE id=?", (frame["video_id"],))
    if vid and vid.get("target_person_id"):
        target_emb = person_embedding(db.get_person(vid["target_person_id"]))

    img_path = Path(frame.get("full_path") or frame.get("preview_path") or "")
    if not img_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found")

    img_bgr = cv2.imread(str(img_path))
    if img_bgr is None:
        raise HTTPException(status_code=400, detail="Could not read frame image")

    match_res = jobs.face_engine.match_frame(img_bgr, target_emb)
    return {
        "frame_id": frame_id,
        "face_count": match_res["face_count"],
        "target_person_sim": match_res["target_person_sim"],
        "matched": match_res["matched"],
        "faces": match_res["faces"],
    }


@app.post("/api/videos/{video_id}/auto-extract")
def auto_extract_endpoint(video_id: str, req: AutoExtractRequest = None):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    pid = req.target_person_id if req else None
    job_id = jobs.submit_auto_extract_job(video_id, target_person_id=pid)
    return {"job_id": job_id, "status": "queued"}


class BatchAutoExtractRequest(BaseModel):
    video_ids: list[str] | None = None
    only_unextracted: bool = True
    target_curated: int | None = None


@app.post("/api/videos/batch-auto-extract")
def batch_auto_extract_endpoint(req: BatchAutoExtractRequest | None = None):
    """Executes the auto-extraction pipeline for all videos in list step by step (overnight batch run)."""
    opts = req.model_dump(exclude_none=True) if req else {}
    vids = opts.pop("video_ids", None)
    only_un = opts.pop("only_unextracted", True)
    try:
        job_id = jobs.submit_batch_auto_extract_job(video_ids=vids, only_unextracted=only_un, options=opts)
        return {"job_id": job_id, "status": "queued"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ----------------------------------------------------------------------------- Person enrollment from the video
def _decode_frame_at(video_id: str, t: float, max_dim: int) -> np.ndarray:
    vid = db.one("SELECT path, letterbox, watermark_rect FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    frames = sample_frames_at(Path(vid["path"]), [max(0.0, t)], max_dim=max_dim)
    if not frames:
        raise HTTPException(status_code=400, detail=f"Could not decode a frame at {t:.2f}s")
    return frames[0][1]


def _sorted_faces(img_bgr: np.ndarray) -> list[dict[str, Any]]:
    faces = jobs.face_engine.detect_faces(img_bgr)
    faces.sort(key=lambda f: f["box"][2] * f["box"][3] * f["score"], reverse=True)
    return faces


@app.get("/api/videos/{video_id}/frame-at")
def get_video_frame_at(video_id: str, t: float = 0.0, max_dim: int = 960):
    """JPEG of the video at `t` seconds (to pick a face from when enrolling the person to prioritize)."""
    img = _decode_frame_at(video_id, t, max(64, min(max_dim, 2160)))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise HTTPException(status_code=500, detail="Could not encode frame")
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/api/videos/{video_id}/faces-at")
def get_video_faces_at(video_id: str, t: float = 0.0, max_dim: int = 960):
    """Faces at `t`, most prominent first; boxes are in the coordinates of `frame-at` with the same max_dim."""
    img = _decode_frame_at(video_id, t, max(64, min(max_dim, 2160)))
    faces = _sorted_faces(img)
    h, w = img.shape[:2]
    return {
        "video_id": video_id,
        "time_s": t,
        "width": w,
        "height": h,
        "faces": [{"index": i, "box": f["box"], "score": f["score"]} for i, f in enumerate(faces)],
    }


@app.post("/api/persons/enroll")
def enroll_person_from_video(req: PersonEnrollRequest):
    """Create (or add exemplars to) a person from the face at one or more timestamps of a video."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    stamps = list(req.times or [])
    if req.time_s is not None:
        stamps.insert(0, req.time_s)
    if not stamps:
        raise HTTPException(status_code=400, detail="Provide time_s or times")

    crop_dir = settings.cache_dir / "persons"
    crop_dir.mkdir(parents=True, exist_ok=True)
    person: dict[str, Any] | None = None
    enrolled = 0
    for t in stamps:
        img = _decode_frame_at(req.video_id, t, 0)  # native resolution: best identity embedding
        faces = _sorted_faces(img)
        if not faces:
            raise HTTPException(status_code=400, detail=f"No face found at {t:.2f}s")
        face = faces[req.face_index if 0 <= req.face_index < len(faces) else 0]
        emb = jobs.face_engine.extract_feature(img, face)
        crop_path = crop_dir / f"enroll_{int(time.time() * 1000)}_{enrolled}.jpg"
        cv2.imwrite(str(crop_path), jobs.face_engine.crop_face(img, face), [cv2.IMWRITE_JPEG_QUALITY, 95])
        person = db.create_or_update_person(
            name, embedding=emb, face_crop_path=str(crop_path),
            person_id=req.person_id if req.person_id is not None else (person["id"] if person else None),
        )
        enrolled += 1

    assert person is not None
    final_crop = crop_dir / f"person_{person['id']}.jpg"
    Path(person["face_crop_path"]).replace(final_crop)
    with db.write() as conn:
        conn.execute("UPDATE persons SET face_crop_path=? WHERE id=?", (str(final_crop), person["id"]))
    person["face_crop_path"] = str(final_crop)
    return {"person": _enrich_person_dict(person), "enrolled": enrolled}


# ----------------------------------------------------------------------------- Autopilot
def _autopilot_payload(video_id: str) -> dict[str, Any]:
    state = jobs.autopilot.get_state(video_id)
    video = db.one("SELECT target_person_id, target_person_name FROM videos WHERE id=?", (video_id,)) or {}
    person = db.get_person(video["target_person_id"]) if video.get("target_person_id") else None
    return {
        **state,
        "video_id": video_id,
        "target_person": (
            {"id": person["id"], "name": person["name"], "face_crop_url": f"/api/persons/{person['id']}/crop" if person.get("face_crop_path") else None}
            if person else None
        ),
        "chunks": get_video_scenes_caption_stats(db, video_id),
        "priorities": list(PRIORITIES),
    }


def _autopilot_errors(fn):
    """Translate autopilot domain errors to HTTP responses."""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except DestructiveRechunkError as e:
            raise _rechunk_conflict(e) from e
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return wrapper


@app.get("/api/videos/{video_id}/autopilot")
def get_autopilot(video_id: str):
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    return _autopilot_payload(video_id)


@app.post("/api/videos/{video_id}/autopilot/start")
@_autopilot_errors
def start_autopilot(video_id: str, config: AutopilotConfig = None):
    jobs.autopilot.start(video_id, config or AutopilotConfig())
    return _autopilot_payload(video_id)


@app.post("/api/videos/{video_id}/autopilot/priorities")
@_autopilot_errors
def set_autopilot_priorities(video_id: str, req: AutopilotPrioritiesRequest):
    """Human review: set scene priorities; the response carries the re-derived budgets."""
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    if jobs.autopilot.get_state(video_id)["stage"] in ("planning", "extracting", "refining"):
        raise HTTPException(status_code=409, detail="Autopilot is running; wait for it to finish or cancel the job")
    autochunker.apply_priorities(video_id, req.priorities)
    return _autopilot_payload(video_id)


@app.post("/api/videos/{video_id}/autopilot/approve")
@_autopilot_errors
def approve_autopilot(video_id: str, req: AutopilotApproveRequest = None):
    jobs.autopilot.approve(video_id, req.priorities if req else None)
    return _autopilot_payload(video_id)


@app.post("/api/videos/{video_id}/autopilot/refine")
@_autopilot_errors
def refine_autopilot(video_id: str, req: AutopilotRefineRequest = None):
    """Re-evaluate similarity of the current picks (also for videos curated by hand)."""
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    jobs.autopilot.refine(video_id, req.model_dump() if req else None)
    return _autopilot_payload(video_id)


@app.post("/api/videos/{video_id}/autopilot/reset")
def reset_autopilot(video_id: str):
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    if jobs.autopilot.get_state(video_id)["stage"] in ("planning", "extracting", "refining"):
        raise HTTPException(status_code=409, detail="Autopilot is running; cancel its job first")
    jobs.autopilot.reset(video_id)
    return _autopilot_payload(video_id)


@app.post("/api/autopilot/batch")
def start_autopilot_batch(req: AutopilotBatchRequest):
    """Start the same configuration on many videos; exclusions usually differ, so `per_video` overrides them."""
    results = []
    for vid in req.video_ids:
        entry: dict[str, Any] = {"video_id": vid, "ok": True}
        try:
            cfg = AutopilotConfig(**{**req.config.model_dump(), **req.per_video.get(vid, {})})
            jobs.autopilot.start(vid, cfg)
        except DestructiveRechunkError as e:
            entry.update(ok=False, requires_confirm=True, error=str(e))
        except Exception as e:
            entry.update(ok=False, error=str(e))
        results.append(entry)
    return {"results": results, "started": sum(1 for r in results if r["ok"])}


# ----------------------------------------------------------------------------- Similarity Deduplication & Postprocessing
class DedupSimilarityRequest(BaseModel):
    similarity_threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    target_min: int = Field(default=150, ge=1)
    target_max: int = Field(default=200, ge=1)


@app.post("/api/videos/{video_id}/dedup-similarity")
@app.post("/api/videos/{video_id}/postprocess-similarity")
def dedup_similarity_endpoint(video_id: str, req: DedupSimilarityRequest | None = None):
    """Local postprocessing step per video:
    Analyzes visual similarity (CLIP cosine), selects the image with the best scoring value
    for pairs with >= 95% similarity, and constrains the active tagged set to 150 - 200 images.
    """
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    from pipeline.refine import Refiner
    refiner = Refiner(db, settings)
    thresh = req.similarity_threshold if req else 0.95
    t_min = req.target_min if req else 150
    t_max = req.target_max if req else 200
    try:
        report = refiner.deduplicate_and_trim_similarity(
            video_id, similarity_threshold=thresh, target_min=t_min, target_max=t_max
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    jobs.broadcast_event("job_finished", {
        "job_id": 0,
        "video_id": video_id,
        "step": "dedup_similarity",
        "picks_count": report["after_count"],
        "duplicates_removed": report["duplicates_removed"],
    })
    return report


# ----------------------------------------------------------------------------- Chunks
@app.get("/api/videos/{video_id}/chunks")
def get_chunks(video_id: str):
    chunks = get_video_scenes_caption_stats(db, video_id)
    return {"chunks": chunks}


@app.get("/api/videos/{video_id}/scenes")
def get_video_scenes(video_id: str):
    return {"scenes": get_video_scenes_caption_stats(db, video_id)}


@app.put("/api/videos/{video_id}/chunks")
def save_chunks(video_id: str, update: ChunksUpdate):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        saved = autochunker.save_user_chunks(
            video_id,
            [c.model_dump(exclude_unset=True) for c in update.chunks],
            confirm_destructive=update.confirm_destructive,
        )
        return {
            "chunks": saved,
            "scene_stats": get_video_scenes_caption_stats(db, video_id),
        }
    except DestructiveRechunkError as e:
        raise _rechunk_conflict(e) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _rechunk_conflict(err: DestructiveRechunkError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"message": str(err), "requires_confirm": True, "affected_chunks": err.affected},
    )


@app.patch("/api/chunks/{chunk_id}")
def update_chunk(chunk_id: int, patch: ChunkPatch):
    existing = db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Chunk not found")

    updates = []
    params = []
    if patch.caption_model is not None:
        updates.append("caption_model = ?")
        params.append(patch.caption_model)
    if patch.budget_pct is not None:
        updates.append("budget_pct = ?")
        params.append(patch.budget_pct)
    if patch.rule is not None:
        updates.append("rule = ?")
        params.append(patch.rule)
    if patch.tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(patch.tags))
    if patch.notes is not None:
        updates.append("notes = ?")
        params.append(patch.notes)

    if updates:
        params.append(chunk_id)
        with db.write() as conn:
            conn.execute(f"UPDATE chunks SET {', '.join(updates)} WHERE id = ?", tuple(params))

    return {"chunk": db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))}


class ChunkExtractRequest(BaseModel):
    k: int | None = Field(default=None, ge=1, le=500)
    budget_pct: float | None = Field(default=None, ge=0.0, le=100.0)


@app.post("/api/chunks/{chunk_id}/extract")
@app.post("/api/videos/{video_id}/chunks/{chunk_id}/extract")
def extract_chunk_endpoint(chunk_id: int, req: ChunkExtractRequest | None = None, video_id: str | None = None):
    """Targeted single-chunk extraction: extracts candidates and picks strictly within this chunk's timecode range."""
    ch = db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
    if not ch:
        raise HTTPException(status_code=404, detail="Chunk not found")
    opts = req.model_dump(exclude_none=True) if req else {}
    try:
        job_id = jobs.submit_extract_chunk_job(chunk_id, options=opts)
        return {"job_id": job_id, "status": "queued", "chunk_id": chunk_id, "video_id": ch["video_id"]}
    except JobConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/videos/{video_id}/chunks/suggest")
def suggest_chunks(video_id: str, req: SuggestChunksRequest = None):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    # Generates maximum 15 balanced scene chunks with budget percentages summing to 100%
    try:
        chunks = autochunker.generate_balanced_chunks(
            video_id, target_frames=300, confirm_destructive=bool(req and req.confirm_destructive)
        )
    except DestructiveRechunkError as e:
        raise _rechunk_conflict(e) from e
    return {"suggested": chunks}


@app.post("/api/videos/{video_id}/extract-750")
@app.post("/api/videos/{video_id}/extract_750")
def trigger_extract_750(video_id: str):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    # While the human is reviewing an autopilot plan, extracting means approving it (so it continues to refine)
    if jobs.autopilot.get_state(video_id)["stage"] == "awaiting_review":
        state = jobs.autopilot.approve(video_id)
        return {"job_id": state["job_id"], "status": "queued", "autopilot": state["stage"]}
    job_id = jobs.submit_extract_750_job(video_id)
    return {"job_id": job_id, "status": "queued"}


def _enrich_frame_dict(
    f: dict[str, Any],
    exported_ids: set[int] | None = None,
    tag_map: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    f["preview_url"] = f"/api/frames/{f['id']}/preview"
    f["full_url"] = f"/api/frames/{f['id']}/full"
    f["thumb_url"] = f"/api/frames/{f['id']}/preview"
    f["time_s"] = f["t"]
    f["is_pick"] = (f.get("role") == "pick")
    f["is_captioned"] = (f["id"] in exported_ids) if exported_ids is not None else False
    f["chunk_pos"] = f.get("chunk_pos", f.get("chunk_position", 0))
    f["chunk_position"] = f.get("chunk_position", f.get("chunk_pos", 0))
    f["chunk_start"] = f.get("chunk_start", 0.0)
    f["chunk_end"] = f.get("chunk_end", 0.0)
    f["native"] = f.get("native", 0)

    # Face & Person target metadata
    zs = f.get("zs_scores")
    if isinstance(zs, str):
        try:
            zs = json.loads(zs)
        except Exception:
            zs = {}
    elif not isinstance(zs, dict):
        zs = {}
    f["target_person_sim"] = round(float(zs.get("target_person_sim", 0.0)), 3)
    f["target_person_matched"] = bool(zs.get("target_person_matched", False))
    f["face_count"] = int(zs.get("face_count", 0))
    f["faces"] = zs.get("faces", [])
    if tag_map is not None:
        f["tags"] = tag_map.get(f["id"], [])
    return f


def _fetch_curation_frames(video_id: str, role: str) -> list[dict[str, Any]]:
    return db.all(
        """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.preview_path, f.full_path, f.native,
               p.role, p.rank, p.state, p.crop, c.position as chunk_pos, c.position as chunk_position,
               c.start_s as chunk_start, c.end_s as chunk_end, c.rule as chunk_rule, c.tags as chunk_tags,
               c.notes as chunk_notes, c.caption_model as chunk_caption_model, c.budget_pct, c.caption_model
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ? AND p.role = ?
        ORDER BY c.position ASC, p.rank ASC, f.t ASC
        """,
        (video_id, role),
    )


@app.get("/api/videos/{video_id}/curation-300")
def get_curation_300(video_id: str):
    video = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    chunks = get_video_scenes_caption_stats(db, video_id)
    picks = _fetch_curation_frames(video_id, "pick")
    runners = _fetch_curation_frames(video_id, "runner_up")

    stats = jobs.scoring_engine.compute_distribution_stats(picks)
    exported_ids = {r["frame_id"] for r in db.all("SELECT e.frame_id FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (video_id,))}
    tag_map = jobs.image_tags.video_tag_map(video_id)
    for f in picks + runners:
        _enrich_frame_dict(f, exported_ids, tag_map)

    return {
        "video": video,
        "chunks": chunks,
        "scene_stats": chunks,
        "picks": picks,
        "runners": runners,
        "candidates": picks + runners,
        "stats": stats,
        "distribution": stats,
    }


@app.post("/api/videos/{video_id}/swap-frame")
def swap_frame(video_id: str, req: SwapFrameRequest):
    drop_id = req.target_drop_id
    drop_frame = db.one(
        """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.embedding, f.zs_scores, p.role
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        WHERE f.id = ?
        """,
        (drop_id,),
    )
    if not drop_frame:
        raise HTTPException(status_code=404, detail="Frame not found in picks")
    if drop_frame["video_id"] != video_id:
        raise HTTPException(status_code=400, detail="Drop frame does not belong to this video")

    chunk_id = drop_frame["chunk_id"]
    pick_id = req.pick_frame_id

    if pick_id:
        pick_frame = db.one(
            """
            SELECT f.id, f.video_id, f.chunk_id, p.role
            FROM frames f
            JOIN picks p ON f.id = p.frame_id
            WHERE f.id = ?
            """,
            (pick_id,),
        )
        if not pick_frame:
            raise HTTPException(status_code=404, detail="Pick candidate frame not found in picks")
        if pick_frame["video_id"] != video_id:
            raise HTTPException(status_code=400, detail="Pick candidate does not belong to this video")
        if pick_frame["chunk_id"] != chunk_id:
            raise HTTPException(status_code=400, detail="Pick frame does not belong to the same chunk as drop frame")
    else:
        # Choose best replacement runner via argmax_j (sim(drop, j) * q_j) within chunk
        runners = db.all(
            """
            SELECT f.id, f.t, f.embedding, f.zs_scores, p.rank
            FROM frames f
            JOIN picks p ON f.id = p.frame_id
            WHERE p.chunk_id = ? AND p.role = 'runner_up'
            ORDER BY p.rank ASC
            """,
            (chunk_id,),
        )
        if not runners:
            raise HTTPException(status_code=400, detail="No replacement candidates available in this chunk")

        drop_emb: np.ndarray | None = None
        if drop_frame.get("embedding"):
            try:
                drop_emb = np.frombuffer(drop_frame["embedding"], dtype=np.float32)
            except Exception:
                drop_emb = None

        drop_t = float(drop_frame.get("t", 0.0))
        best_score = -1e9
        best_cand_id = runners[0]["id"]

        for r in runners:
            q_j = 0.5
            if r.get("zs_scores"):
                try:
                    sc = json.loads(r["zs_scores"]) if isinstance(r["zs_scores"], str) else r["zs_scores"]
                    q_j = float(sc.get("composite", sc.get("quality", 0.5)))
                except Exception:
                    q_j = 0.5

            sim_val = 1.0
            r_emb: np.ndarray | None = None
            if r.get("embedding"):
                try:
                    r_emb = np.frombuffer(r["embedding"], dtype=np.float32)
                except Exception:
                    r_emb = None

            if drop_emb is not None and r_emb is not None and drop_emb.size == r_emb.size:
                dot = float(np.dot(drop_emb, r_emb))
                norm_drop = max(1e-6, float(np.linalg.norm(drop_emb)))
                norm_r = max(1e-6, float(np.linalg.norm(r_emb)))
                cos_sim = max(0.0, dot / (norm_drop * norm_r))
                t_dist = abs(drop_t - float(r.get("t", 0.0)))
                t_sim = math.exp(-t_dist / 5.0)
                sim_val = 0.75 * cos_sim + 0.25 * t_sim
            else:
                t_dist = abs(drop_t - float(r.get("t", 0.0)))
                sim_val = math.exp(-t_dist / 5.0)

            score = sim_val * max(0.01, q_j)
            if score > best_score:
                best_score = score
                best_cand_id = r["id"]

        pick_id = best_cand_id

    # Execute swap in database
    with db.write() as conn:
        conn.execute("UPDATE picks SET role='runner_up', state='candidate' WHERE frame_id=?", (drop_id,))
        conn.execute("UPDATE picks SET role='pick', state='keep' WHERE frame_id=?", (pick_id,))
    log_curation_events(db, [(drop_id, "swap_out"), (pick_id, "swap_in")])

    # Ensure swapped-in pick has native full-resolution decode (C1/H1)
    ensure_native_frame(db, pick_id)

    picks = _fetch_curation_frames(video_id, "pick")
    runners = _fetch_curation_frames(video_id, "runner_up")
    exported_ids = {r["frame_id"] for r in db.all("SELECT e.frame_id FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (video_id,))}
    tag_map = jobs.image_tags.video_tag_map(video_id)
    for f in picks + runners:
        _enrich_frame_dict(f, exported_ids, tag_map)

    stats = jobs.scoring_engine.compute_distribution_stats(picks)
    return {
        "status": "ok",
        "swapped_in": {"id": pick_id},
        "swapped_out": {"id": drop_id},
        "dropped": drop_id,
        "picks": picks,
        "runners": runners,
        "candidates": picks + runners,
        "stats": stats,
        "distribution": stats,
    }


@app.post("/api/videos/{video_id}/caption-openrouter")
def trigger_caption_openrouter(video_id: str, req: OpenRouterCaptionRequest):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    options = req.model_dump()
    if not options.get("mode"):
        stored = merged_user_settings(db.get_setting("user_settings", {}))
        options["mode"] = caption_mode_for(db, video_id, fallback=stored.get("openrouter_mode") or "concept")
    job_id = jobs.submit_openrouter_caption_job(video_id, options)
    return {"job_id": job_id, "status": "queued", "mode": options["mode"]}


@app.post("/api/openrouter/batch_caption")
def trigger_openrouter_batch_caption_alias(req: dict[str, Any]):
    vid = req.get("video_id")
    if not vid:
        raise HTTPException(status_code=400, detail="video_id required")
    # map trigger_word -> trigger
    if "trigger_word" in req and "trigger" not in req:
        req["trigger"] = req["trigger_word"]
    clean_params = {k: v for k, v in req.items() if k != "video_id" and v is not None}
    caption_req = OpenRouterCaptionRequest(**clean_params)
    return trigger_caption_openrouter(str(vid), caption_req)


@app.get("/api/openrouter/models")
def list_openrouter_models():
    return {
        "models": [
            {
                "id": "google/gemma-4-31b-it",
                "name": "Google Gemma 4 31B (Default)",
                "recommended": True,
            },
            {
                "id": "deepseek/deepseek-v4.1-flash",
                "name": "DeepSeek V4.1 Flash (Layer 2 Fallback for refused frames)",
                "recommended": False,
            },
            {
                "id": "x-ai/grok-4.7",
                "name": "Grok 4.7 (Layer 3 Final Fallback, Uncensored / High Reliability)",
                "recommended": False,
            },
            {"id": "qwen/qwen-2.5-vl-72b-instruct", "name": "Qwen 2.5 VL 72B", "recommended": False},
            {"id": "google/gemini-2.0-flash-exp:free", "name": "Gemini 2.0 Flash (Free)", "recommended": False},
            {"id": "mistralai/pixtral-12b", "name": "Pixtral 12B", "recommended": False},
        ]
    }


class AutoProcessRequest(BaseModel):
    target_frames: int = 150


class BatchAutoProcessRequest(BaseModel):
    video_ids: list[str]
    target_frames: int = 150


# ----------------------------------------------------------------------------- Jobs & SSE
@app.post("/api/jobs")
def create_job(req: JobCreate):
    job_id = jobs.submit_extract_job(req.video_id, req.chunk_id)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/videos/{video_id}/auto-process")
def auto_process_single_endpoint(video_id: str, req: AutoProcessRequest = None):
    target = req.target_frames if req else 150
    job_id = jobs.submit_auto_process_job(video_id, target_frames=target)
    return {"job_id": job_id, "status": "queued"}


@app.post("/api/videos/batch-auto-process")
def batch_auto_process_endpoint(req: BatchAutoProcessRequest):
    job_ids = []
    for vid in req.video_ids:
        jid = jobs.submit_auto_process_job(vid, target_frames=req.target_frames)
        job_ids.append(jid)
    return {"job_ids": job_ids, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(video_id: str | None = None):
    if video_id:
        rows = db.all("SELECT * FROM jobs WHERE video_id=? ORDER BY id DESC LIMIT 20", (video_id,))
    else:
        rows = db.all("SELECT * FROM jobs ORDER BY id DESC LIMIT 20")
    return {"jobs": rows}


@app.delete("/api/jobs/{job_id}")
def cancel_job_endpoint(job_id: int):
    ok = jobs.cancel_job(job_id)
    return {"cancelled": ok}


@app.get("/api/events")
async def events_endpoint():
    return StreamingResponse(jobs.sse_stream(), media_type="text/event-stream")


# ----------------------------------------------------------------------------- Candidates & Curation
@app.get("/api/videos/{video_id}/candidates")
def get_video_candidates(video_id: str):
    """Retrieve all extracted candidates and picks grouped by chunk for a video."""
    query = """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.zs_scores,
               f.preview_path, f.full_path, f.native, p.role, p.rank, p.state, p.crop,
               c.position AS chunk_position, c.position AS chunk_pos, c.start_s AS chunk_start,
               c.end_s AS chunk_end, c.rule AS chunk_rule, c.tags AS chunk_tags,
               c.notes AS chunk_notes, c.caption_model AS chunk_caption_model, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        LEFT JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ?
        ORDER BY c.position ASC, f.chunk_id ASC, p.role DESC, p.rank ASC
    """
    frames = db.all(query, (video_id,))
    exported_ids = {r["frame_id"] for r in db.all("SELECT e.frame_id FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (video_id,))}
    tag_map = jobs.image_tags.video_tag_map(video_id)
    for f in frames:
        _enrich_frame_dict(f, exported_ids, tag_map)

    picks = [f for f in frames if f["is_pick"]]
    runners = [f for f in frames if not f["is_pick"]]
    stats = jobs.scoring_engine.compute_distribution_stats(picks)

    return {
        "frames": frames,
        "candidates": frames,
        "picks": picks,
        "runners": runners,
        "stats": stats,
        "distribution": stats,
        "scene_stats": get_video_scenes_caption_stats(db, video_id),
    }


@app.post("/api/videos/{video_id}/candidates/{frame_id}/state")
def set_candidate_state_endpoint(video_id: str, frame_id: int, req: CandidateStateRequest):
    allowed_states = ("keep", "reject", "star", "candidate", None)
    if req.state not in allowed_states:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid candidate state '{req.state}'. Allowed states: ['keep', 'reject', 'star', 'candidate', null]",
        )

    frame = db.one("SELECT id, video_id, chunk_id FROM frames WHERE id=?", (frame_id,))
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    if frame["video_id"] != video_id:
        raise HTTPException(status_code=400, detail="Frame does not belong to this video")

    old_pick = db.one("SELECT state FROM picks WHERE frame_id=?", (frame_id,))
    with db.write() as conn:
        cur = conn.execute("UPDATE picks SET state=? WHERE frame_id=?", (req.state, frame_id))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'runner_up', 999, ?)",
                (frame_id, frame["chunk_id"], req.state),
            )
    action = state_action(old_pick["state"] if old_pick else None, req.state)
    if action:
        log_curation_events(db, [(frame_id, action)])

    if req.state in ("keep", "star"):
        ensure_native_frame(db, frame_id)

    return {"status": "ok", "frame_id": frame_id, "state": req.state}


@app.post("/api/videos/{video_id}/curate/keep_all")
def bulk_keep_all_picks(video_id: str):
    changed = _bulk_state_changes(video_id, "keep")
    with db.write() as conn:
        conn.execute(
            """
            UPDATE picks SET state='keep'
            WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?) AND role='pick'
            """,
            (video_id,),
        )
    log_curation_events(db, [(fid, "bulk_keep") for fid in changed])
    return {"status": "ok", "video_id": video_id}


@app.post("/api/videos/{video_id}/curate/reject_all")
def bulk_reject_all_picks(video_id: str):
    changed = _bulk_state_changes(video_id, "reject")
    with db.write() as conn:
        conn.execute(
            """
            UPDATE picks SET state='reject'
            WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?) AND role='pick'
            """,
            (video_id,),
        )
    log_curation_events(db, [(fid, "bulk_reject") for fid in changed])
    return {"status": "ok", "video_id": video_id}


@app.get("/api/videos/{video_id}/frames/{frame_id}/thumb")
def get_video_frame_thumb(video_id: str, frame_id: int):
    return get_frame_preview(frame_id)


@app.get("/api/chunks/{chunk_id}/candidates")
def get_chunk_candidates(chunk_id: int):
    query = """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.zs_scores,
               f.preview_path, f.full_path, f.native, p.role, p.rank, p.state, p.crop,
               c.position AS chunk_position, c.position AS chunk_pos, c.start_s AS chunk_start,
               c.end_s AS chunk_end, c.rule AS chunk_rule, c.tags AS chunk_tags,
               c.notes AS chunk_notes, c.caption_model AS chunk_caption_model, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        LEFT JOIN chunks c ON f.chunk_id = c.id
        WHERE f.chunk_id = ?
        ORDER BY p.role DESC, p.rank ASC
    """
    frames = db.all(query, (chunk_id,))
    exported_ids = {r["frame_id"] for r in db.all("SELECT e.frame_id FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.chunk_id=?", (chunk_id,))}
    tag_map = jobs.image_tags.video_tag_map(frames[0]["video_id"]) if frames else {}
    for f in frames:
        _enrich_frame_dict(f, exported_ids, tag_map)
    return {"frames": frames}


@app.patch("/api/picks/{frame_id}")
def update_pick(frame_id: int, patch: PickPatch):
    existing = db.one("SELECT * FROM picks WHERE frame_id=?", (frame_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Pick not found")
    if patch.state is not None and patch.state not in ("keep", "reject", "star", "candidate"):
        raise HTTPException(status_code=400, detail="state must be keep, reject, star or candidate")
    if patch.role is not None and patch.role not in ("pick", "runner_up"):
        raise HTTPException(status_code=400, detail="role must be pick or runner_up")

    if patch.rebalance and patch.role == "runner_up" and existing["role"] == "pick":
        # Keep the chunk quota: the swap demotes this frame and promotes the best runner-up of the chunk
        frame = db.one("SELECT video_id FROM frames WHERE id=?", (frame_id,))
        swapped = swap_frame(frame["video_id"], SwapFrameRequest(drop_frame_id=frame_id))
        if patch.state is not None or patch.crop is not None:
            update_pick(frame_id, PickPatch(state=patch.state, crop=patch.crop))
        return {"pick": db.one("SELECT * FROM picks WHERE frame_id=?", (frame_id,)), "rebalanced": swapped}

    updates = []
    params = []
    if patch.state is not None:
        updates.append("state = ?")
        params.append(patch.state)
    if patch.role is not None:
        updates.append("role = ?")
        params.append(patch.role)
    if patch.crop is not None:
        updates.append("crop = ?")
        params.append(json.dumps(patch.crop))

    if updates:
        params.append(frame_id)
        with db.write() as conn:
            conn.execute(f"UPDATE picks SET {', '.join(updates)} WHERE frame_id = ?", tuple(params))

    _log_pick_patch(frame_id, existing, patch)
    return {"pick": db.one("SELECT * FROM picks WHERE frame_id=?", (frame_id,))}


@app.get("/api/frames/{frame_id}/preview")
def get_frame_preview(frame_id: int):
    f = db.one("SELECT preview_path FROM frames WHERE id=?", (frame_id,))
    if not f or not f.get("preview_path") or not Path(f["preview_path"]).is_file():
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(Path(f["preview_path"]), media_type="image/webp")


@app.get("/api/frames/{frame_id}/full")
def get_frame_full(frame_id: int):
    f = db.one("SELECT full_path FROM frames WHERE id=?", (frame_id,))
    if not f or not f.get("full_path") or not Path(f["full_path"]).is_file():
        raise HTTPException(status_code=404, detail="Full frame not found")
    return FileResponse(Path(f["full_path"]), media_type="image/png")


# ----------------------------------------------------------------------------- Export & Dataset
class ExportRequest(BaseModel):
    video_id: str | None = None
    trigger_word: str | None = None


@app.post("/api/export")
def run_export(req: ExportRequest):
    if req.video_id:
        res = exporter.export_video(req.video_id, custom_trigger=req.trigger_word)
        return res
    else:
        # Export all curated videos. 'exported' is excluded: those are finished (and captioned); re-running
        # the exporter there could only re-link files. Videos with a job in flight are skipped.
        curated = db.all(
            """
            SELECT id FROM videos v
            WHERE status IN ('curating', 'curating_300', 'chunked') AND missing = 0
              AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.video_id = v.id AND j.status IN ('queued', 'running'))
            """
        )
        total = 0
        for v in curated:
            r = exporter.export_video(v["id"], custom_trigger=req.trigger_word)
            total += r.get("exported", 0)
        return {"exported": total, "videos_processed": len(curated)}


@app.get("/api/dataset")
def get_dataset_summary():
    """Retrieve global dataset statistics: counts per video, tags breakdown."""
    total_exports = db.one("SELECT COUNT(*) as cnt FROM exports")["cnt"]
    per_video = db.all(
        """
        SELECT v.id, v.name, v.slug, COUNT(e.frame_id) as count
        FROM videos v
        JOIN frames f ON v.id = f.video_id
        JOIN exports e ON f.id = e.frame_id
        GROUP BY v.id
        ORDER BY count DESC
        """
    )
    return {
        "total_exports": total_exports,
        "per_video": per_video,
        "finished_dir": str(settings.finished_dir),
    }


# ----------------------------------------------------------------------------- Captions (Phase G)
from pipeline.captions import caption_stats as _caption_stats, get_caption as _get_caption  # noqa: E402
from pipeline.prompts import MODES as _CAPTION_MODES, prompt_version as _prompt_version  # noqa: E402


@app.get("/api/captions/stats")
def get_caption_stats(video_id: str | None = None, job_id: int | None = None):
    """Caption totals from the captions table: by source, by model/layer, by prompt version, tokens and cost."""
    stats = _caption_stats(db, video_id=video_id, job_id=job_id)
    stats["current_prompt_versions"] = {m: _prompt_version(m) for m in _CAPTION_MODES}
    return stats


@app.get("/api/frames/{frame_id}/caption")
def get_frame_caption(frame_id: int):
    """The frame's caption row (source of truth), or `caption: null` when it has none yet."""
    if not db.one("SELECT id FROM frames WHERE id=?", (frame_id,)):
        raise HTTPException(status_code=404, detail="Frame not found")
    return {"frame_id": frame_id, "caption": _get_caption(db, frame_id)}


# ----------------------------------------------------------------------------- Phase F: curation log, learned weights, reselect
from pipeline.reselect import ReselectError, reselect_video  # noqa: E402


def _bulk_state_changes(video_id: str, target_state: str) -> list[int]:
    """Pick frames of a video whose state a bulk keep/reject would change (only those are logged)."""
    rows = db.all(
        """
        SELECT p.frame_id FROM picks p JOIN frames f ON f.id = p.frame_id
        WHERE f.video_id = ? AND p.role = 'pick' AND p.state IS NOT ?
        """,
        (video_id, target_state),
    )
    return [int(r["frame_id"]) for r in rows]


def _log_pick_patch(frame_id: int, existing: dict[str, Any], patch: PickPatch) -> None:
    """Curation events for a PATCH /api/picks change: role pick <-> runner_up is a swap, state a keep/reject/star."""
    events: list[tuple[int, str]] = []
    if patch.role is not None and patch.role != existing.get("role"):
        events.append((frame_id, "swap_in" if patch.role == "pick" else "swap_out"))
    if patch.state is not None:
        action = state_action(existing.get("state"), patch.state)
        if action:
            events.append((frame_id, action))
    if events:
        log_curation_events(db, events)


class ReselectRequest(BaseModel):
    target_count: int | None = Field(default=None, ge=1, le=5000)
    per_chunk_floor: int | None = Field(default=None, ge=0, le=5000)
    per_chunk_cap: int | None = Field(default=None, ge=0, le=5000)
    # 0 = pure quality ranking, 1 = pure coverage (facility location); default: setting reselect_diversity
    diversity: float | None = Field(default=None, ge=0.0, le=1.0)
    # Budget strictness when floor/cap are not given: floor = (1-e)*quota, cap = (1+e)*quota
    elasticity: float | None = Field(default=None, ge=0.0, le=1.0)
    # Phase F2: max share of the picks per axis (multi_person|solo|equipment|face, 0..1); default: setting
    # reselect_axis_caps ({} = none). An empty object here disables the stored caps for this call.
    axis_caps: dict[str, float] | None = None
    # Skip candidates near-duplicating frames already exported from other videos; default: setting
    cross_dataset_dedup: bool | None = None


@app.post("/api/videos/{video_id}/reselect")
def reselect_endpoint(video_id: str, req: ReselectRequest | None = None):
    """Re-run only the pick/runner selection over stored frames (no decode). Same shape as curation-300 + `reselect`."""
    if not db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
        raise HTTPException(status_code=404, detail="Video not found")
    req = req or ReselectRequest()
    jobs._assert_no_active_job(video_id)  # JobConflictError -> 409
    user_settings = merged_user_settings(db.get_setting("user_settings", {}))
    diversity = req.diversity if req.diversity is not None else float(user_settings.get("reselect_diversity", 0.5))
    elasticity = req.elasticity if req.elasticity is not None else float(user_settings.get("reselect_elasticity", 0.2))
    axis_caps = req.axis_caps if req.axis_caps is not None else (user_settings.get("reselect_axis_caps") or {})
    cross_dedup = (
        req.cross_dataset_dedup if req.cross_dataset_dedup is not None
        else bool(user_settings.get("reselect_cross_dataset_dedup", False))
    )
    try:
        report = reselect_video(
            db,
            video_id,
            target_count=req.target_count,
            per_chunk_floor=req.per_chunk_floor,
            per_chunk_cap=req.per_chunk_cap,
            diversity=diversity,
            elasticity=elasticity,
            dup_threshold=float(user_settings.get("similarity_dedup_threshold", 0.95)),
            learned_weights=active_learned_weights(db, user_settings),
            learned_blend=float(user_settings.get("learned_weights_blend", 0.5)),
            before_write=lambda: jobs._assert_no_active_job(video_id),
            axis_caps=axis_caps,
            cross_dataset_dedup=cross_dedup,
            embed_rows=jobs.selection_embed_rows(video_id, user_settings),
        )
    except ReselectError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = get_curation_300(video_id)
    payload["reselect"] = report
    return payload


@app.get("/api/scoring/learned-weights")
def get_learned_weights():
    return learned_status(db, merged_user_settings(db.get_setting("user_settings", {})))


@app.post("/api/scoring/learned-weights/fit")
def fit_learned_weights_endpoint():
    user_settings = merged_user_settings(db.get_setting("user_settings", {}))
    try:
        fit_learned_weights(db, min_events=int(user_settings.get("learned_weights_min_events", 50)))
    except NotEnoughEvents as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return learned_status(db, user_settings)


# ----------------------------------------------------------------------------- Settings
SECRET_SETTINGS = ("openrouter_api_key",)


def _redact_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Secrets never leave the server; the client only learns whether one is stored (`<key>_set`)."""
    out = {k: v for k, v in values.items() if k not in SECRET_SETTINGS}
    for key in SECRET_SETTINGS:
        out[f"{key}_set"] = bool(str(values.get(key) or "").strip())
    return out


@app.get("/api/settings")
def get_user_settings():
    stored = db.get_setting("user_settings", {})
    return {"settings": _redact_settings(merged_user_settings(stored))}


@app.put("/api/settings")
def update_user_settings(payload: dict[str, Any]):
    current = db.get_setting("user_settings", {})
    merged = merged_user_settings(current)
    for key in SECRET_SETTINGS:
        payload.pop(f"{key}_set", None)
        # A client that never saw the secret sends it back empty; that must not wipe the stored one
        if key in payload and not str(payload[key] or "").strip():
            payload.pop(key)
    clean, errors, ignored = validate_user_settings(payload)
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Invalid settings: " + "; ".join(errors), "errors": errors})
    merged.update(clean)
    if merged.get("target_curated_min", 0) > merged.get("target_curated_max", 10**9):
        raise HTTPException(status_code=400, detail="target_curated_min must not exceed target_curated_max")
    db.set_setting("user_settings", merged)
    return {"settings": _redact_settings(merged), "ignored_keys": ignored}


# ----------------------------------------------------------------------------- RAG Prompt Director
import asyncio  # noqa: E402  (RAG-only imports kept in this section)
import logging  # noqa: E402
import uuid  # noqa: E402
from typing import Literal  # noqa: E402

from fastapi import Query  # noqa: E402

from pipeline.rag import auto_reindex as rag_auto_reindex  # noqa: E402
from pipeline.rag.evaluation import evaluate_retrieval  # noqa: E402
from pipeline.rag.generator import MAX_VARIATIONS, exemplar_subsets, variation_temperature  # noqa: E402
from pipeline.rag.history import ARCHETYPE_LINT_MODES, RagHistory  # noqa: E402
from pipeline.rag.image_embedder import embedder_for_index, load_image_bgr  # noqa: E402
from pipeline.rag.linter import lint_prompt, token_counter_name  # noqa: E402
from pipeline.rag.retriever import DENSE_MODES  # noqa: E402

rag_logger = logging.getLogger("pipeline.api.rag")
# History + favourites live in the main state DB (survive index rebuilds); see pipeline/rag/history.py
rag_history = RagHistory(settings.db_path)
RAG_BATCH_CONCURRENCY = 3
RAG_PROMPT_MAX_CHARS = 8000
RAG_PREVIEW_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
# R1: None = server default (per-domain when the index has domain vectors, else whole caption)
RagDenseMode = Literal["whole", "domain"]


def _rag_auto_reindex_enabled() -> bool:
    return bool(merged_user_settings(db.get_setting("user_settings", {})).get("rag_auto_reindex", True))


def _rag_after_caption_job(*, job_id: int, video_id: str, captioned: int = 0) -> None:
    """R6 hook: queue an incremental index update after a successful caption job (setting rag_auto_reindex).

    ``rag_index_job`` is looked up at call time; its on_finished callback invalidates the retriever.
    """
    if not _rag_auto_reindex_enabled():
        return
    rag_index_job.request_incremental()
    rag_logger.info("RAG incremental reindex queued after caption job %s (%s, %d captions)", job_id, video_id, captioned)


rag_auto_reindex.register(jobs, _rag_after_caption_job)


class RagRetrieveRequest(BaseModel):
    selected_tags: dict[str, list[str]] = Field(default_factory=dict)
    user_description: str = ""
    top_k: int = Field(default=4, ge=1, le=12)
    dense_mode: RagDenseMode | None = None


class RagGenerateRequest(BaseModel):
    selected_tags: dict[str, list[str]] = Field(default_factory=dict)
    user_description: str = ""
    character: str | None = None
    top_k: int = Field(default=4, ge=1, le=12)
    model: str = "google/gemma-4-31b-it"
    trigger: str = "restrained_elegance"
    local_url: str | None = None
    api_key: str | None = None
    # Batch "Generate N": variations use different exemplar subsets and jittered temperature
    n: int = Field(default=1, ge=1, le=MAX_VARIATIONS)
    dense_mode: RagDenseMode | None = None


class RagIndexRequest(BaseModel):
    limit: int | None = None
    # R6: embed only new/changed captions (full build when there is no compatible index)
    incremental: bool = False


class RagLintRequest(BaseModel):
    prompt: str = Field(max_length=RAG_PROMPT_MAX_CHARS)
    # a/b/c (archetype keys) or the linter's own modes
    archetype: Literal["a", "b", "c", "tags", "short", "dense"] = "b"
    trigger: str = "restrained_elegance"
    character: str | None = None


class RagHistoryPrompts(BaseModel):
    a: str | None = Field(default=None, max_length=RAG_PROMPT_MAX_CHARS)
    b: str | None = Field(default=None, max_length=RAG_PROMPT_MAX_CHARS)
    c: str | None = Field(default=None, max_length=RAG_PROMPT_MAX_CHARS)


class RagHistoryPatch(BaseModel):
    favorite: bool | None = None
    # Inline edits: replaced prompts are re-linted server-side and flagged `edited`
    prompts: RagHistoryPrompts | None = None


def _rag_retrieve_or_empty(
    selected_tags: dict[str, list[str]], user_description: str, top_k: int, dense_mode: str | None = None
) -> list[dict[str, Any]]:
    """Retrieve exemplars; a missing index yields [] (the UI shows a build prompt instead of an error)."""
    try:
        return rag_retriever.retrieve(selected_tags, user_description, top_k, dense_mode=dense_mode)
    except RagIndexNotReady:
        return []


def _rag_image_index_enabled() -> bool:
    return bool(merged_user_settings(db.get_setting("user_settings", {})).get("rag_image_index", True))


def _rag_image_embedder_name() -> str:
    """'siglip' | 'clip': the embedder a new image index would use (before one exists)."""
    name = getattr(rag_index_job.image_embedder, "name", None)
    return name if name in ("siglip", "clip") else "clip"


def _openrouter_key(explicit: str | None) -> str:
    """Request key, else the key saved in user settings, else OPENROUTER_API_KEY (env / .env via env.sh)."""
    if explicit and explicit.strip():
        return explicit.strip()
    stored = (db.get_setting("user_settings", {}) or {}).get("openrouter_api_key") or ""
    return str(stored).strip() or settings.openrouter_api_key.strip()


@app.get("/api/rag/categories")
def get_rag_categories():
    return {"categories": TAXONOMY_CATEGORIES}


@app.get("/api/rag/presets")
def get_rag_presets():
    return {"presets": CURATED_PRESETS}


@app.get("/api/rag/characters")
def get_rag_characters():
    return {"characters": CHARACTERS}


@app.get("/api/rag/status")
def get_rag_status():
    status = rag_index_job.status()
    status["openrouter_key_configured"] = bool(_openrouter_key(None))
    if status["ready"]:
        # Opening the Prompt Director polls this first: load the index + CLIP text model in the background
        rag_retriever.warm_async()
    domain_vectors = rag_retriever.has_domain_embeddings() if status["ready"] else False
    image_index = rag_retriever.image_index_status() if status["ready"] else {
        "ready": False, "count": 0, "embedder": None, "model": None,
    }
    status["retrieval"] = {
        "fusion": "rrf",
        "bm25": rag_retriever.lexical_ready,
        "token_counter": token_counter_name(),
        # R1: dense mode used when a request does not choose one ('whole' for an index without domain vectors)
        "dense_mode": rag_retriever.effective_dense_mode(None) if status["ready"] else "whole",
        "dense_modes": list(DENSE_MODES),
        "domain_embeddings": domain_vectors,
        # R3: "more like this frame" (GET /api/rag/similar)
        "image_index": {
            **image_index,
            "embedder": image_index["embedder"] or _rag_image_embedder_name(),
            "enabled": _rag_image_index_enabled(),
        },
    }
    status["auto_reindex"] = _rag_auto_reindex_enabled()
    return status


@app.post("/api/rag/index")
def build_rag_index(req: RagIndexRequest | None = None):
    req = req or RagIndexRequest()
    started = rag_index_job.start(limit=req.limit, incremental=req.incremental)
    if not started:
        raise HTTPException(status_code=409, detail="RAG index build already running")
    return rag_index_job.status()


@app.get("/api/rag/eval")
def eval_rag_retrieval(
    k: int = Query(default=4, ge=1, le=50),
    max_queries: int = Query(default=200, ge=1, le=5000),
    fusion: Literal["rrf", "linear"] = "rrf",
    query_words: int = Query(default=40, ge=0, le=500),
    dense_mode: RagDenseMode | None = None,
):
    """R10 retrieval metrics on the held-out val split (definitions: pipeline/rag/evaluation.py)."""
    try:
        result = evaluate_retrieval(
            rag_retriever, k=k, max_queries=max_queries, fusion=fusion, query_words=query_words, dense_mode=dense_mode
        )
    except RagIndexNotReady:
        raise HTTPException(status_code=404, detail="RAG index not built yet")
    return {**result, "index": rag_index_job.index_meta() or None}


@app.post("/api/rag/retrieve")
def retrieve_rag_context(req: RagRetrieveRequest):
    results = _rag_retrieve_or_empty(req.selected_tags, req.user_description, req.top_k, req.dense_mode)
    ready = rag_retriever.is_ready()
    return {
        "results": results,
        "index_ready": ready,
        "dense_mode": rag_retriever.effective_dense_mode(req.dense_mode) if ready else req.dense_mode or "whole",
    }


@app.get("/api/rag/similar")
def similar_rag_frames(frame_id: int, top_k: int = Query(default=8, ge=1, le=50)):
    """R3 "more like this frame": indexed captions whose image is closest to the frame's image.

    The frame image (``frames.full_path``, else ``preview_path``) is embedded with the embedder that built the
    image index (SigLIP, else CLIP); the frame's own caption is excluded. 404 unknown frame or no readable image;
    ``index_ready: false`` and no results when there is no image index.
    """
    frame = db.one("SELECT full_path, preview_path FROM frames WHERE id=?", (frame_id,))
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    image = next(
        (img for p in (frame.get("full_path"), frame.get("preview_path"))
         if p and (img := load_image_bgr(Path(p))) is not None),
        None,
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Frame image not found on disk")
    image_index = rag_retriever.image_index_status()
    if not image_index["ready"]:
        return {"results": [], "index_ready": False, "embedder": image_index["embedder"] or _rag_image_embedder_name()}
    try:
        embedder = embedder_for_index(rag_index_job.image_embedder, image_index["embedder"])
        query = embedder.embed_images([image])[0]
        results = rag_retriever.similar(query, top_k=top_k, exclude_frame_id=frame_id)
    except RagIndexNotReady:
        return {"results": [], "index_ready": False, "embedder": _rag_image_embedder_name()}
    except Exception as err:  # the index's embedder cannot be loaded, or its vectors do not match
        rag_logger.warning("RAG similar-frame search failed: %s", err)
        raise HTTPException(status_code=503, detail=f"Image search unavailable: {err}. Rebuild the RAG index.")
    return {"results": results, "index_ready": True, "embedder": image_index["embedder"]}


@app.post("/api/rag/generate")
async def generate_rag_prompts(req: RagGenerateRequest):
    n = req.n
    # Batches retrieve a larger pool so variations can draw on different exemplars
    pool_k = min(12, req.top_k * 2) if n > 1 else req.top_k
    pool = await asyncio.to_thread(
        _rag_retrieve_or_empty, req.selected_tags, req.user_description, pool_k, req.dense_mode
    )
    use_local = req.model == "local" or bool(req.local_url and req.local_url.strip())
    api_key = None if use_local else _openrouter_key(req.api_key)
    if not use_local and not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter API key missing: set OPENROUTER_API_KEY in .env, save it in the caption dialog, or use the local model.",
        )

    subsets = exemplar_subsets(len(pool), req.top_k, n)
    semaphore = asyncio.Semaphore(RAG_BATCH_CONCURRENCY)

    async def one(index: int) -> dict[str, Any]:
        async with semaphore:
            return await rag_generator.generate(
                retrieved_items=[pool[j] for j in subsets[index]],
                selected_tags=req.selected_tags,
                user_description=req.user_description,
                character=req.character,
                model=req.model,
                trigger=req.trigger,
                api_key=api_key,
                local_url=req.local_url if use_local else None,
                temperature=variation_temperature(index, n),
            )

    outcomes = await asyncio.gather(*(one(i) for i in range(n)), return_exceptions=True)
    results: list[tuple[int, dict[str, Any]]] = []
    errors: list[str] = []
    for index, outcome in enumerate(outcomes):
        if isinstance(outcome, RagUpstreamError):
            errors.append(str(outcome))
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            results.append((index, outcome))
    if not results:
        raise HTTPException(status_code=502, detail=errors[0])

    inputs = {
        "selected_tags": req.selected_tags,
        "user_description": req.user_description,
        "character": req.character,
        "model": req.model,
        "trigger": req.trigger,
        "top_k": req.top_k,
        "n": n,
        "local_url": req.local_url if use_local else None,
        "dense_mode": req.dense_mode,
    }
    batch_id = uuid.uuid4().hex
    entries: list[dict[str, Any]] = []
    for index, result in results:
        try:
            entries.append(rag_history.add(inputs, result, batch_id=batch_id, batch_index=index))
        except sqlite3.Error as err:  # history is a convenience; never lose the generated prompts over it
            rag_logger.warning("Could not store RAG history entry: %s", err)
            entries.append({
                "id": None, "created_at": time.time(), "favorite": False, "batch_id": batch_id,
                "batch_index": index, "inputs": inputs, "result": result,
            })

    first = dict(entries[0]["result"])
    if errors:
        first["notices"] = [*first.get("notices", []), f"{len(errors)} of {n} variations failed: {errors[0]}"]
    return {**first, "history_id": entries[0]["id"], "batch_id": batch_id, "variations": entries}


@app.post("/api/rag/lint")
def lint_rag_prompt(req: RagLintRequest):
    mode = ARCHETYPE_LINT_MODES.get(req.archetype, req.archetype)
    result = lint_prompt(req.prompt, archetype=mode, trigger=req.trigger, character=req.character)
    return {**result, "archetype": mode, "token_counter": token_counter_name()}


@app.get("/api/rag/history")
def list_rag_history(
    favorites: bool = False,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return rag_history.list(favorites=favorites, limit=limit, offset=offset)


@app.patch("/api/rag/history/{entry_id}")
def patch_rag_history(entry_id: int, req: RagHistoryPatch):
    prompts = {k: v for k, v in (req.prompts.model_dump() if req.prompts else {}).items() if v is not None}
    entry = rag_history.update(entry_id, favorite=req.favorite, prompts=prompts or None)
    if entry is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    return entry


@app.delete("/api/rag/history/{entry_id}")
def delete_rag_history(entry_id: int):
    if not rag_history.delete(entry_id):
        raise HTTPException(status_code=404, detail="History entry not found")
    return {"deleted": entry_id}


@app.get("/api/rag/preview/{caption_id}")
def get_rag_preview(caption_id: int):
    rag_db_path = settings.cache_dir / "rag_index.sqlite"
    if not rag_db_path.exists():
        raise HTTPException(status_code=404, detail="RAG index not built yet")
    with sqlite3.connect(rag_db_path) as conn:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT img_rel_path FROM rag_captions WHERE id=?", (caption_id,)).fetchone()
        if not r or not r["img_rel_path"]:
            raise HTTPException(status_code=404, detail="Preview not found")
        # Relative to the project root (exports under data/finished), or absolute (cache frames of table rows)
        full_path = settings.project_root / r["img_rel_path"]
        if not full_path.is_file():
            raise HTTPException(status_code=404, detail="Image file not found on disk")
        return FileResponse(full_path, media_type=RAG_PREVIEW_MEDIA.get(full_path.suffix.lower(), "image/png"))


class MossExpandPromptRequest(BaseModel):
    idea: str
    dataset_id: str | None = None
    video_id: str | None = None
    mode: str = "CHARACTER"
    trigger_phrase: str = ""
    class_noun: str = "woman"
    locked_traits: list[str] | None = None
    use_llm: bool = False
    openrouter_key: str | None = None


class MossIndexCaptionItem(BaseModel):
    frame_id: int
    video_id: str = ""
    trigger: str = ""
    mode: str = "CHARACTER"
    long: str = ""
    medium: str = ""
    short: str = ""
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None


class MossIndexRequest(BaseModel):
    captions: list[MossIndexCaptionItem] | None = None
    video_id: str | None = None
    reindex_all: bool = False


@app.post("/api/rag/expand-prompt")
def expand_moss_prompt_endpoint(req: MossExpandPromptRequest):
    video_id = req.dataset_id or req.video_id
    api_key = req.openrouter_key or _openrouter_key(None)
    return expand_moss_prompt(
        db=db,
        idea=req.idea,
        video_id_or_dataset=video_id,
        mode=req.mode or "CHARACTER",
        trigger_phrase=req.trigger_phrase or "",
        class_noun=req.class_noun or "woman",
        locked_traits=req.locked_traits,
        use_llm=req.use_llm,
        openrouter_key=api_key,
    )


def _index_provided_captions(database: DB, captions: list[MossIndexCaptionItem]) -> int:
    count = 0
    for c in captions:
        emb = np.array(c.embedding, dtype=np.float32) if c.embedding else None
        index_moss_caption(
            db=database,
            frame_id=c.frame_id,
            video_id=c.video_id,
            trigger=c.trigger,
            mode=c.mode,
            long=c.long,
            medium=c.medium,
            short=c.short,
            tags=c.tags,
            embedding=emb,
        )
        count += 1
    return count


def _index_existing_db_captions(database: DB, video_id: str | None) -> int:
    count = 0
    where_clause = "WHERE video_id=?" if video_id else ""
    args = (video_id,) if video_id else ()
    rows = database.all(
        f"SELECT frame_id, video_id, text, trigger, mode, caption_long, caption_medium, caption_short FROM captions {where_clause}",
        args,
    )
    for r in rows:
        long_c = str(r.get("caption_long") or r.get("text") or "")
        med_c = str(r.get("caption_medium") or r.get("text") or "")
        short_c = str(r.get("caption_short") or r.get("text") or "")
        index_moss_caption(
            db=database,
            frame_id=int(r["frame_id"]),
            video_id=str(r.get("video_id") or ""),
            trigger=str(r.get("trigger") or ""),
            mode=str(r.get("mode") or "CHARACTER"),
            long=long_c,
            medium=med_c,
            short=short_c,
            tags=[],
        )
        count += 1
    return count


@app.post("/api/rag/index-moss")
def index_moss_endpoint(req: MossIndexRequest | None = None):
    req = req or MossIndexRequest()
    if req.captions:
        count = _index_provided_captions(db, req.captions)
    else:
        count = _index_existing_db_captions(db, req.video_id)
    return {"status": "ok", "indexed_count": count}


@app.get("/api/rag/exemplars")
def get_moss_exemplars_endpoint(
    query: str = Query(default="", description="Search query or idea"),
    idea: str | None = Query(default=None, description="Alias for query"),
    video_id: str | None = None,
    dataset_id: str | None = None,
    mode: str | None = None,
    top_k: int = Query(default=3, ge=1, le=50),
):
    q = query or idea or ""
    vid = video_id or dataset_id
    exemplars = retrieve_moss_exemplars(
        db=db,
        query_text=q,
        top_k=top_k,
        video_id=vid,
        mode=mode,
    )
    return {"exemplars": exemplars, "count": len(exemplars), "query": q}


# ----------------------------------------------------------------------------- Static Web
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# CP_WEB_DIST points at a build elsewhere (a Docker layer, or a private build while `ng build` rewrites src/web/dist)
DIST_CANDIDATES = tuple(
    Path(p) for p in (os.environ.get("CP_WEB_DIST"),) if p
) + (WEB_DIR / "dist" / "browser", WEB_DIR / "dist")
FRONTEND_MISSING_HTML = (
    "<!doctype html><title>Frontend not built</title>"
    "<h1>Frontend not built</h1><p>The Angular build (<code>src/web/dist</code>) is missing or being rebuilt. "
    "Run <code>bash scripts/build_web.sh</code> (or <code>npm run build</code>) and reload.</p>"
)


def _dist_dir() -> Path | None:
    """The built Angular app. Resolved per request, so a rebuild is picked up without restarting the server.

    The raw sources in `src/web` are never served: without a build the browser would get TypeScript and a
    blank page (GAP report section 5).
    """
    for d in DIST_CANDIDATES:
        if (d / "index.html").is_file():
            return d
    return None


if _dist_dir() is None:
    import logging

    logging.getLogger("uvicorn.error").warning(
        "Frontend build missing (%s): the UI answers 503 until `bash scripts/build_web.sh` has run", DIST_CANDIDATES[0]
    )

app.mount("/static", StaticFiles(directory=str(DIST_CANDIDATES[0]), check_dir=False), name="static")


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    # Unknown API routes must 404 as JSON, not fall through to index.html (200 HTML breaks res.json())
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"Unknown API route: /{full_path}")
    dist = _dist_dir()
    if dist is None:
        return HTMLResponse(FRONTEND_MISSING_HTML, status_code=503)
    # Path params are not normalised: `..` segments (raw or %2e%2e) must not escape the web root
    static_root = dist.resolve()
    file_path = (static_root / full_path).resolve()
    if full_path and file_path.is_relative_to(static_root) and file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(dist / "index.html")
