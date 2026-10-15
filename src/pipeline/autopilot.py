"""Autopilot: from a short configuration to a curated, de-duplicated frame set ready for captioning.

The human supplies only a configuration (time ranges to leave out, the person to prioritise); the pipeline
then runs

    plan  ->  (human prioritises the scenes)  ->  extract & score  ->  re-evaluate similarity  ->  ready

* **plan** (`autopilot_plan` job): scene cuts are detected around the excluded ranges; with a target person
  a handful of frames per scene is face-matched to measure how much the person is on screen, which sets a
  suggested priority (high / normal / low) and with it the frame budget of every scene.
* **review** (human in the loop): the user raises, lowers or skips scenes and approves. Skipped this way:
  nothing is sampled from them. With `review_chunks=false` the pipeline continues unattended.
* **extract**: the existing `extract_750` job (candidate sampling, face matching, scoring, MMR reduction).
* **refine** (`autopilot_refine` job): `pipeline.refine` re-evaluates the similarity of the chosen frames
  across the whole set, swaps near-duplicates and off-target faces for runner-ups and stores a report.

State lives in `videos.autopilot` (JSON) so the UI, the API and restarts all see the same stage.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
from pydantic import BaseModel, Field, field_validator

from pipeline.autochunk import AutoChunker, DestructiveRechunkError, normalize_ranges
from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.decode import sample_frames_at
from pipeline.export import apply_letterbox_crop, apply_watermark_crop
from pipeline.face import person_embedding
from pipeline.refine import RefineConfig, Refiner

if TYPE_CHECKING:  # pragma: no cover
    from pipeline.jobs import JobManager

STAGES = ("idle", "planning", "awaiting_review", "extracting", "refining", "ready", "error", "cancelled")
ACTIVE_STAGES = ("planning", "extracting", "refining")
# Scenes where the target person is on screen in at least this share of the probed frames
HIGH_PRESENCE = 0.45
LOW_PRESENCE = 0.10

_TS = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)\s*$")


def parse_timestamp(value: Any) -> float:
    """`90`, `"90.5"`, `"1:30"` or `"1:02:03"` -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _TS.match(str(value))
    if not m:
        raise ValueError(f"Invalid timestamp '{value}' (use seconds, mm:ss or hh:mm:ss)")
    a, b, c = m.groups()
    parts = [float(p) for p in (a, b) if p is not None]
    # "1:30" parses as groups (1, None, 30); "1:02:03" as (1, 02, 03)
    return sum(p * 60 ** (len(parts) - i) for i, p in enumerate(parts)) + float(c)


class TimeRange(BaseModel):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)

    @field_validator("end_s")
    @classmethod
    def _ordered(cls, end: float, info: Any) -> float:
        if end <= info.data.get("start_s", 0.0):
            raise ValueError("end_s must be greater than start_s")
        return end


def _coerce_range(raw: Any) -> Any:
    if isinstance(raw, TimeRange):
        return raw
    if isinstance(raw, str):
        sep = re.split(r"\s*(?:-|–|—|\bto\b)\s*", raw.strip(), maxsplit=1)
        if len(sep) != 2:
            raise ValueError(f"Invalid range '{raw}' (use 'start-end', e.g. 0:00-1:30)")
        return {"start_s": parse_timestamp(sep[0]), "end_s": parse_timestamp(sep[1])}
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return {"start_s": parse_timestamp(raw[0]), "end_s": parse_timestamp(raw[1])}
    if isinstance(raw, dict):
        out = dict(raw)
        for key, alt in (("start_s", "start"), ("end_s", "end")):
            if key not in out and alt in out:
                out[key] = out.pop(alt)
            if key in out:
                out[key] = parse_timestamp(out[key])
        return out
    return raw


class AutopilotConfig(BaseModel):
    """What the user hands the pipeline. Everything but the ranges and the person is optional tuning."""

    exclude_ranges: list[TimeRange] = Field(default_factory=list)
    target_person_id: int | None = None
    review_chunks: bool | None = None  # None -> user setting `autopilot.review_chunks` (default: ask the human)
    target_frames: int | None = Field(default=None, ge=5, le=2000)
    person_focus: float | None = Field(default=None, ge=0.0, le=1.0)
    dup_similarity: float | None = Field(default=None, ge=0.5, le=1.0)
    probe_frames: int | None = Field(default=None, ge=3, le=60)
    # Replacing existing chunks deletes their frames, picks and export records
    confirm_destructive: bool = False

    @field_validator("exclude_ranges", mode="before")
    @classmethod
    def _parse_ranges(cls, value: Any) -> Any:
        return [_coerce_range(r) for r in (value or [])]

    def stored(self) -> dict[str, Any]:
        """The part persisted with the run (never the one-shot destructive confirmation)."""
        return self.model_dump(exclude={"confirm_destructive"})


