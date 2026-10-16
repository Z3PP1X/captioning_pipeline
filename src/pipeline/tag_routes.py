"""REST routes for learn-as-you-tag image tagging (see `pipeline.image_tags`)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline.image_tags import TAG_CATEGORIES, ImageTagEngine
from pipeline.jobs import JobConflictError


class Box(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class TagCreate(BaseModel):
    name: str
    category: str = "object"
    prompt: str | None = None
    color: str | None = None


class TagPatch(BaseModel):
    name: str | None = None
    category: str | None = None
    prompt: str | None = None
    color: str | None = None


class TagMerge(BaseModel):
    into_tag_id: int


class RegionCreate(BaseModel):
    box: Box
    tag_id: int | None = None
    tag_name: str | None = None
    category: str = "object"
    polarity: str = "positive"


class SuggestionDecision(BaseModel):
    box: Box | None = None


def build_tag_router(engine: ImageTagEngine, jobs: Any) -> APIRouter:
    """`jobs` is the JobManager: video-wide suggestion runs are jobs, and changes are broadcast over SSE."""
    router = APIRouter(prefix="/api")

    def _video_exists(video_id: str) -> None:
        if not engine.db.one("SELECT id FROM videos WHERE id=?", (video_id,)):
            raise HTTPException(status_code=404, detail="Video not found")

    def _learned(result: dict[str, Any]) -> dict[str, Any]:
        """After an exemplar changed: re-score the cached frames of the video so the grid reflects it at once."""
        video_id = result.get("video_id")
        if video_id:
            res = engine.suggest_for_video(video_id, compute_missing=False)
            jobs.broadcast_event("tags_updated", {"video_id": video_id, "tag_id": result.get("tag_id"), **res})
            # Frames never opened have no region embeddings yet: embed them in the background (once), so the
            # rest of the video gets suggestions without the user starting "Auto-tag"
            if engine.uncached_frame_count(video_id):
                try:
                    result["auto_tag_job_id"] = jobs.submit_tag_suggest_job(video_id)
                except JobConflictError:
                    pass  # another job holds the video; the next learning step tries again
        tag = engine.get_tag(result["tag_id"]) if result.get("tag_id") else None
        frame = engine.frame_tags(result["frame_id"]) if result.get("frame_id") else None
        return {"status": "ok", **result, "tag": tag, "frame": frame}

    # ------------------------------------------------------------------ tag vocabulary
    @router.get("/tags")
    def list_tags():
        tags = engine.list_tags()
        return {"tags": tags, "total": len(tags), "categories": list(TAG_CATEGORIES)}

    @router.post("/tags")
    def create_tag(req: TagCreate):
        try:
            return {"tag": engine.create_tag(req.name, req.category, req.prompt, req.color)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/tags/{tag_id}")
    def get_tag(tag_id: int):
        tag = engine.get_tag(tag_id)
        if not tag:
            raise HTTPException(status_code=404, detail="Tag not found")
        return {"tag": tag, "regions": engine.list_regions(tag_id)}

    @router.patch("/tags/{tag_id}")
    def patch_tag(tag_id: int, req: TagPatch):
        try:
            tag = engine.update_tag(tag_id, **req.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        if not tag:
            raise HTTPException(status_code=404, detail="Tag not found")
        return {"tag": tag}

    @router.delete("/tags/{tag_id}")
    def delete_tag(tag_id: int):
        if not engine.delete_tag(tag_id):
            raise HTTPException(status_code=404, detail="Tag not found")
        jobs.broadcast_event("tags_updated", {"video_id": None, "tag_id": tag_id, "deleted": True})
        return {"status": "ok", "deleted": True}

    @router.post("/tags/{tag_id}/merge")
    def merge_tag(tag_id: int, req: TagMerge):
        tag = engine.merge_tags(tag_id, req.into_tag_id)
        if not tag:
            raise HTTPException(status_code=400, detail="Cannot merge: unknown tag or same tag")
        jobs.broadcast_event("tags_updated", {"video_id": None, "tag_id": req.into_tag_id})
        return {"tag": tag}

    @router.get("/tag-regions/{region_id}/crop")
    def region_crop(region_id: int):
        r = engine.db.one("SELECT crop_path FROM tag_regions WHERE id=?", (region_id,))
        if not r or not r.get("crop_path") or not Path(r["crop_path"]).is_file():
            raise HTTPException(status_code=404, detail="Region crop not found")
        return FileResponse(Path(r["crop_path"]), media_type="image/jpeg")

    @router.delete("/tag-regions/{region_id}")
    def delete_region(region_id: int):
        r = engine.db.one("SELECT tag_id, video_id FROM tag_regions WHERE id=?", (region_id,))
        if not r or not engine.delete_region(region_id):
            raise HTTPException(status_code=404, detail="Region not found")
        return _learned({"tag_id": r["tag_id"], "video_id": r["video_id"], "region_id": region_id})

    # ------------------------------------------------------------------ per frame
    @router.get("/frames/{frame_id}/tags")
    def frame_tags(frame_id: int, live: bool = True):
        try:
            return engine.frame_tags(frame_id, compute=live)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/frames/{frame_id}/tags")
    def add_region(frame_id: int, req: RegionCreate):
        """Mark a region of the frame as showing a tag (or, with polarity 'negative', as NOT showing it)."""
        try:
            res = engine.add_region(
                frame_id, req.box.model_dump(), tag_id=req.tag_id, tag_name=req.tag_name,
                polarity=req.polarity, source="manual", category=req.category,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _learned(res)

    @router.post("/frames/{frame_id}/tags/{tag_id}/accept")
    def accept(frame_id: int, tag_id: int, req: SuggestionDecision | None = None):
        try:
            res = engine.accept_suggestion(frame_id, tag_id, req.box.model_dump() if req and req.box else None)
        except (LookupError, FileNotFoundError) as e:
            raise HTTPException(status_code=404, detail=str(e))
        return _learned(res)

    @router.post("/frames/{frame_id}/tags/{tag_id}/reject")
    def reject(frame_id: int, tag_id: int, req: SuggestionDecision | None = None):
        try:
            res = engine.reject_suggestion(frame_id, tag_id, req.box.model_dump() if req and req.box else None)
        except (LookupError, FileNotFoundError) as e:
            raise HTTPException(status_code=404, detail=str(e))
        return _learned(res)

    @router.delete("/frames/{frame_id}/tags/{tag_id}")
    def remove(frame_id: int, tag_id: int):
        frame = engine.db.one("SELECT video_id FROM frames WHERE id=?", (frame_id,))
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")
        if not engine.remove_frame_tag(frame_id, tag_id):
            raise HTTPException(status_code=404, detail="Frame has no such tag")
        return _learned({"tag_id": tag_id, "frame_id": frame_id, "video_id": frame["video_id"]})

    # ------------------------------------------------------------------ per video
    @router.post("/videos/{video_id}/suggest-tags")
    def suggest_video(video_id: str):
        """Background job: embed all frames as region windows, then store suggestions for every tag."""
        _video_exists(video_id)
        if not engine.db.one("SELECT id FROM image_tags LIMIT 1"):
            raise HTTPException(status_code=400, detail="No tags yet: mark a region on a frame first")
        job_id = jobs.submit_tag_suggest_job(video_id)
        return {"job_id": job_id, "status": "queued"}

    @router.get("/videos/{video_id}/tags")
    def video_tags(video_id: str):
        _video_exists(video_id)
        return {"video_id": video_id, "tags": engine.video_summary(video_id)}

    return router
