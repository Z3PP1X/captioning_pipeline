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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.autochunk import PRIORITIES, AutoChunker, DestructiveRechunkError
from pipeline.autopilot import AutopilotConfig
from pipeline.config import DEFAULT_USER_SETTINGS, Settings, VIDEO_STATUSES, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.decode import ensure_native_frame, sample_frames_at
from pipeline.export import Exporter
from pipeline.face import person_embedding
from pipeline.indexer import Indexer
from pipeline.jobs import JobConflictError, JobManager
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
)
from pipeline.reconcile import get_video_scenes_caption_stats, reconcile_captions_and_state
from pipeline.select import select_diverse_top_k
from pipeline.streaming import stream_video

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


@app.exception_handler(JobConflictError)
def job_conflict_handler(request: Request, exc: JobConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ----------------------------------------------------------------------------- Models
class VideoPatch(BaseModel):
    status: str | None = None
    watermark_rect: dict[str, float] | None = None
    letterbox: dict[str, float] | None = None


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
    mode: str = "concept"
    trigger: str = "restrained_elegance"
    class_noun: str = "woman"
    max_tokens: int = DEFAULT_CAPTION_MAX_TOKENS
    temperature: float = 0.80
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
    for v in videos:
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

    if updates:
        params.append(video_id)
        with db.write() as conn:
            conn.execute(f"UPDATE videos SET {', '.join(updates)} WHERE id = ?", tuple(params))

    return {"video": db.one("SELECT * FROM videos WHERE id=?", (video_id,))}


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


@app.post("/api/videos/{video_id}/target-person")
def set_video_target_person_endpoint(video_id: str, req: VideoTargetPersonRequest):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    updated = db.set_video_target_person(video_id, req.person_id)
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

    # Re-match all candidate frames for this video with the person embedding
    target_emb = emb
    all_frames = db.all("SELECT id, preview_path, full_path, zs_scores FROM frames WHERE video_id=?", (video_id,))
    updated_count = 0

    with db.write() as conn:
        for f in all_frames:
            fpath = f.get("preview_path") or f.get("full_path")
            if not fpath or not Path(fpath).is_file():
                continue
            f_img = cv2.imread(fpath)
            if f_img is None:
                continue
            face_res = jobs.face_engine.match_frame(f_img, target_emb)
            zs = json.loads(f["zs_scores"]) if f.get("zs_scores") and isinstance(f["zs_scores"], str) else (f.get("zs_scores") or {})
            zs["target_person_sim"] = face_res["target_person_sim"]
            zs["target_person_matched"] = face_res["matched"]
            zs["face_count"] = face_res["face_count"]
            zs["faces"] = face_res["faces"]
            conn.execute("UPDATE frames SET zs_scores=? WHERE id=?", (json.dumps(zs), f["id"]))
            updated_count += 1

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


def _enrich_frame_dict(f: dict[str, Any], exported_ids: set[int] | None = None) -> dict[str, Any]:
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
    for f in picks + runners:
        _enrich_frame_dict(f, exported_ids)

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

    # Ensure swapped-in pick has native full-resolution decode (C1/H1)
    ensure_native_frame(db, pick_id)

    picks = _fetch_curation_frames(video_id, "pick")
    runners = _fetch_curation_frames(video_id, "runner_up")
    exported_ids = {r["frame_id"] for r in db.all("SELECT e.frame_id FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (video_id,))}
    for f in picks + runners:
        _enrich_frame_dict(f, exported_ids)

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
    job_id = jobs.submit_openrouter_caption_job(video_id, req.model_dump())
    return {"job_id": job_id, "status": "queued"}


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
    for f in frames:
        _enrich_frame_dict(f, exported_ids)

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

    with db.write() as conn:
        cur = conn.execute("UPDATE picks SET state=? WHERE frame_id=?", (req.state, frame_id))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO picks (frame_id, chunk_id, role, rank, state) VALUES (?, ?, 'runner_up', 999, ?)",
                (frame_id, frame["chunk_id"], req.state),
            )

    if req.state in ("keep", "star"):
        ensure_native_frame(db, frame_id)

    return {"status": "ok", "frame_id": frame_id, "state": req.state}


@app.post("/api/videos/{video_id}/curate/keep_all")
def bulk_keep_all_picks(video_id: str):
    with db.write() as conn:
        conn.execute(
            """
            UPDATE picks SET state='keep'
            WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?) AND role='pick'
            """,
            (video_id,),
        )
    return {"status": "ok", "video_id": video_id}


@app.post("/api/videos/{video_id}/curate/reject_all")
def bulk_reject_all_picks(video_id: str):
    with db.write() as conn:
        conn.execute(
            """
            UPDATE picks SET state='reject'
            WHERE frame_id IN (SELECT id FROM frames WHERE video_id=?) AND role='pick'
            """,
            (video_id,),
        )
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
    for f in frames:
        _enrich_frame_dict(f, exported_ids)
    return {"frames": frames}


@app.patch("/api/picks/{frame_id}")
def update_pick(frame_id: int, patch: PickPatch):
    existing = db.one("SELECT * FROM picks WHERE frame_id=?", (frame_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Pick not found")

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
    merged.update(payload)
    db.set_setting("user_settings", merged)
    return {"settings": _redact_settings(merged)}


# ----------------------------------------------------------------------------- RAG Prompt Director
class RagRetrieveRequest(BaseModel):
    selected_tags: dict[str, list[str]] = Field(default_factory=dict)
    user_description: str = ""
    top_k: int = Field(default=4, ge=1, le=12)


class RagGenerateRequest(BaseModel):
    selected_tags: dict[str, list[str]] = Field(default_factory=dict)
    user_description: str = ""
    character: str | None = None
    top_k: int = Field(default=4, ge=1, le=12)
    model: str = "google/gemma-4-31b-it"
    trigger: str = "restrained_elegance"
    local_url: str | None = None
    api_key: str | None = None


class RagIndexRequest(BaseModel):
    limit: int | None = None


def _rag_retrieve_or_empty(selected_tags: dict[str, list[str]], user_description: str, top_k: int) -> list[dict[str, Any]]:
    """Retrieve exemplars; a missing index yields [] (the UI shows a build prompt instead of an error)."""
    try:
        return rag_retriever.retrieve(selected_tags, user_description, top_k)
    except RagIndexNotReady:
        return []


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
    return status


@app.post("/api/rag/index")
def build_rag_index(req: RagIndexRequest | None = None):
    started = rag_index_job.start(limit=req.limit if req else None)
    if not started:
        raise HTTPException(status_code=409, detail="RAG index build already running")
    return rag_index_job.status()


@app.post("/api/rag/retrieve")
def retrieve_rag_context(req: RagRetrieveRequest):
    results = _rag_retrieve_or_empty(req.selected_tags, req.user_description, req.top_k)
    return {"results": results, "index_ready": rag_retriever.is_ready()}


@app.post("/api/rag/generate")
async def generate_rag_prompts(req: RagGenerateRequest):
    retrieved = _rag_retrieve_or_empty(req.selected_tags, req.user_description, req.top_k)
    use_local = req.model == "local" or bool(req.local_url and req.local_url.strip())
    api_key = None if use_local else _openrouter_key(req.api_key)
    if not use_local and not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter API key missing: set OPENROUTER_API_KEY in .env, save it in the caption dialog, or use the local model.",
        )
    try:
        result = await rag_generator.generate(
            retrieved_items=retrieved,
            selected_tags=req.selected_tags,
            user_description=req.user_description,
            character=req.character,
            model=req.model,
            trigger=req.trigger,
            api_key=api_key,
            local_url=req.local_url if use_local else None,
        )
    except RagUpstreamError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return result


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
        full_path = settings.project_root / r["img_rel_path"]
        if not full_path.is_file():
            raise HTTPException(status_code=404, detail="Image file not found on disk")
        return FileResponse(full_path, media_type="image/png")


# ----------------------------------------------------------------------------- Static Web
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DIST_DIR = WEB_DIR / "dist" / "browser"
if not DIST_DIR.exists():
    DIST_DIR = WEB_DIR / "dist"

STATIC_DIR = DIST_DIR if (DIST_DIR / "index.html").exists() else WEB_DIR

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    # Unknown API routes must 404 as JSON, not fall through to index.html (200 HTML breaks res.json())
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"Unknown API route: /{full_path}")
    # Path params are not normalised: `..` segments (raw or %2e%2e) must not escape the web root
    static_root = STATIC_DIR.resolve()
    file_path = (static_root / full_path).resolve()
    if full_path and file_path.is_relative_to(static_root) and file_path.is_file():
        return FileResponse(file_path)
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"status": "API running", "web": "frontend not yet mounted"})