class JobContext:
    """What a job body gets from the JobManager: progress, cancellation and a hook that runs after finish."""

    def __init__(
        self,
        job_id: int,
        video_id: str,
        video_name: str,
        progress: Callable[[float, str], None],
        check_cancel: Callable[[], None],
    ):
        self.job_id = job_id
        self.video_id = video_id
        self.video_name = video_name
        self.progress = progress
        self.check_cancel = check_cancel
        self.after_finish: list[Callable[[], None]] = []

    def then(self, fn: Callable[[], None]) -> None:
        """Run `fn` once the job row is marked finished (so it may submit the next job for this video)."""
        self.after_finish.append(fn)


def suggest_priority(presence: float | None, has_target: bool) -> str:
    if not has_target or presence is None:
        return "normal"
    if presence >= HIGH_PRESENCE:
        return "high"
    if presence >= LOW_PRESENCE:
        return "normal"
    return "low"


class Autopilot:
    def __init__(self, db: DB, settings: Settings | None = None, jobs: "JobManager | None" = None):
        self.db = db
        self.settings = settings or get_settings()
        self.jobs = jobs
        self.chunker = AutoChunker(db, self.settings)

    # ------------------------------------------------------------------ state
    def get_state(self, video_id: str) -> dict[str, Any]:
        row = self.db.one("SELECT autopilot FROM videos WHERE id=?", (video_id,))
        state = (row or {}).get("autopilot")
        if not isinstance(state, dict) or state.get("stage") not in STAGES:
            return {"stage": "idle", "message": "", "config": None, "plan": None, "report": None, "error": None, "job_id": None}
        return state

    def _set(self, video_id: str, **fields: Any) -> dict[str, Any]:
        import json

        state = {**self.get_state(video_id), **fields, "updated_at": time.time()}
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET autopilot=? WHERE id=?", (json.dumps(state), video_id))
        if self.jobs:
            self.jobs.broadcast_event("autopilot_updated", {
                "video_id": video_id,
                "stage": state["stage"],
                "message": state.get("message", ""),
            })
        return state

    def _restore(self, video_id: str, previous: dict[str, Any]) -> None:
        import json

        with self.db.write() as conn:
            conn.execute("UPDATE videos SET autopilot=? WHERE id=?", (json.dumps(previous), video_id))

    def heal_stale(self) -> int:
        """After a restart a run whose job died must not claim to be running."""
        active = {
            r["video_id"]
            for r in self.db.all("SELECT DISTINCT video_id FROM jobs WHERE status IN ('queued','running') AND video_id IS NOT NULL")
        }
        healed = 0
        for row in self.db.all("SELECT id FROM videos WHERE autopilot IS NOT NULL"):
            vid = row["id"]
            if vid not in active and self.get_state(vid)["stage"] in ACTIVE_STAGES:
                self._set(vid, stage="error", error="Interrupted by a server restart", message="Interrupted by a server restart")
                healed += 1
        return healed

    # ------------------------------------------------------------------ commands
    def _preflight_destructive(self, video_id: str) -> list[dict[str, Any]]:
        affected = []
        for ch in self.db.all("SELECT id, position FROM chunks WHERE video_id=?", (video_id,)):
            counts = self.db.one(
                """
                SELECT
                    (SELECT COUNT(*) FROM picks WHERE chunk_id=? AND role='pick') AS picks,
                    (SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.chunk_id=?) AS exports
                """,
                (ch["id"], ch["id"]),
            )
            if counts["picks"] or counts["exports"]:
                affected.append({"id": ch["id"], "position": ch["position"], **counts})
        return affected

    def start(self, video_id: str, config: AutopilotConfig) -> dict[str, Any]:
        """Validate the configuration and queue the planning job."""
        assert self.jobs is not None, "Autopilot needs a JobManager to run jobs"
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            raise LookupError("Video not found")
        self.jobs._assert_no_active_job(video_id)
        if video.get("broken") or not video.get("duration"):
            raise ValueError("Video is broken or has no readable duration")

        duration = float(video["duration"])
        ranges = normalize_ranges([(r.start_s, r.end_s) for r in config.exclude_ranges], duration)
        if ranges and sum(b - a for a, b in ranges) >= duration - 1.0:
            raise ValueError("The excluded ranges cover the whole video")
        config = config.model_copy(update={
            "exclude_ranges": [TimeRange(start_s=a, end_s=b) for a, b in ranges],
        })

        if config.target_person_id is not None:
            person = self.db.get_person(config.target_person_id)
            if not person:
                raise ValueError(f"Person {config.target_person_id} not found")
            if person_embedding(person) is None:
                raise ValueError(f"Person '{person['name']}' has no enrolled face yet - enroll a face first")

        if not config.confirm_destructive:
            affected = self._preflight_destructive(video_id)
            if affected:
                raise DestructiveRechunkError(affected)

        previous = self.get_state(video_id)
        self.db.set_video_target_person(video_id, config.target_person_id)
        self._set(
            video_id, stage="planning", config=config.stored(), plan=None, report=None, error=None,
            job_id=None, started_at=time.time(), message="Planning scenes...",
        )
        try:
            job_id = self.jobs.submit_autopilot_job("autopilot_plan", video_id, self._run_plan)
        except Exception:
            self._restore(video_id, previous)
            raise
        return self._set(video_id, job_id=job_id)

    def approve(
        self,
        video_id: str,
        priorities: dict[int, str] | None = None,
        unattended: bool = False,
    ) -> dict[str, Any]:
        """The human accepted the scene priorities: extract, score and refine.

        `unattended` is the no-review path, where the planning job itself triggers this from stage 'planning'.
        """
        assert self.jobs is not None
        state = self.get_state(video_id)
        allowed = ("awaiting_review", "error", "cancelled", "ready") + (("planning",) if unattended else ())
        if state["stage"] not in allowed:
            raise ValueError(f"Nothing to approve (autopilot is '{state['stage']}')")
        if not self.db.all("SELECT id FROM chunks WHERE video_id=?", (video_id,)):
            raise ValueError("Video has no scenes to extract")

        if priorities:
            self.chunker.apply_priorities(video_id, priorities)
        if all((c.get("priority") or "normal") == "skip" for c in self.db.all("SELECT priority FROM chunks WHERE video_id=?", (video_id,))):
            raise ValueError("At least one scene must not be skipped")

        cfg = AutopilotConfig(**(state.get("config") or {}))
        options: dict[str, Any] = {}
        if cfg.target_frames:
            options = {"target_curated": cfg.target_frames}

        self._set(video_id, stage="extracting", error=None, report=None, message="Extracting and scoring candidates...")
        try:
            job_id = self.jobs.submit_extract_750_job(video_id, options=options)
        except Exception:
            self._restore(video_id, state)
            raise
        return self._set(video_id, job_id=job_id)

    def _approve_unattended(self, video_id: str) -> None:
        try:
            self.approve(video_id, unattended=True)
        except Exception as ex:
            self._set(video_id, stage="error", error=str(ex), message=f"Could not start extraction: {ex}")

    def refine(self, video_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Queue the similarity re-evaluation for a video that already has picks (also usable by hand)."""
        assert self.jobs is not None
        if not self.db.one("SELECT 1 AS x FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=? AND p.role='pick'", (video_id,)):
            raise ValueError("Video has no extracted picks to evaluate")
        previous = self.get_state(video_id)
        self._set(video_id, stage="refining", error=None, message="Re-evaluating similarity...",
                  refine_overrides={k: v for k, v in (overrides or {}).items() if v is not None})
        try:
            job_id = self.jobs.submit_autopilot_job("autopilot_refine", video_id, self._run_refine)
        except Exception:
            self._restore(video_id, previous)
            raise
        return self._set(video_id, job_id=job_id)

    def reset(self, video_id: str) -> None:
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET autopilot=NULL WHERE id=?", (video_id,))
        if self.jobs:
            self.jobs.broadcast_event("autopilot_updated", {"video_id": video_id, "stage": "idle", "message": ""})

    # ------------------------------------------------------------------ job bodies
    def _probe_scene(self, vpath: str, start_s: float, end_s: float, n: int, emb: np.ndarray, video: dict[str, Any]) -> tuple[float | None, float | None]:
        """(target person presence, any-face presence) from `n` frames spread over a scene."""
        margin = min(1.0, 0.1 * (end_s - start_s))
        stamps = list(np.linspace(start_s + margin, max(start_s + margin, end_s - margin), n))
        frames = sample_frames_at(vpath, stamps, max_dim=512)
        if not frames:
            return None, None
        assert self.jobs is not None
        matched = with_face = 0
        for _, img in frames:
            img = apply_watermark_crop(apply_letterbox_crop(img, video.get("letterbox")), video.get("watermark_rect"))
            res = self.jobs.face_engine.match_frame(img, emb)
            matched += 1 if res["matched"] else 0
            with_face += 1 if res["face_count"] > 0 else 0
        return round(matched / len(frames), 3), round(with_face / len(frames), 3)

    def _run_plan(self, ctx: JobContext) -> dict[str, Any]:
        video = self.db.one("SELECT * FROM videos WHERE id=?", (ctx.video_id,))
        if not video:
            raise ValueError("Video not found")
        state = self.get_state(ctx.video_id)
        cfg = AutopilotConfig(**(state.get("config") or {}))
        us = merged_user_settings(self.db.get_setting("user_settings", {}))
        auto = us.get("autopilot") or {}
        target_frames = cfg.target_frames or int(us.get("target_curated", 300))
        review = cfg.review_chunks if cfg.review_chunks is not None else bool(auto.get("review_chunks", True))
        ranges = [(r.start_s, r.end_s) for r in cfg.exclude_ranges]

        ctx.progress(0.05, "Detecting scenes around the excluded ranges...")
        scenes = self.chunker.plan_scenes(ctx.video_id, exclude_ranges=ranges)
        if not scenes:
            raise RuntimeError("No usable scenes left: everything is excluded, black or unreadable")
        ctx.check_cancel()

        person = self.db.get_person(cfg.target_person_id) if cfg.target_person_id else None
        emb = person_embedding(person)
        presences: list[float | None] = [None] * len(scenes)
        faces: list[float | None] = [None] * len(scenes)
        if emb is not None:
            n_probe = int(cfg.probe_frames or auto.get("probe_frames", 12))
            for i, (a, b) in enumerate(scenes):
                ctx.progress(0.10 + 0.80 * (i / len(scenes)), f"Looking for {person['name']} in scene {i + 1}/{len(scenes)}...")
                presences[i], faces[i] = self._probe_scene(video["path"], a, b, n_probe, emb, video)
                ctx.check_cancel()

        priorities = [suggest_priority(p, emb is not None) for p in presences]
        ctx.progress(0.92, "Allocating frame budgets...")
        rows = self.chunker.build_chunk_rows(ctx.video_id, scenes, target_frames, priorities, presences, faces)
        self.chunker.persist_chunks(ctx.video_id, rows, confirm_destructive=True)

        plan = {
            "scenes": len(scenes),
            "excluded_s": round(sum(b - a for a, b in normalize_ranges(ranges, float(video["duration"]))), 1),
            "target_person": person["name"] if emb is not None else None,
            "priorities": {p: priorities.count(p) for p in ("high", "normal", "low", "skip") if priorities.count(p)},
        }
        if review:
            self._set(ctx.video_id, stage="awaiting_review", plan=plan,
                      message=f"{len(scenes)} scenes planned - review the priorities, then approve")
        else:
            self._set(ctx.video_id, stage="planning", plan=plan, message=f"{len(scenes)} scenes planned - continuing unattended")
            ctx.then(lambda: self._approve_unattended(ctx.video_id))
        return plan

    def _run_refine(self, ctx: JobContext) -> dict[str, Any]:
        state = self.get_state(ctx.video_id)
        cfg = AutopilotConfig(**(state.get("config") or {}))
        overrides = {"dup_similarity": cfg.dup_similarity, "person_focus": cfg.person_focus}
        overrides.update(state.get("refine_overrides") or {})
        us = merged_user_settings(self.db.get_setting("user_settings", {}))
        report = Refiner(self.db, self.settings).run(
            ctx.video_id,
            RefineConfig.from_settings(us, **overrides),
            check_cancel=ctx.check_cancel,
            on_progress=ctx.progress,
        )
        after = report["after"]
        self._set(
            ctx.video_id, stage="ready", report=report, error=None, refine_overrides=None,
            message=f"Ready for captioning: {report['picks_after']} frames, {after['dup_pairs']} near-duplicate pairs left",
        )
        return {"picks": report["picks_after"], "swaps": len(report["swaps"])}

    # ------------------------------------------------------------------ job lifecycle hooks
    def on_extract_finished(self, video_id: str) -> None:
        """Called by the JobManager after an `extract_750` job committed; continues a run in 'extracting'."""
        if self.get_state(video_id)["stage"] != "extracting":
            return
        try:
            self._set(video_id, stage="refining", message="Re-evaluating similarity...")
            self.jobs.submit_autopilot_job("autopilot_refine", video_id, self._run_refine)  # type: ignore[union-attr]
        except Exception as ex:
            self._set(video_id, stage="error", error=str(ex), message=f"Could not start the similarity pass: {ex}")

    def on_job_failed(self, video_id: str, message: str) -> None:
        if self.get_state(video_id)["stage"] in ACTIVE_STAGES:
            self._set(video_id, stage="error", error=message, message=message)

    def on_job_cancelled(self, video_id: str) -> None:
        if self.get_state(video_id)["stage"] in ACTIVE_STAGES:
            self._set(video_id, stage="cancelled", message="Cancelled")
