"""FastAPI web application and REST API for the video frame captioning pipeline."""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pipeline.autochunk import AutoChunker
from pipeline.config import DEFAULT_USER_SETTINGS, Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.export import Exporter
from pipeline.indexer import Indexer
from pipeline.jobs import JobManager
from pipeline.select import select_diverse_top_k
from pipeline.streaming import stream_video

settings = get_settings()
db = DB(settings.db_path)
indexer = Indexer(db, settings)
jobs = JobManager(db, settings)
exporter = Exporter(db, settings)
autochunker = AutoChunker(db, settings)


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
        conn.execute("UPDATE videos SET status='chunked' WHERE status IN ('extracting', 'extracting_750')")
    indexer.scan_and_sync()
    yield


app = FastAPI(title="Captioning Pipeline API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    rule: str = "person"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


class ChunksUpdate(BaseModel):
    chunks: list[ChunkItem]


class SuggestChunksRequest(BaseModel):
    min_len_s: float = 4.0
    k: int = 5
    rule: str = "person"


class JobCreate(BaseModel):
    video_id: str
    chunk_id: int | None = None


class PickPatch(BaseModel):
    state: str | None = None  # keep, reject, star, candidate
    role: str | None = None   # pick, runner_up
    crop: dict[str, float] | None = None


class OpenRouterCaptionRequest(BaseModel):
    api_key: str | None = None
    model: str = "google/gemma-4-31b-it"
    fallback_layer2: str = "deepseek/deepseek-v4.1-flash"
    fallback_layer3: str = "x-ai/grok-4.7"
    reasoning_effort: str = "high"
    mode: str = "concept"
    trigger: str = "restrained_elegance"
    class_noun: str = "woman"
    max_tokens: int = 300
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


# ----------------------------------------------------------------------------- Videos
@app.get("/api/videos")
def list_videos(status: str | None = None, search: str | None = None):
    query = """
        SELECT v.*,
            (SELECT COUNT(*) FROM chunks c WHERE c.video_id = v.id) as chunk_count,
            (SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id = f.id WHERE f.video_id = v.id) as export_count
        FROM videos v
        WHERE v.missing = 0
    """
    params: list[Any] = []

    if status:
        query += " AND v.status = ?"
        params.append(status)

    if search:
        query += " AND (v.name LIKE ? OR v.slug LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like])

    query += " ORDER BY v.name ASC"
    videos = db.all(query, tuple(params))
    return {"videos": videos, "total": len(videos)}


@app.post("/api/videos/scan")
def scan_videos():
    vids = indexer.scan_and_sync()
    return {"scanned": len(vids)}


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
    p = settings.filmstrip_dir / video_id / filename
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(p, media_type="image/webp")


# ----------------------------------------------------------------------------- Chunks
@app.get("/api/videos/{video_id}/chunks")
def get_chunks(video_id: str):
    chunks = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC, start_s ASC", (video_id,))
    return {"chunks": chunks}


@app.put("/api/videos/{video_id}/chunks")
def save_chunks(video_id: str, update: ChunksUpdate):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        saved = autochunker.save_user_chunks(video_id, [c.model_dump() for c in update.chunks])
        return {"chunks": saved}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/videos/{video_id}/chunks/suggest")
def suggest_chunks(video_id: str, req: SuggestChunksRequest = None):
    vid = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")

    # Generates maximum 15 balanced scene chunks with budget percentages summing to 100%
    chunks = autochunker.generate_balanced_chunks(video_id, target_frames=300)
    return {"suggested": chunks}


@app.post("/api/videos/{video_id}/extract-750")
def trigger_extract_750(video_id: str):
    vid = db.one("SELECT id FROM videos WHERE id=?", (video_id,))
    if not vid:
        raise HTTPException(status_code=404, detail="Video not found")
    job_id = jobs.submit_extract_750_job(video_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/videos/{video_id}/curation-300")
def get_curation_300(video_id: str):
    video = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    chunks = db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))
    picks = db.all(
        """
        SELECT f.id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.preview_path, f.full_path,
               p.role, p.rank, p.state, p.crop, c.position as chunk_pos, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ? AND p.role = 'pick'
        ORDER BY c.position ASC, p.rank ASC, f.t ASC
        """,
        (video_id,),
    )
    runners = db.all(
        """
        SELECT f.id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.preview_path, f.full_path,
               p.role, p.rank, p.state, p.crop, c.position as chunk_pos, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ? AND p.role = 'runner_up'
        ORDER BY c.position ASC, p.rank ASC, f.t ASC
        """,
        (video_id,),
    )

    stats = jobs.scoring_engine.compute_distribution_stats(picks)
    for f in picks + runners:
        f["preview_url"] = f"/api/frames/{f['id']}/preview"
        f["full_url"] = f"/api/frames/{f['id']}/full"

    return {
        "video": video,
        "chunks": chunks,
        "picks": picks,
        "runners": runners,
        "stats": stats,
        "distribution": stats,
    }


@app.post("/api/videos/{video_id}/swap-frame")
def swap_frame(video_id: str, req: SwapFrameRequest):
    drop_id = req.target_drop_id
    drop_pick = db.one("SELECT * FROM picks WHERE frame_id=?", (drop_id,))
    if not drop_pick:
        raise HTTPException(status_code=404, detail="Frame not found in picks")

    chunk_id = drop_pick["chunk_id"]
    pick_id = req.pick_frame_id
    if not pick_id:
        best_cand = db.one(
            """
            SELECT p.frame_id FROM picks p
            JOIN frames f ON p.frame_id = f.id
            WHERE p.chunk_id=? AND p.role='runner_up'
            ORDER BY p.rank ASC LIMIT 1
            """,
            (chunk_id,),
        )
        if not best_cand:
            raise HTTPException(status_code=400, detail="No replacement candidates available in this chunk")
        pick_id = best_cand["frame_id"]

    with db.write() as conn:
        conn.execute("UPDATE picks SET role='runner_up', state='candidate' WHERE frame_id=?", (drop_id,))
        conn.execute("UPDATE picks SET role='pick', state='keep' WHERE frame_id=?", (pick_id,))

    picks = db.all(
        """
        SELECT f.id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.preview_path, f.full_path,
               p.role, p.rank, p.state, p.crop, c.position as chunk_pos, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ? AND p.role = 'pick'
        ORDER BY c.position ASC, p.rank ASC, f.t ASC
        """,
        (video_id,),
    )
    runners = db.all(
        """
        SELECT f.id, f.chunk_id, f.t, f.metrics, f.zs_scores, f.preview_path, f.full_path,
               p.role, p.rank, p.state, p.crop, c.position as chunk_pos, c.budget_pct
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ? AND p.role = 'runner_up'
        ORDER BY c.position ASC, p.rank ASC, f.t ASC
        """,
        (video_id,),
    )
    for f in picks + runners:
        f["preview_url"] = f"/api/frames/{f['id']}/preview"
        f["full_url"] = f"/api/frames/{f['id']}/full"

    stats = jobs.scoring_engine.compute_distribution_stats(picks)
    return {
        "status": "ok",
        "swapped_in": {"id": pick_id},
        "swapped_out": {"id": drop_id},
        "dropped": drop_id,
        "picks": picks,
        "runners": runners,
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


@app.get("/api/openrouter/models")
def list_openrouter_models():
    return {
        "models": [
            {
                "id": "google/gemma-4-31b-it",
                "name": "Google Gemma 4 31B (Default, High Reasoning, max 300 tokens)",
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
               f.preview_path, p.role, p.rank, p.state, p.crop,
               c.position AS chunk_position, c.start_s AS chunk_start,
               c.end_s AS chunk_end, c.rule AS chunk_rule, c.tags AS chunk_tags,
               c.notes AS chunk_notes
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        LEFT JOIN chunks c ON f.chunk_id = c.id
        WHERE f.video_id = ?
        ORDER BY c.position ASC, f.chunk_id ASC, p.role DESC, p.rank ASC
    """
    frames = db.all(query, (video_id,))
    for f in frames:
        f["preview_url"] = f"/api/frames/{f['id']}/preview"
        f["full_url"] = f"/api/frames/{f['id']}/full"
    return {"frames": frames}


@app.get("/api/chunks/{chunk_id}/candidates")
def get_chunk_candidates(chunk_id: int):
    query = """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.zs_scores,
               f.preview_path, p.role, p.rank, p.state, p.crop,
               c.position AS chunk_position, c.start_s AS chunk_start,
               c.end_s AS chunk_end, c.rule AS chunk_rule, c.tags AS chunk_tags,
               c.notes AS chunk_notes
        FROM frames f
        JOIN picks p ON f.id = p.frame_id
        LEFT JOIN chunks c ON f.chunk_id = c.id
        WHERE f.chunk_id = ?
        ORDER BY p.role DESC, p.rank ASC
    """
    frames = db.all(query, (chunk_id,))
    for f in frames:
        f["preview_url"] = f"/api/frames/{f['id']}/preview"
        f["full_url"] = f"/api/frames/{f['id']}/full"
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
        # Export all curated videos
        curated = db.all("SELECT id FROM videos WHERE status IN ('curating', 'chunked', 'exported')")
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
@app.get("/api/settings")
def get_user_settings():
    stored = db.get_setting("user_settings", {})
    return {"settings": merged_user_settings(stored)}


@app.put("/api/settings")
def update_user_settings(payload: dict[str, Any]):
    current = db.get_setting("user_settings", {})
    merged = merged_user_settings(current)
    merged.update(payload)
    db.set_setting("user_settings", merged)
    return {"settings": merged}


# ----------------------------------------------------------------------------- Static Web
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def serve_index():
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"status": "API running", "web": "frontend not yet mounted"})
