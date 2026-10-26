"""Background extraction job worker and Server-Sent Events (SSE) dispatcher."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncGenerator, Callable

import cv2
import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.decode import decode_exact_frame, decode_exact_frames, ensure_native_frames, sample_chunk_frames
from pipeline.export import (
    append_manifest_record,
    apply_letterbox_crop,
    apply_watermark_crop,
    existing_caption_kind,
    link_or_copy,
    render_export_image,
    resolve_buckets,
    snap_to_multiple_of_16,
    write_caption_files,
)
from pipeline.metrics import (
    compute_dhash,
    compute_tier1_metrics,
    dhash_dedup_consecutive,
    filter_and_rank_tier1,
    hard_filter_check,
    phash_hamming_distance,
)
from pipeline.openrouter import (
    CASCADE_ERRORS,
    DEFAULT_CAPTION_MAX_TOKENS,
    DEFAULT_CAPTION_TEMPERATURE,
    DEFAULT_CAPTION_TOP_P,
    ModelRefusalError,
    OpenRouterCaptioner,
    empty_usage,
    looks_truncated,
    normalize_reasoning_effort,
)
from pipeline.captions import get_accepted_caption, import_caption, record_caption
from pipeline.prompts import normalize_mode as normalize_caption_mode, prompt_version as caption_prompt_version
from pipeline.autopilot import Autopilot, JobContext
from pipeline.face import FaceEngine, person_embedding
from pipeline.image_tags import ImageTagEngine
from pipeline.learned_weights import active_learned_weights
from pipeline.analysis import (
    AnalysisCache,
    crop_signature,
    face_summary,
    open_analysis_cache,
    write_frame_features,
)
from pipeline.embedders import Dinov2Embedder
from pipeline.quality import IqaScorer, gate_metrics, quality_gates, raw_components
from pipeline.scoring import ScoringEngine
from pipeline.select import select_diverse_top_k
from pipeline.tagger import LocalTagger


SSE_QUEUE_MAX = 500
SSE_HEARTBEAT_S = 15.0

# Settles a video after a job ends without completing (error or cancel); `?` = video id, then statuses.
# Nothing changed on cancel, so a fully captioned video goes back to 'exported'.
SETTLE_VIDEO_STATUS_SQL = """
    UPDATE videos SET status = CASE
        WHEN (SELECT COUNT(*) FROM picks p JOIN frames f ON p.frame_id=f.id
              WHERE f.video_id=videos.id AND p.state IN ('keep', 'star')) > 0
         AND (SELECT COUNT(*) FROM picks p JOIN frames f ON p.frame_id=f.id
              WHERE f.video_id=videos.id AND p.state IN ('keep', 'star')
                AND NOT EXISTS (SELECT 1 FROM exports e WHERE e.frame_id=f.id)) = 0 THEN 'exported'
        WHEN (SELECT COUNT(*) FROM picks p JOIN frames f ON p.frame_id=f.id
              WHERE f.video_id=videos.id AND p.role='pick') > 0 THEN 'curating_300'
        WHEN EXISTS (SELECT 1 FROM chunks c WHERE c.video_id=videos.id) THEN 'chunked'
        ELSE 'new'
    END
    WHERE id=? AND status IN ({placeholders})
"""


def user_settings_for_mode(db: DB) -> str:
    """The default caption mode from the user settings (`openrouter_mode`)."""
    return merged_user_settings(db.get_setting("user_settings", {})).get("openrouter_mode") or "concept"


class JobCancelled(Exception):
    """Raised at a job checkpoint after the user cancelled it; the job must not mutate the DB further."""


class JobConflictError(Exception):
    """Raised when a job is submitted for a video that already has an active queued or running job."""


class JobManager:
    def __init__(self, db: DB, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.classifier = ClipClassifier(self.settings)
        self.tagger = LocalTagger(self.classifier, self.settings)
        self.scoring_engine = ScoringEngine(self.classifier)
        self.captioner = OpenRouterCaptioner(self.settings)
        self.face_engine = FaceEngine(self.settings)
        self.image_tags = ImageTagEngine(self.db, self.settings, self.classifier)
        # Phase F2: optional DINOv2 selection embedder and IQA component (models load lazily, CLIP fallback)
        self.dino = Dinov2Embedder(self.settings)
        self.iqa = IqaScorer(self.settings)
        self._score_lock = threading.Lock()
        self.autopilot = Autopilot(self.db, self.settings, self)
        # Dedicated decode executor (1-2 workers) and caption executor (4 workers) (C3/H4)
        self.decode_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="decode_worker")
        self.caption_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="caption_worker")
        self.executor = self.decode_executor  # backwards-compatible alias
        self._listeners: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()
        self._cancel_flags: set[int] = set()

    def _assert_no_active_job(self, video_id: str) -> None:
        """Enforces per-video job lock: raises JobConflictError if an active job already exists (C3/H4)."""
        existing = self.db.one(
            "SELECT id, type, status FROM jobs WHERE video_id=? AND status IN ('queued', 'running')",
            (video_id,),
        )
        if existing:
            raise JobConflictError(
                f"Video {video_id} already has an active {existing['type']} job "
                f"(#{existing['id']}, status: {existing['status']})"
            )

    @staticmethod
    def _enqueue(q: asyncio.Queue, payload: str) -> None:
        """Runs on the listener's event loop. A slow client loses its oldest events, not the newest."""
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(payload)

    def broadcast_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Broadcast event to all SSE listeners. Safe to call from worker threads."""
        payload = json.dumps({"type": event_type, "data": data})
        with self._lock:
            listeners = list(self._listeners)
        for loop, q in listeners:
            try:
                # asyncio.Queue is not thread-safe; hand the put to the queue's own loop
                loop.call_soon_threadsafe(self._enqueue, q, payload)
            except RuntimeError:
                pass  # loop closed: the stream's finally removes it

    async def sse_stream(self) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE formatted strings, with a heartbeat comment when idle."""
        entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=SSE_QUEUE_MAX))
        q = entry[1]
        with self._lock:
            self._listeners.append(entry)
        try:
            # Send initial keepalive
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=SSE_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {msg}\n\n"
        finally:
            with self._lock:
                if entry in self._listeners:
                    self._listeners.remove(entry)

    def cancel_job(self, job_id: int) -> bool:
        """Request cancellation. Queued jobs end immediately; running jobs stop at their next checkpoint
        and report `job_cancelled` themselves. Finished jobs cannot be cancelled."""
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job or job["status"] not in ("queued", "running"):
            return False
        self._cancel_flags.add(job_id)
        if job["status"] == "queued":
            with self.db.write() as conn:
                cur = conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
                    (time.time(), job_id),
                )
                if cur.rowcount and job["video_id"]:
                    self._settle_video_status(conn, job["video_id"])
            if cur.rowcount:
                self.broadcast_event("job_cancelled", {"job_id": job_id, "video_id": job["video_id"]})
                return True
        self.broadcast_event("job_progress", {
            "job_id": job_id,
            "video_id": job["video_id"],
            "chunk_id": job["chunk_id"],
            "progress": job["progress"],
            "step": "Cancelling...",
        })
        return True

    def _check_cancel(self, job_id: int) -> None:
        if job_id in self._cancel_flags:
            raise JobCancelled()

    @staticmethod
    def _settle_video_status(conn: Any, video_id: str) -> None:
        """Move a video out of a transitional job status after a job ended without completing."""
        transitional = ("extracting", "extracting_750", "captioning")
        conn.execute(
            SETTLE_VIDEO_STATUS_SQL.format(placeholders=", ".join("?" * len(transitional))),
            (video_id, *transitional),
        )

    def _start_job(self, job_id: int, video_id: str | None, video_name: str, chunk_id: int | None = None) -> bool:
        """Mark a queued job running. False if it was cancelled while still queued."""
        with self.db.write() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='queued'",
                (time.time(), job_id),
            )
        if cur.rowcount == 0:
            self._cancel_flags.discard(job_id)
            return False
        self.broadcast_event("job_started", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": chunk_id,
        })
        return True

    def _finish_cancelled(self, job_id: int, video_id: str | None, video_name: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                (time.time(), job_id),
            )
            if video_id:
                self._settle_video_status(conn, video_id)
        self.broadcast_event("job_cancelled", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": None,
        })

    def submit_extract_job(self, video_id: str, chunk_id: int | None = None) -> int:
        now = time.time()
        vid = self.db.one("SELECT name FROM videos WHERE id=?", (video_id,))
        video_name = vid["name"] if vid else "Video"

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES (?, ?, ?, 'queued', 0.0, ?)
                """,
                ("extract_chunk" if chunk_id else "extract_video", video_id, chunk_id, now),
            )
            job_id = cursor.lastrowid
            conn.execute("UPDATE videos SET status='extracting' WHERE id=?", (video_id,))

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": chunk_id,
        })
        self.decode_executor.submit(self._run_job, job_id, video_id, chunk_id, video_name)
        return job_id

    def submit_auto_process_job(self, video_id: str, target_frames: int = 150) -> int:
        now = time.time()
        vid = self.db.one("SELECT name FROM videos WHERE id=?", (video_id,))
        video_name = vid["name"] if vid else "Video"

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES ('auto_process', ?, NULL, 'queued', 0.0, ?)
                """,
                (video_id, now),
            )
            job_id = cursor.lastrowid
            conn.execute("UPDATE videos SET status='extracting' WHERE id=?", (video_id,))

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": None,
        })
        self.decode_executor.submit(self._run_auto_process_job, job_id, video_id, video_name, target_frames)
        return job_id

    def submit_auto_extract_job(self, video_id: str, target_person_id: int | None = None) -> int:
        """Autonomously extracts candidates for a video with optional target person prioritization:
        1. Sets target_person_id on video if provided.
        2. Auto-chunks video if not already chunked.
        3. Submits extract_750 job with YuNet/SFace face recognition & priority scoring.
        """
        if target_person_id is not None:
            self.db.set_video_target_person(video_id, target_person_id)

        chunks = self.db.all("SELECT id FROM chunks WHERE video_id=?", (video_id,))
        if not chunks:
            from pipeline.autochunk import AutoChunker
            chunker = AutoChunker(self.db, self.settings)
            chunker.generate_balanced_chunks(video_id, target_frames=300)

        return self.submit_extract_750_job(video_id)

    def submit_autopilot_job(self, job_type: str, video_id: str, runner: Callable[[JobContext], dict[str, Any]]) -> int:
        """Queue a short job (planning, similarity pass) whose body `runner(ctx)` lives in the autopilot.

        The video keeps its status; the autopilot state tracks the stage. `ctx.then(fn)` callbacks run after
        the job row is finished, so they may queue the next job for the same video.
        """
        return self.submit_video_task(job_type, video_id, runner, notify_autopilot=True)

    def submit_video_task(
        self,
        job_type: str,
        video_id: str,
        runner: Callable[[JobContext], dict[str, Any]],
        notify_autopilot: bool = False,
    ) -> int:
        """Queue a per-video job whose body is `runner(ctx)`; the video keeps its status. Holds the video lock."""
        now = time.time()
        vid = self.db.one("SELECT name FROM videos WHERE id=?", (video_id,))
        video_name = vid["name"] if vid else "Video"

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                "INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at) VALUES (?, ?, NULL, 'queued', 0.0, ?)",
                (job_type, video_id, now),
            )
            job_id = cursor.lastrowid

        self.broadcast_event("job_queued", {"job_id": job_id, "video_id": video_id, "video_name": video_name, "chunk_id": None})
        self.decode_executor.submit(self._run_video_task, job_id, video_id, video_name, runner, notify_autopilot)
        return job_id

    def _run_video_task(
        self,
        job_id: int,
        video_id: str,
        video_name: str,
        runner: Callable[[JobContext], dict[str, Any]],
        notify_autopilot: bool = False,
    ) -> None:
        on_cancelled = self.autopilot.on_job_cancelled if notify_autopilot else (lambda _vid: None)
        on_failed = self.autopilot.on_job_failed if notify_autopilot else (lambda _vid, _err: None)
        if not self._start_job(job_id, video_id, video_name):
            on_cancelled(video_id)
            return

        def progress(p: float, step: str) -> None:
            p = round(min(1.0, max(0.0, p)), 2)
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
            self.broadcast_event("job_progress", {
                "job_id": job_id, "video_id": video_id, "video_name": video_name,
                "chunk_id": None, "progress": p, "step": step,
            })

        ctx = JobContext(job_id, video_id, video_name, progress, lambda: self._check_cancel(job_id))
        try:
            result = runner(ctx)
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status='finished', error=NULL, progress=1.0, finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )
            self.broadcast_event("job_finished", {
                "job_id": job_id, "video_id": video_id, "video_name": video_name, "chunk_id": None, **(result or {}),
            })
        except JobCancelled:
            self._finish_cancelled(job_id, video_id, video_name)
            on_cancelled(video_id)
            return
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
            self.broadcast_event("job_error", {"job_id": job_id, "video_id": video_id, "video_name": video_name, "error": str(ex)})
            on_failed(video_id, str(ex))
            return
        finally:
            self._cancel_flags.discard(job_id)

        for callback in ctx.after_finish:
            try:
                callback()
            except Exception as ex:  # a failed follow-up must not turn the finished job into an error
                on_failed(video_id, str(ex))

    def _sample_candidates(
        self,
        vpath: Path,
        start_s: float,
        end_s: float,
        sample_fps: float,
        max_samples: int,
        user_settings: dict[str, Any],
        sampling_stats: dict[str, Any],
        analysis: AnalysisCache | None = None,
    ) -> tuple[list[tuple[float, np.ndarray]], dict[float, float]]:
        """Phase F sampling: window-peak decode (setting `window_peak_sampling`), then dHash pre-dedup of
        consecutive near-identical samples (`dhash_dedup_max_distance`), before any metrics/faces/CLIP.

        With an `analysis` cache (Phase F2, setting `reuse_analysis_cache`) only the parts of the range the cache
        does not cover are decoded; the rest is served from `<cache>/analysis/<video>.npz`.

        Accumulates `{raw_samples, dhash_dropped, window_peak, subsamples}` into `sampling_stats` and returns
        (samples, motion energy by t; empty without window-peak sampling).
        """
        window_peak = bool(user_settings.get("window_peak_sampling", True))
        oversample = int(user_settings.get("window_peak_oversample", 3)) if window_peak else 0
        decode_stats: dict[str, Any] = {}
        kwargs: dict[str, Any] = {"sample_fps": sample_fps, "max_samples": max_samples, "max_dim": 512}
        if window_peak:
            kwargs.update(window_peak=oversample, stats_out=decode_stats)
        if analysis is not None and analysis.enabled:
            kwargs["stats_out"] = decode_stats
            raw = analysis.sample(sample_chunk_frames, vpath, start_s, end_s, **kwargs)
            sampling_stats["analysis_cache"] = dict(analysis.stats)
        else:
            raw = sample_chunk_frames(vpath, start_s, end_s, **kwargs)
        max_dist = int(user_settings.get("dhash_dedup_max_distance", 4))
        kept, dropped = dhash_dedup_consecutive(list(raw), max_dist)
        sampling_stats["raw_samples"] = sampling_stats.get("raw_samples", 0) + len(raw)
        sampling_stats["dhash_dropped"] = sampling_stats.get("dhash_dropped", 0) + dropped
        sampling_stats["dhash_max_distance"] = max_dist
        sampling_stats["window_peak"] = window_peak
        sampling_stats["subsamples"] = sampling_stats.get("subsamples", 0) + int(decode_stats.get("subsamples", len(raw)))
        motion = decode_stats.get("motion") or {}
        return kept, (motion if isinstance(motion, dict) else {})

    def _configure_learned_scoring(self, user_settings: dict[str, Any]) -> bool:
        """Blend fitted weights into the composite when `use_learned_weights` is on (else the default path)."""
        try:
            weights = active_learned_weights(self.db, user_settings)
        except Exception:
            weights = None
        self.scoring_engine.configure_learned(weights, float(user_settings.get("learned_weights_blend", 0.5)))
        return weights is not None

    # ------------------------------------------------------------------ Phase F2: analysis cache / quality v2
    def _open_analysis(self, video: dict[str, Any], user_settings: dict[str, Any]) -> AnalysisCache | None:
        """The video's analysis cache (setting `reuse_analysis_cache`; None when off or the file is missing)."""
        if not user_settings.get("reuse_analysis_cache", True):
            return None
        try:
            cache = open_analysis_cache(self.db, self.settings.cache_dir, video["id"], video.get("path"))
        except Exception:
            return None
        return cache if cache.enabled else None

    @staticmethod
    def _native_scale(video: dict[str, Any], sample_max_dim: int = 512) -> float:
        native = max(int(video.get("width") or 0), int(video.get("height") or 0))
        return max(1.0, native / float(sample_max_dim)) if native else 1.0

    def _gate_samples(
        self,
        raw_samples: list[tuple[float, np.ndarray]],
        motion_by_t: dict[float, float],
        letterbox: Any,
        watermark_rect: Any,
        user_settings: dict[str, Any],
        gate_stats: dict[str, int],
        features: dict[float, dict[str, Any]],
    ) -> list[tuple[float, np.ndarray, dict[str, Any]]]:
        """Crop, tier-1 metrics and gates for one chunk's samples -> [(t, cropped image, metrics)] that pass.

        Quality v2 (`quality_v2`): `quality.quality_gates` (dark / blown / clipping / letterbox-black / blur
        relative to the chunk); otherwise the legacy absolute `hard_filter_check`. Every sample's signals and
        gate reasons go to `features` (-> frame_features).
        """
        v2 = bool(user_settings.get("quality_v2", True))
        prepared: list[tuple[float, np.ndarray, dict[str, Any]]] = []
        for t, img_bgr in raw_samples:
            t_val = round(float(t), 3)
            # Pre-crop letterbox and watermark before metrics & CLIP (C5/H8/H9)
            img_cropped = apply_letterbox_crop(img_bgr, letterbox)
            img_cropped = apply_watermark_crop(img_cropped, watermark_rect)
            metrics = compute_tier1_metrics(img_cropped)
            if t_val in motion_by_t:
                metrics["motion_energy"] = motion_by_t[t_val]
            if v2:
                metrics.update(gate_metrics(img_cropped))
            prepared.append((t_val, img_cropped, metrics))
        if v2:
            reasons = quality_gates([m for _, _, m in prepared])
        else:
            reasons = []
            for _, _, m in prepared:
                ok, why = hard_filter_check(m, sharpness_floor=15.0)
                reasons.append([] if ok else [why or "hard_filter"])
        passed: list[tuple[float, np.ndarray, dict[str, Any]]] = []
        for (t_val, img, m), why in zip(prepared, reasons):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            features[t_val] = {
                "t": t_val,
                "lap_var": round(float(cv2.Laplacian(gray, cv2.CV_32F).var()), 3),
                "motion": m.get("motion_energy"),
                "mean_luma": m.get("mean_luma"),
                "luma_std": m.get("contrast"),
                "clipping": m.get("clipping"),
                "dhash": compute_dhash(gray),
                "gates": list(why),
            }
            if why:
                for r in why:  # noqa: VEC001 aggregate filter rejection reasons
                    key = r if v2 else "hard_filter"
                    gate_stats[key] = gate_stats.get(key, 0) + 1
                continue
            passed.append((t_val, img, m))
        return passed

    def _attach_quality_raw(
        self,
        cands: list[dict[str, Any]],
        user_settings: dict[str, Any],
        native_scale: float,
        features: dict[float, dict[str, Any]],
    ) -> None:
        """Face summary for every candidate; quality-v2 raw components (`metrics.q_raw`) when v2 is on."""
        v2 = bool(user_settings.get("quality_v2", True))
        for c in cands:
            m = c["metrics"]
            feat = features.setdefault(c["t"], {"t": c["t"]})
            feat["face_count"] = int(m.get("face_count") or 0)
            feat["face_summary"] = face_summary(m.get("faces"), c["img_bgr"].shape, native_scale)
            if v2:
                m["q_raw"] = raw_components(c["img_bgr"], m, m.get("faces"), native_scale)

    def _clip_model_id(self) -> str:
        repo = getattr(self.classifier, "repo", None)
        return repo if isinstance(repo, str) else "clip"

    def _clip_embeddings(
        self,
        cands: list[dict[str, Any]],
        analysis: AnalysisCache | None,
        crop: str,
        stats: dict[str, Any],
    ) -> np.ndarray:
        """CLIP embeddings, reusing the analysis cache for timestamps embedded before with the same crop."""
        imgs = [c["img_bgr"] for c in cands]
        ts = [c["t"] for c in cands]
        model = self._clip_model_id()
        cached = analysis.get_embeddings("clip", model, crop, ts) if analysis is not None else {}
        missing = [i for i in range(len(cands)) if i not in cached]
        emb: np.ndarray | None = None
        if cached:
            try:
                rows: list[Any] = [None] * len(cands)
                for i, v in cached.items():
                    rows[i] = v
                if missing:
                    fresh = self.classifier.embed_images([imgs[i] for i in missing], batch_size=32)
                    for i, v in zip(missing, fresh):
                        rows[i] = v
                emb = np.stack([np.asarray(r, dtype=np.float32) for r in rows])
            except Exception:
                emb = None
        if emb is None:
            emb = self.classifier.embed_images(imgs, batch_size=32)
            missing = list(range(len(cands)))
            cached = {}
        if analysis is not None and missing:
            try:
                analysis.put_embeddings("clip", model, crop, [ts[i] for i in missing], np.asarray(emb)[missing])
            except Exception:
                pass
        stats["clip_embeddings"] = {"computed": len(missing), "cached": len(cached)}
        return emb

    def _attach_iqa(
        self, cands: list[dict[str, Any]], embeddings: np.ndarray, user_settings: dict[str, Any], stats: dict[str, Any]
    ) -> None:
        """Optional IQA component (`iqa_model`, quality v2 only) into `metrics.q_raw.iqa`."""
        mode = str(user_settings.get("iqa_model", "off"))
        if mode == "off" or not user_settings.get("quality_v2", True) or not cands:
            return
        try:
            scores = self.iqa.score(mode, [c["img_bgr"] for c in cands], embeddings, classifier=self.classifier)
        except Exception as exc:
            scores, self.iqa.error = None, str(exc)
        if scores is None:
            stats["iqa"] = {"model": mode, "active": False, "error": self.iqa.error}
            return
        for c, s in zip(cands, scores):
            c["metrics"].setdefault("q_raw", {})["iqa"] = round(float(s), 4)
        stats["iqa"] = {"model": mode, "active": True}

    def _quality_reference(self, video_id: str, exclude: tuple[float, float]) -> dict[str, list[float]]:
        """Raw v2 components of the video's frames outside `exclude` (video-level percentiles for one chunk)."""
        ref: dict[str, list[float]] = {}
        for r in self.db.all(
            "SELECT t, q_components FROM frame_features WHERE video_id=? AND q_components IS NOT NULL", (video_id,)
        ):
            if exclude[0] <= float(r["t"]) <= exclude[1]:
                continue
            raw = (r.get("q_components") or {}).get("raw") or {}
            for k, v in raw.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    ref.setdefault(k, []).append(float(v))
        return ref

    def _score_batch(
        self,
        cands: list[dict[str, Any]],
        embeddings: np.ndarray,
        target_person_emb: Any,
        user_settings: dict[str, Any],
        reference: dict[str, list[float]] | None = None,
    ) -> bool:
        """Configure learned weights + quality v2 and score, under one lock (the engine is shared by workers)."""
        with self._score_lock:
            learned_active = self._configure_learned_scoring(user_settings)
            self.scoring_engine.configure_quality(bool(user_settings.get("quality_v2", True)), reference)
            self.scoring_engine.score_candidates_batch(cands, embeddings, target_person_embedding=target_person_emb)
        return learned_active

    def _selection_embeddings(
        self,
        items: list[dict[str, Any]],
        analysis: AnalysisCache | None,
        crop: str,
        user_settings: dict[str, Any],
        features: dict[float, dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        """DINOv2 embeddings of the stored frames into the analysis cache (setting `selection_embedder`)."""
        if user_settings.get("selection_embedder", "clip") != "dinov2":
            return
        if analysis is None:
            stats["selection_embedder"] = {"embedder": "clip", "fallback": "analysis cache disabled"}
            return
        ts = [round(float(it["t"]), 3) for it in items]
        try:
            cached = analysis.get_embeddings("dinov2", self.dino.model_id, crop, ts)
            missing = [i for i in range(len(items)) if i not in cached]
            if missing:
                if not self.dino.available():
                    stats["selection_embedder"] = {"embedder": "clip", "fallback": self.dino.error}
                    return
                vecs = self.dino.embed_images([items[i]["img_bgr"] for i in missing])
                analysis.put_embeddings("dinov2", self.dino.model_id, crop, [ts[i] for i in missing], vecs)
        except Exception as exc:
            stats["selection_embedder"] = {"embedder": "clip", "fallback": str(exc)}
            return
        for t in ts:
            refs = features.setdefault(t, {"t": t}).setdefault("emb_refs", {})
            refs["dinov2"] = f"analysis/{analysis.video_id}.npz#emb_dinov2"
        stats["selection_embedder"] = {"embedder": "dinov2", "computed": len(missing), "cached": len(cached)}

    def selection_embed_rows(self, video_id: str, user_settings: dict[str, Any]) -> Callable[[list[dict]], Any] | None:
        """For reselect: a callable mapping frame rows to DINOv2 vectors (analysis cache first, then the stored
        previews), or None when `selection_embedder` is 'clip'."""
        if user_settings.get("selection_embedder", "clip") != "dinov2":
            return None
        from pipeline.embedders import selection_embeddings_for_rows

        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,)) or {"id": video_id}

        def embed(rows: list[dict]) -> Any:
            cache = self._open_analysis(video, {"reuse_analysis_cache": True})
            crop = crop_signature(video.get("letterbox"), video.get("watermark_rect"))
            return selection_embeddings_for_rows(rows, embedder=self.dino, cache=cache, crop=crop)

        return embed

    def _persist_analysis(
        self,
        video_id: str,
        analysis: AnalysisCache | None,
        features: dict[float, dict[str, Any]],
        cands: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> None:
        """Save the npz and upsert frame_features (never fails the job)."""
        try:
            for c in cands:
                feat = features.setdefault(c["t"], {"t": c["t"]})
                if analysis is not None:
                    feat.setdefault("emb_refs", {})["clip"] = f"analysis/{video_id}.npz#emb_clip"
                q_raw = (c.get("metrics") or {}).get("q_raw")
                if q_raw is not None:
                    feat["q_components"] = {"raw": q_raw, "norm": (c.get("scores") or {}).get("q_components")}
            if analysis is not None:
                analysis.save()
            stats["frame_features"] = write_frame_features(self.db, video_id, list(features.values()))
        except Exception as exc:  # pragma: no cover - defensive
            stats["analysis_error"] = str(exc)

    def _maybe_auto_suggest_tags(self, video_id: str) -> None:
        """After an extraction: pre-tag the new frames when learned tags exist (setting `auto_suggest_tags`).

        Skipped when another job (an autopilot follow-up) already holds the video; the user can run it later.
        """
        stored = merged_user_settings(self.db.get_setting("user_settings", {}))
        if not stored.get("auto_suggest_tags", True):
            return
        if not self.db.one("SELECT 1 FROM tag_regions WHERE polarity='positive' LIMIT 1"):
            return
        try:
            self.submit_tag_suggest_job(video_id)
        except JobConflictError:
            pass

    def submit_tag_suggest_job(self, video_id: str) -> int:
        """Embed every frame of the video as region windows and store tag suggestions (job `tag_suggest`)."""
        def runner(ctx: JobContext) -> dict[str, Any]:
            res = self.image_tags.suggest_for_video(video_id, on_progress=ctx.progress, check_cancel=ctx.check_cancel)
            self.broadcast_event("tags_updated", {"video_id": video_id, **res})
            return res

        return self.submit_video_task("tag_suggest", video_id, runner)

    def submit_extract_750_job(self, video_id: str, options: dict[str, Any] | None = None) -> int:
        now = time.time()
        vid = self.db.one("SELECT name FROM videos WHERE id=?", (video_id,))
        video_name = vid["name"] if vid else "Video"

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES ('extract_750', ?, NULL, 'queued', 0.0, ?)
                """,
                (video_id, now),
            )
            job_id = cursor.lastrowid
            conn.execute("UPDATE videos SET status='extracting_750' WHERE id=?", (video_id,))

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": None,
        })
        self.decode_executor.submit(self._run_extract_750_job, job_id, video_id, video_name, options or {})
        return job_id

    def _execute_extract_750(
        self,
        video_id: str,
        options: dict[str, Any] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            raise ValueError(f"Video {video_id} not found")

        vpath = Path(video["path"])
        chunks = self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))
        if not chunks:
            from pipeline.autochunk import AutoChunker
            chunker = AutoChunker(self.db, self.settings)
            chunks = chunker.generate_balanced_chunks(video_id, target_frames=300)

        # Scenes the human skipped during review are not sampled at all
        chunks = [c for c in chunks if (c.get("priority") or "normal") != "skip"]
        if not chunks:
            raise RuntimeError("Every scene is skipped - nothing to extract")

        total_chunks = len(chunks)
        user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))
        options = options or {}
        target_total = int(user_settings.get("target_candidates", 750))
        target_curated = int(options.get("target_curated") or user_settings.get("target_curated", 300))
        if options.get("target_curated"):
            target_total = max(target_total, round(target_curated * 2.5))
        category_quotas = user_settings.get("category_quotas")
        mmr_lambda = float(user_settings.get("mmr_lambda", 0.65))

        letterbox = video.get("letterbox")
        watermark_rect = video.get("watermark_rect")

        video_persons = self.db.get_video_persons(video_id, include_embeddings=True)
        pos_persons = video_persons.get("positive", [])
        neg_persons = video_persons.get("negative", [])

        # Primary positive target person for legacy single target display/compatibility
        if not pos_persons and video.get("target_person_id"):
            legacy_p = self.db.get_person(video["target_person_id"])
            if legacy_p:
                pos_persons = [legacy_p]

        pos_embeddings = [person_embedding(p) for p in pos_persons]
        pos_embeddings = [p for p in pos_embeddings if p is not None]
        neg_embeddings = [person_embedding(p) for p in neg_persons]
        neg_embeddings = [n for n in neg_embeddings if n is not None]

        target_person = pos_persons[0] if pos_persons else None
        target_person_emb = pos_embeddings[0] if pos_embeddings else None

        if pos_persons and neg_persons:
            sample_desc = f"Sampling frames with {len(pos_persons)} positive & {len(neg_persons)} negative face filters..."
        elif pos_persons:
            sample_desc = f"Sampling candidate frames with target person '{pos_persons[0]['name']}' face recognition..."
        elif neg_persons:
            sample_desc = f"Sampling frames excluding {len(neg_persons)} negative person face(s)..."
        else:
            sample_desc = f"Sampling candidate frames across {total_chunks} chunks..."
        if on_progress:
            on_progress(0.05, sample_desc)

        # 1. Sample candidate frames per chunk (streamed at 512px with pre-crop & gates)
        all_candidates: list[dict[str, Any]] = []
        seen_cand_ts: set[float] = set()
        sampling_stats: dict[str, Any] = {}
        # Phase F2: analysis cache (decode once, reuse), gates and per-timestamp features
        analysis = self._open_analysis(video, user_settings)
        crop_sig = crop_signature(letterbox, watermark_rect)
        gate_stats: dict[str, int] = {}
        features: dict[float, dict[str, Any]] = {}
        native_scale = self._native_scale(video)

        for idx, ch in enumerate(chunks):
            if check_cancel:
                check_cancel()

            bpct = float(ch.get("budget_pct", 100.0 / max(1, total_chunks)))
            chunk_n = max(15, round(target_total * (bpct / 100.0)))
            dur = max(1.0, float(ch["end_s"]) - float(ch["start_s"]))
            needed_fps = max(1.0, (chunk_n * 1.5) / dur)

            # Streamed at 512px to keep RAM minimal (C4/H5); window-peak + dHash pre-dedup (Phase F)
            raw_samples, motion_by_t = self._sample_candidates(
                vpath,
                float(ch["start_s"]),
                float(ch["end_s"]),
                needed_fps,
                chunk_n * 2,
                user_settings,
                sampling_stats,
                analysis,
            )
            raw_samples = [(t, img) for t, img in raw_samples if round(float(t), 3) not in seen_cand_ts]

            chunk_cands: list[dict[str, Any]] = []
            # Crop + metrics + hard gates (quality v2: chunk-relative) before faces/CLIP (C5/H8, Phase F2)
            for t_val, img_cropped, metrics in self._gate_samples(
                raw_samples, motion_by_t, letterbox, watermark_rect, user_settings, gate_stats, features
            ):
                if t_val in seen_cand_ts:
                    continue

                # Intra-chunk pHash deduplication <= 4 (C5/H8)
                if any(phash_hamming_distance(metrics["phash"], kept["metrics"]["phash"]) <= 4 for kept in chunk_cands):
                    continue

                # Face detection & target person matching via YuNet + SFace
                if pos_embeddings or neg_embeddings:
                    match_res = self.face_engine.match_frame_multi(
                        img_cropped,
                        positive_features=pos_embeddings,
                        negative_features=neg_embeddings,
                    )
                    # Rule: Exclude frame if exclusively the negative person is visible!
                    if match_res["excluded"]:
                        continue

                    metrics["target_person_sim"] = match_res["max_pos_sim"]
                    metrics["target_person_matched"] = match_res["pos_matched"]
                    metrics["negative_person_matched"] = match_res["neg_matched"]
                    metrics["face_count"] = match_res["face_count"]
                    metrics["faces"] = match_res["faces"]
                else:
                    detected_faces = self.face_engine.detect_faces(img_cropped)
                    metrics["target_person_sim"] = 0.0
                    metrics["target_person_matched"] = False
                    metrics["negative_person_matched"] = False
                    metrics["face_count"] = len(detected_faces)
                    metrics["faces"] = [
                        {"box": f["box"], "score": f["score"], "landmarks": f.get("landmarks")} for f in detected_faces
                    ]

                cand_dict = {
                    "chunk_id": ch["id"],
                    "t": t_val,
                    "img_bgr": img_cropped,
                    "metrics": metrics,
                    "target_person_sim": metrics["target_person_sim"],
                    "target_person_matched": metrics["target_person_matched"],
                    "face_count": metrics["face_count"],
                }
                seen_cand_ts.add(t_val)
                chunk_cands.append(cand_dict)
                all_candidates.append(cand_dict)

            p = 0.05 + 0.35 * ((idx + 1) / total_chunks)
            if on_progress:
                on_progress(round(p, 2), f"Sampled Chunk {idx + 1}/{total_chunks} ({len(chunk_cands)} valid frames)")

        if not all_candidates:
            raise RuntimeError("No candidate frames could be sampled from video")

        # 2. Batch Embeddings & 7-Dimension Scoring (metrics are already computed during sampling)
        if check_cancel:
            check_cancel()
        if on_progress:
            on_progress(0.50, f"Computing CLIP embeddings on {len(all_candidates)} filtered frames...")
        f2_stats: dict[str, Any] = {}
        self._attach_quality_raw(all_candidates, user_settings, native_scale, features)
        images_to_embed = [c["img_bgr"] for c in all_candidates]
        embeddings = self._clip_embeddings(all_candidates, analysis, crop_sig, f2_stats)
        self._attach_iqa(all_candidates, embeddings, user_settings, f2_stats)

        if check_cancel:
            check_cancel()
        step_desc = (
            f"Evaluating scoring with target person '{target_person['name']}' priority..."
            if target_person else "Evaluating 7-dimension scoring criteria..."
        )
        if on_progress:
            on_progress(0.68, step_desc)
        learned_active = self._score_batch(all_candidates, embeddings, target_person_emb, user_settings)

        # 3. Multi-objective reduction
        if on_progress:
            on_progress(0.78, f"Optimizing best {target_curated} frame subset...")
        picks_300, runners_450, stats = self.scoring_engine.reduce_750_to_300(
            all_candidates, chunks, target_total=target_curated, category_quotas=category_quotas, mmr_lambda=mmr_lambda
        )

        # 4. Save previews and decode exact full-resolution frames for picks
        if on_progress:
            on_progress(0.82, "Saving thumbnails and native frames...")
        preview_dir = self.settings.cache_dir / "previews" / video_id
        preview_dir.mkdir(parents=True, exist_ok=True)
        cand_dir = self.settings.cache_dir / "candidates" / video_id
        cand_dir.mkdir(parents=True, exist_ok=True)

        # Prune stale candidate and preview files for this video (M9)
        for old_f in list(preview_dir.glob("*.webp")) + list(cand_dir.glob("*.png")):
            try:
                old_f.unlink()
            except OSError:
                pass

        all_to_store = picks_300 + runners_450

        picks_by_chunk: dict[int, list[dict[str, Any]]] = {}
        for p in picks_300:
            cid = p["chunk_id"]
            picks_by_chunk.setdefault(cid, []).append(p)

        # Stream native frames directly to disk chunk by chunk without accumulating in RAM (C4/H5)
        for cid, ch_picks in picks_by_chunk.items():
            if check_cancel:
                check_cancel()
            ts = [p["t"] for p in ch_picks]
            def write_pick(target_t: float, native_img: np.ndarray):
                pick_path = cand_dir / f"full_c{cid}_{int(target_t * 1000)}.png"
                cv2.imwrite(str(pick_path), native_img)
            decode_exact_frames(vpath, ts, on_frame=write_pick)

        # Phase F2: DINOv2 selection embeddings of the stored frames (setting selection_embedder, cached in the npz)
        self._selection_embeddings(all_to_store, analysis, crop_sig, user_settings, features, f2_stats)

        # Last cancel point: commit replaces the current frames, picks and exports.
        if check_cancel:
            check_cancel()
        saved_records = []
        seen_store_ts: set[float] = set()
        for item in all_to_store:
            t = round(float(item["t"]), 3)
            if t in seen_store_ts:
                continue
            seen_store_ts.add(t)
            cid = item["chunk_id"]
            is_pick = item.get("role") == "pick"

            prev_path = preview_dir / f"prev_c{cid}_{int(t * 1000)}.webp"
            h, w = item["img_bgr"].shape[:2]
            pw = 384
            ph = max(16, int(h * (pw / w)))
            small = cv2.resize(item["img_bgr"], (pw, ph), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(prev_path), small, [cv2.IMWRITE_WEBP_QUALITY, 85])

            full_path = cand_dir / f"full_c{cid}_{int(t * 1000)}.png"
            if not is_pick:
                # Runners are saved at 512px; re-decoded to native resolution on demand (C1/H1)
                cv2.imwrite(str(full_path), item["img_bgr"])

            saved_records.append({
                "item": item,
                "t": t,
                "prev_path": str(prev_path),
                "full_path": str(full_path),
                "is_pick": is_pick,
            })

        # 5. Database Commit
        if on_progress:
            on_progress(0.92, "Committing records to database...")
        with self.db.write() as conn:
            conn.execute("DELETE FROM frames WHERE video_id=?", (video_id,))
            for rec in saved_records:
                item = rec["item"]
                is_pick = rec["is_pick"]
                cursor = conn.execute(
                    """
                    INSERT OR REPLACE INTO frames (
                        video_id, chunk_id, t, metrics, phash, embedding,
                        zs_scores, preview_path, full_path, native
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        item["chunk_id"],
                        rec["t"],
                        json.dumps(item["metrics"]),
                        item["metrics"]["phash"],
                        item["embedding"].tobytes(),
                        json.dumps(item["scores"]),
                        rec["prev_path"],
                        rec["full_path"],
                        1 if is_pick else 0,
                    ),
                )
                frame_id = cursor.lastrowid
                conn.execute(
                    """
                    INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (frame_id, item["chunk_id"], item.get("role", "pick"), item.get("rank", 0), item.get("state", "keep")),
                )
        self._persist_analysis(video_id, analysis, features, all_candidates, f2_stats)

        # 6. Local postprocessing: Deduplicate >= 95% similar images & trim to target budget (150-200)
        dedup_report = {}
        is_autopilot = self.autopilot.get_state(video_id)["stage"] == "extracting"
        if not is_autopilot:
            if on_progress:
                on_progress(0.96, "Running local similarity deduplication (≥95%) & score-optimal selection...")
            sim_threshold = float(user_settings.get("similarity_dedup_threshold", 0.95))
            target_min = int(user_settings.get("target_curated_min", 150))
            target_max = int(user_settings.get("target_curated_max", 200))
            if options.get("target_curated"):
                t_cur = int(options["target_curated"])
                target_min = min(target_min, t_cur)
                target_max = min(target_max, max(t_cur, target_min))
            try:
                from pipeline.refine import Refiner
                refiner = Refiner(self.db, self.settings)
                dedup_report = refiner.deduplicate_and_trim_similarity(
                    video_id,
                    similarity_threshold=sim_threshold,
                    target_min=target_min,
                    target_max=target_max,
                    check_cancel=check_cancel,
                )
            except Exception as e:
                dedup_report = {"error": str(e)}

        with self.db.write() as conn:
            conn.execute("UPDATE videos SET status='curating_300' WHERE id=?", (video_id,))

        final_picks_count = (
            dedup_report.get("after_count", len(picks_300))
            if isinstance(dedup_report, dict) and "after_count" in dedup_report
            else len(picks_300)
        )
        stats["dedup_similarity"] = dedup_report
        stats["sampling"] = sampling_stats
        stats["dhash_dropped"] = int(sampling_stats.get("dhash_dropped", 0))
        stats["learned_weights_active"] = learned_active
        stats["quality_v2"] = bool(user_settings.get("quality_v2", True))
        stats["quality_gates"] = gate_stats
        stats.update(f2_stats)

        runners_count = len(runners_450)
        del all_candidates, images_to_embed, embeddings, picks_300, runners_450, saved_records
        import gc
        gc.collect()

        return {
            "picks_count": final_picks_count,
            "runners_count": runners_count,
            "stats": stats,
            "dedup_report": dedup_report,
        }

    def _run_extract_750_job(self, job_id: int, video_id: str, video_name: str, options: dict[str, Any] | None = None) -> None:
        if not self._start_job(job_id, video_id, video_name):
            return

        def update_job_progress(p: float, step: str):
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
            self.broadcast_event("job_progress", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "progress": p,
                "step": step,
            })

        try:
            res = self._execute_extract_750(
                video_id,
                options=options,
                on_progress=update_job_progress,
                check_cancel=lambda: self._check_cancel(job_id),
            )
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status='finished', error=NULL, progress=1.0, finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )

            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "picks_count": res.get("picks_count", 0),
                "runners_count": res.get("runners_count", 0),
                "stats": res.get("stats", {}),
            })

            # Character profile: identity gate + solo crops on the fresh picks (no-op for 'concept')
            try:
                from pipeline.profiles import apply_character_profile

                apply_character_profile(self.db, video_id)
            except Exception:
                pass  # the gate is a refinement; a failure must not turn a finished extraction into an error
            # An autopilot run continues with the similarity pass (no-op for manual extractions)
            self.autopilot.on_extract_finished(video_id)
            self._maybe_auto_suggest_tags(video_id)

        except JobCancelled:
            self._finish_cancelled(job_id, video_id, video_name)
            self.autopilot.on_job_cancelled(video_id)
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
                # Never leave video stuck in 'extracting_750'
                self._settle_video_status(conn, video_id)
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "error": str(ex),
            })
            self.autopilot.on_job_failed(video_id, str(ex))
        finally:
            self._cancel_flags.discard(job_id)

    # -------------------------------------------------------------------------
    # Batch Auto-Extract Pipeline (Sequential step-by-step for overnight runs)
    # -------------------------------------------------------------------------
    def submit_batch_auto_extract_job(
        self,
        video_ids: list[str] | None = None,
        only_unextracted: bool = True,
        options: dict[str, Any] | None = None,
    ) -> int:
        now = time.time()
        if not video_ids:
            query = "SELECT id, name, status FROM videos WHERE broken=0 AND duration > 0"
            if only_unextracted:
                query += " AND status IN ('new', 'chunked')"
            query += " ORDER BY CASE status WHEN 'new' THEN 1 WHEN 'chunked' THEN 2 ELSE 3 END, name ASC"
            rows = self.db.all(query)
            target_ids = [r["id"] for r in rows]
        else:
            target_ids = list(video_ids)

        if not target_ids:
            raise ValueError("No eligible videos found to auto-extract")

        title = f"Batch Auto-Extract ({len(target_ids)} videos)"
        with self.db.write() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES ('batch_auto_extract', NULL, NULL, 'queued', 0.0, ?)
                """,
                (now,),
            )
            job_id = cursor.lastrowid

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": None,
            "video_name": title,
            "chunk_id": None,
        })
        self.decode_executor.submit(self._run_batch_auto_extract_job, job_id, target_ids, options or {})
        return job_id

    def _run_batch_auto_extract_job(self, job_id: int, video_ids: list[str], options: dict[str, Any]) -> None:
        title = f"Batch Auto-Extract ({len(video_ids)} videos)"
        if not self._start_job(job_id, None, title):
            return

        def update_batch_progress(p: float, step: str):
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
            self.broadcast_event("job_progress", {
                "job_id": job_id,
                "video_id": None,
                "video_name": title,
                "chunk_id": None,
                "progress": p,
                "step": step,
            })

        total = len(video_ids)
        processed = 0
        failed = 0
        failures: list[str] = []

        try:
            for idx, vid in enumerate(video_ids):
                self._check_cancel(job_id)
                v = self.db.one("SELECT id, name, status, broken, duration FROM videos WHERE id=?", (vid,))
                if not v or v.get("broken") or not v.get("duration"):
                    continue

                vname = v.get("name", vid)
                base_p = idx / total
                update_batch_progress(round(base_p, 3), f"[{idx+1}/{total}] '{vname}': Starting extraction...")

                try:
                    # 1. Ensure chunked
                    chunks = self.db.all("SELECT id FROM chunks WHERE video_id=?", (vid,))
                    if not chunks:
                        from pipeline.autochunk import AutoChunker
                        chunker = AutoChunker(self.db, self.settings)
                        chunker.generate_balanced_chunks(vid, target_frames=300)

                    # 2. Extract with scaled sub-progress
                    def on_sub_prog(sub_p: float, sub_step: str):
                        combined = base_p + (sub_p / total)
                        update_batch_progress(round(combined, 3), f"[{idx+1}/{total}] {vname}: {sub_step}")

                    with self.db.write() as conn:
                        conn.execute("UPDATE videos SET status='extracting_750' WHERE id=?", (vid,))

                    self._execute_extract_750(
                        vid,
                        options=options,
                        on_progress=on_sub_prog,
                        check_cancel=lambda: self._check_cancel(job_id),
                    )
                    processed += 1
                    self.broadcast_event("video_updated", {"video_id": vid, "status": "curating_300"})
                except JobCancelled:
                    raise
                except Exception as ex:
                    failed += 1
                    err_msg = f"{vname}: {type(ex).__name__}: {str(ex)}"
                    failures.append(err_msg)
                    with self.db.write() as conn:
                        self._settle_video_status(conn, vid)
                    continue

            with self.db.write() as conn:
                status_note = f"Finished {processed}/{total} videos ({failed} failed)" if failed else None
                conn.execute(
                    "UPDATE jobs SET status='finished', error=?, progress=1.0, finished_at=? WHERE id=?",
                    (status_note, time.time(), job_id),
                )

            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": None,
                "video_name": title,
                "chunk_id": None,
                "processed": processed,
                "failed": failed,
                "total": total,
            })
        except JobCancelled:
            self._finish_cancelled(job_id, None, title)
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": None,
                "video_name": title,
                "error": str(ex),
            })
        finally:
            self._cancel_flags.discard(job_id)

    # -------------------------------------------------------------------------
    # Targeted Single-Chunk Extraction (Multi-Step Frame Budget Re-Extraction)
    # -------------------------------------------------------------------------
    def submit_extract_chunk_job(self, chunk_id: int, options: dict[str, Any] | None = None) -> int:
        now = time.time()
        ch = self.db.one(
            "SELECT c.id, c.video_id, v.name as video_name FROM chunks c JOIN videos v ON c.video_id=v.id WHERE c.id=?",
            (chunk_id,),
        )
        if not ch:
            raise ValueError(f"Chunk {chunk_id} not found")
        video_id = ch["video_id"]
        video_name = ch["video_name"]

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES ('extract_chunk', ?, ?, 'queued', 0.0, ?)
                """,
                (video_id, chunk_id, now),
            )
            job_id = cursor.lastrowid
            conn.execute("UPDATE chunks SET dirty=1 WHERE id=?", (chunk_id,))

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": chunk_id,
        })
        self.decode_executor.submit(self._run_extract_chunk_job, job_id, video_id, video_name, chunk_id, options or {})
        return job_id

    def _execute_extract_chunk(
        self,
        chunk_id: int,
        k: int | None = None,
        budget_pct: float | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        chunk = self.db.one("SELECT * FROM chunks WHERE id=?", (chunk_id,))
        if not chunk:
            raise ValueError(f"Chunk {chunk_id} not found")
        video_id = chunk["video_id"]
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            raise ValueError(f"Video {video_id} not found")
        vpath = Path(video["path"])

        with self.db.write() as conn:
            if k is not None:
                conn.execute("UPDATE chunks SET k=? WHERE id=?", (int(k), chunk_id))
            if budget_pct is not None:
                conn.execute("UPDATE chunks SET budget_pct=? WHERE id=?", (float(budget_pct), chunk_id))

        target_k = int(k if k is not None else chunk.get("k", 15))
        target_k = max(5, target_k)
        target_samples = max(25, target_k * 3)

        dur = max(1.0, float(chunk["end_s"]) - float(chunk["start_s"]))
        needed_fps = max(1.0, (target_samples * 1.5) / dur)

        letterbox = video.get("letterbox")
        watermark_rect = video.get("watermark_rect")

        video_persons = self.db.get_video_persons(video_id, include_embeddings=True)
        pos_persons = video_persons.get("positive", [])
        neg_persons = video_persons.get("negative", [])

        if not pos_persons and video.get("target_person_id"):
            legacy_p = self.db.get_person(video["target_person_id"])
            if legacy_p:
                pos_persons = [legacy_p]

        pos_embeddings = [person_embedding(p) for p in pos_persons]
        pos_embeddings = [p for p in pos_embeddings if p is not None]
        neg_embeddings = [person_embedding(p) for p in neg_persons]
        neg_embeddings = [n for n in neg_embeddings if n is not None]

        target_person_emb = pos_embeddings[0] if pos_embeddings else None

        if on_progress:
            on_progress(0.10, f"Sampling candidate frames for Scene #{chunk.get('position', 0) + 1}...")
        if check_cancel:
            check_cancel()

        user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))
        sampling_stats: dict[str, Any] = {}
        # Phase F2: analysis cache, gates, per-timestamp features
        analysis = self._open_analysis(video, user_settings)
        crop_sig = crop_signature(letterbox, watermark_rect)
        gate_stats: dict[str, int] = {}
        features: dict[float, dict[str, Any]] = {}
        native_scale = self._native_scale(video)
        f2_stats: dict[str, Any] = {}
        raw_samples, motion_by_t = self._sample_candidates(
            vpath,
            float(chunk["start_s"]),
            float(chunk["end_s"]),
            needed_fps,
            target_samples * 2,
            user_settings,
            sampling_stats,
            analysis,
        )

        chunk_cands: list[dict[str, Any]] = []
        seen_cand_ts: set[float] = set()

        for t_val, img_cropped, metrics in self._gate_samples(
            raw_samples, motion_by_t, letterbox, watermark_rect, user_settings, gate_stats, features
        ):
            if t_val in seen_cand_ts:
                continue

            if any(phash_hamming_distance(metrics["phash"], kept["metrics"]["phash"]) <= 4 for kept in chunk_cands):
                continue

            if pos_embeddings or neg_embeddings:
                match_res = self.face_engine.match_frame_multi(
                    img_cropped,
                    positive_features=pos_embeddings,
                    negative_features=neg_embeddings,
                )
                if match_res["excluded"]:
                    continue
                metrics["target_person_sim"] = match_res["max_pos_sim"]
                metrics["target_person_matched"] = match_res["pos_matched"]
                metrics["negative_person_matched"] = match_res["neg_matched"]
                metrics["face_count"] = match_res["face_count"]
                metrics["faces"] = match_res["faces"]
            else:
                detected_faces = self.face_engine.detect_faces(img_cropped)
                metrics["target_person_sim"] = 0.0
                metrics["target_person_matched"] = False
                metrics["negative_person_matched"] = False
                metrics["face_count"] = len(detected_faces)
                metrics["faces"] = [
                    {"box": f["box"], "score": f["score"], "landmarks": f.get("landmarks")} for f in detected_faces
                ]

            cand_dict = {
                "chunk_id": chunk_id,
                "t": t_val,
                "img_bgr": img_cropped,
                "metrics": metrics,
                "target_person_sim": metrics["target_person_sim"],
                "target_person_matched": metrics["target_person_matched"],
                "face_count": metrics["face_count"],
            }
            seen_cand_ts.add(t_val)
            chunk_cands.append(cand_dict)

        if not chunk_cands:
            raise RuntimeError("No candidate frames could be sampled from chunk")

        if check_cancel:
            check_cancel()
        if on_progress:
            on_progress(0.50, f"Evaluating CLIP embeddings on {len(chunk_cands)} frames...")

        self._attach_quality_raw(chunk_cands, user_settings, native_scale, features)
        embeddings = self._clip_embeddings(chunk_cands, analysis, crop_sig, f2_stats)
        self._attach_iqa(chunk_cands, embeddings, user_settings, f2_stats)
        reference = (
            self._quality_reference(video_id, (float(chunk["start_s"]), float(chunk["end_s"])))
            if user_settings.get("quality_v2", True) else None
        )
        learned_active = self._score_batch(chunk_cands, embeddings, target_person_emb, user_settings, reference)

        if check_cancel:
            check_cancel()
        if on_progress:
            on_progress(0.75, f"Selecting top {target_k} frames...")

        chunk_cands.sort(key=lambda c: float(c.get("scores", {}).get("composite", 0.0)), reverse=True)
        picks = chunk_cands[:target_k]
        runners = chunk_cands[target_k : target_k + max(target_k * 2, 20)]

        for idx, p in enumerate(picks):
            p["role"] = "pick"
            p["state"] = "keep"
            p["rank"] = idx + 1
        for idx, r in enumerate(runners):
            r["role"] = "runner_up"
            r["state"] = "candidate"
            r["rank"] = target_k + idx + 1

        if on_progress:
            on_progress(0.85, "Writing native frames and thumbnails...")

        preview_dir = self.settings.cache_dir / "previews" / video_id
        cand_dir = self.settings.cache_dir / "candidates" / video_id
        preview_dir.mkdir(parents=True, exist_ok=True)
        cand_dir.mkdir(parents=True, exist_ok=True)

        old_frames = self.db.all("SELECT id, preview_path, full_path FROM frames WHERE chunk_id=?", (chunk_id,))
        for of in old_frames:
            if of.get("preview_path"):
                try:
                    Path(of["preview_path"]).unlink(missing_ok=True)
                except Exception:
                    pass
            if of.get("full_path"):
                try:
                    Path(of["full_path"]).unlink(missing_ok=True)
                except Exception:
                    pass

        pick_ts = [p["t"] for p in picks]
        def write_pick(target_t: float, native_img: np.ndarray):
            pick_path = cand_dir / f"full_c{chunk_id}_{int(target_t * 1000)}.png"
            cv2.imwrite(str(pick_path), native_img)
        decode_exact_frames(vpath, pick_ts, on_frame=write_pick)

        all_to_store = picks + runners
        self._selection_embeddings(all_to_store, analysis, crop_sig, user_settings, features, f2_stats)
        saved_records = []
        seen_store_ts: set[float] = set()

        for item in all_to_store:
            t = round(float(item["t"]), 3)
            if t in seen_store_ts:
                continue
            seen_store_ts.add(t)
            is_pick = item.get("role") == "pick"

            prev_path = preview_dir / f"prev_c{chunk_id}_{int(t * 1000)}.webp"
            h, w = item["img_bgr"].shape[:2]
            pw = 384
            ph = max(16, int(h * (pw / w)))
            small = cv2.resize(item["img_bgr"], (pw, ph), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(prev_path), small, [cv2.IMWRITE_WEBP_QUALITY, 85])

            full_path = cand_dir / f"full_c{chunk_id}_{int(t * 1000)}.png"
            if not is_pick:
                cv2.imwrite(str(full_path), item["img_bgr"])

            saved_records.append({
                "item": item,
                "t": t,
                "prev_path": str(prev_path),
                "full_path": str(full_path),
                "is_pick": is_pick,
            })

        with self.db.write() as conn:
            conn.execute("DELETE FROM picks WHERE chunk_id=?", (chunk_id,))
            conn.execute("DELETE FROM frames WHERE chunk_id=?", (chunk_id,))
            for rec in saved_records:
                item = rec["item"]
                is_pick = rec["is_pick"]
                cursor = conn.execute(
                    """
                    INSERT OR REPLACE INTO frames (
                        video_id, chunk_id, t, metrics, phash, embedding,
                        zs_scores, preview_path, full_path, native
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        chunk_id,
                        rec["t"],
                        json.dumps(item["metrics"]),
                        item["metrics"]["phash"],
                        item["embedding"].tobytes(),
                        json.dumps(item.get("scores", {})),
                        rec["prev_path"],
                        rec["full_path"],
                        1 if is_pick else 0,
                    ),
                )
                frame_id = cursor.lastrowid
                conn.execute(
                    """
                    INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (frame_id, chunk_id, item.get("role", "pick"), item.get("rank", 0), item.get("state", "keep")),
                )
            conn.execute("UPDATE chunks SET dirty=0 WHERE id=?", (chunk_id,))
            conn.execute("UPDATE videos SET status='curating_300' WHERE id=?", (video_id,))
        self._persist_analysis(video_id, analysis, features, chunk_cands, f2_stats)

        return {
            "chunk_id": chunk_id,
            "video_id": video_id,
            "picks_count": len(picks),
            "runners_count": len(runners),
            "stats": {
                "sampling": sampling_stats,
                "dhash_dropped": int(sampling_stats.get("dhash_dropped", 0)),
                "learned_weights_active": learned_active,
                "quality_v2": bool(user_settings.get("quality_v2", True)),
                "quality_gates": gate_stats,
                **f2_stats,
            },
        }

    def _run_extract_chunk_job(
        self,
        job_id: int,
        video_id: str,
        video_name: str,
        chunk_id: int,
        options: dict[str, Any],
    ) -> None:
        if not self._start_job(job_id, video_id, video_name, chunk_id=chunk_id):
            return

        def update_job_progress(p: float, step: str):
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
            self.broadcast_event("job_progress", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": chunk_id,
                "progress": p,
                "step": step,
            })

        try:
            res = self._execute_extract_chunk(
                chunk_id,
                k=options.get("k"),
                budget_pct=options.get("budget_pct"),
                on_progress=update_job_progress,
                check_cancel=lambda: self._check_cancel(job_id),
            )
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status='finished', error=NULL, progress=1.0, finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )
            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": chunk_id,
                "picks_count": res.get("picks_count", 0),
                "runners_count": res.get("runners_count", 0),
                "stats": res.get("stats", {}),
            })
            self.broadcast_event("chunk_extracted", {
                "video_id": video_id,
                "chunk_id": chunk_id,
                "picks_count": res.get("picks_count", 0),
            })
        except JobCancelled:
            self._finish_cancelled(job_id, video_id, video_name)
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
                self._settle_video_status(conn, video_id)
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": chunk_id,
                "error": str(ex),
            })
        finally:
            self._cancel_flags.discard(job_id)

    def submit_openrouter_caption_job(self, video_id: str, options: dict[str, Any]) -> int:
        now = time.time()
        vid = self.db.one("SELECT name FROM videos WHERE id=?", (video_id,))
        video_name = vid["name"] if vid else "Video"

        with self.db.write() as conn:
            self._assert_no_active_job(video_id)
            cursor = conn.execute(
                """
                INSERT INTO jobs (type, video_id, chunk_id, status, progress, created_at)
                VALUES ('openrouter_caption', ?, NULL, 'queued', 0.0, ?)
                """,
                (video_id, now),
            )
            job_id = cursor.lastrowid
            conn.execute("UPDATE videos SET status='captioning' WHERE id=?", (video_id,))

        self.broadcast_event("job_queued", {
            "job_id": job_id,
            "video_id": video_id,
            "video_name": video_name,
            "chunk_id": None,
        })
        self.caption_executor.submit(self._run_openrouter_caption_job, job_id, video_id, options, video_name)
        return job_id

    def _run_openrouter_caption_job(self, job_id: int, video_id: str, options: dict[str, Any], video_name: str) -> None:
        if not self._start_job(job_id, video_id, video_name):
            return

        try:
            import httpx

            video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
            if not video:
                raise ValueError(f"Video {video_id} not found")

            api_key = (
                options.get("api_key")
                or (self.db.get_setting("user_settings", {}) or {}).get("openrouter_api_key")
                or getattr(self.settings, "openrouter_api_key", "")
            )
            if not api_key:
                api_key = os.getenv("OPENROUTER_API_KEY", "")
            if not api_key:
                env_file = Path(__file__).resolve().parent.parent.parent / ".env"
                if env_file.exists():
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("OPENROUTER_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            if not api_key:
                raise ValueError("OpenRouter API Key not provided. Please set OPENROUTER_API_KEY.")

            primary_model = options.get("model", "google/gemma-4-31b-it")
            layer2_model = options.get("fallback_layer2", "deepseek/deepseek-v4.1-flash")
            layer3_model = options.get("fallback_layer3", "x-ai/grok-4.7")
            # Reasoning is off by default: with effort "high" OpenRouter spends most of max_tokens on reasoning
            # and the visible caption gets cut off. When enabled, build_request_payload raises the budget.
            reasoning_effort = normalize_reasoning_effort(options.get("reasoning_effort"))
            if not options.get("mode"):
                from pipeline.profiles import caption_mode_for

                options["mode"] = caption_mode_for(self.db, video_id, fallback=user_settings_for_mode(self.db))
            mode = normalize_caption_mode(options["mode"])
            trigger = str(options.get("trigger") or "restrained_elegance").strip() or "restrained_elegance"
            class_noun = options.get("class_noun", "woman")
            max_tokens = int(options.get("max_tokens") or DEFAULT_CAPTION_MAX_TOKENS)
            temperature = float(options.get("temperature", DEFAULT_CAPTION_TEMPERATURE))
            top_p = float(options.get("top_p", DEFAULT_CAPTION_TOP_P))
            gemma_temp = float(options.get("gemma_temperature", options.get("primary_temperature", temperature)))
            deepseek_temp = float(options.get("deepseek_temperature", options.get("fallback_temperature", temperature)))
            grok_temp = float(options.get("grok_temperature", options.get("fallback_layer3_temperature", temperature)))
            p_version = caption_prompt_version(mode)

            # Captions accepted per layer ('reused' = kept from the captions table / disk without a new call)
            accepted = {"layer1": 0, "layer2": 0, "layer3": 0, "reused": 0}
            # Replies that sent a frame on to the next layer (refusal, cut-off or failed validation gate)
            rejected = {"layer1": 0, "layer2": 0, "layer3": 0}
            job_usage = empty_usage()
            completed_count = 0
            failures: list[str] = []

            query = """
                SELECT f.id, f.chunk_id, f.t, f.full_path, f.metrics, f.zs_scores,
                       c.position as chunk_pos, c.caption_model, p.crop
                FROM frames f
                JOIN picks p ON f.id = p.frame_id
                JOIN chunks c ON f.chunk_id = c.id
                WHERE f.video_id = ? AND p.state IN ('keep', 'star')
                ORDER BY c.position ASC, f.t ASC
            """
            kept_frames = self.db.all(query, (video_id,))
            if not kept_frames:
                raise RuntimeError("No approved frames found to caption.")

            # Ensure all approved frames are decoded at native resolution before captioning (C1/H1)
            ensure_native_frames(self.db, [item["id"] for item in kept_frames])

            total_frames = len(kept_frames)
            vslug = video["slug"]
            from pipeline.indexer import compute_video_title_hash
            video_title_hash = video.get("title_hash") or compute_video_title_hash(video["name"])
            per_video_dir = self.settings.finished_dir / "per_video" / vslug
            merged_dir = self.settings.finished_dir / "merged"
            per_video_dir.mkdir(parents=True, exist_ok=True)
            merged_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = merged_dir / "manifest.jsonl"

            def update_prog(done: int, step: str):
                p = round((done / total_frames), 2)
                with self.db.write() as conn:
                    conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
                self.broadcast_event("job_progress", {
                    "job_id": job_id,
                    "video_id": video_id,
                    "video_name": video_name,
                    "chunk_id": None,
                    "progress": p,
                    "step": step,
                })

            def progress_step() -> str:
                return (
                    f"Captioned {completed_count}/{total_frames} [L1: {accepted['layer1']} | "
                    f"L2: {accepted['layer2']} | L3: {accepted['layer3']} | reused: {accepted['reused']}] "
                    f"cost ${job_usage['cost_usd']:.4f}"
                )

            update_prog(0, f"Starting 3-Layer Cascade ({primary_model} ➜ {layer2_model} ➜ {layer3_model}) for {total_frames} frames...")

            user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))
            concurrency = int(options.get("concurrency") or user_settings.get("openrouter_concurrency", 4))
            openrouter_max_dim = int(options.get("max_dim") or user_settings.get("openrouter_max_dim", 512))
            max_edge = int(user_settings.get("max_edge", 2048))
            normalize_buckets = bool(user_settings.get("normalize_to_buckets", True))
            active_buckets = resolve_buckets(
                options.get("bucket_preset") or user_settings.get("export_bucket_preset", "4_buckets")
            )

            def record_export(conn, frame_id: int, png: Path, merged_png: Path) -> None:
                conn.execute(
                    """
                    INSERT INTO exports (frame_id, per_video_path, merged_path, exported_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(frame_id) DO UPDATE SET
                        per_video_path=excluded.per_video_path,
                        merged_path=excluded.merged_path,
                        exported_at=excluded.exported_at
                    """,
                    (frame_id, str(png), str(merged_png), time.time()),
                )

            def render_frame(item: dict[str, Any]) -> np.ndarray | None:
                full_path = Path(item["full_path"] or "")
                if not full_path.is_file():
                    return None
                raw_img = cv2.imread(str(full_path))
                if raw_img is None:
                    return None
                # Same renderer as the exporter: crop, letterbox, watermark, 1MP center-crop bucket normalization
                return render_export_image(
                    raw_img,
                    crop=item.get("crop"),
                    letterbox=video.get("letterbox"),
                    watermark_rect=video.get("watermark_rect"),
                    max_edge=max_edge,
                    normalize_buckets=normalize_buckets,
                    buckets=active_buckets,
                )

            def reusable_caption(item: dict[str, Any], out_txt: Path) -> str:
                """An accepted caption row, else a VLM .txt on disk (adopted as 'imported'); '' when none or cut off."""
                row = get_accepted_caption(self.db, item["id"])
                if row and not looks_truncated(row["text"]):
                    return row["text"]
                if existing_caption_kind(out_txt) != "caption":
                    return ""
                text = out_txt.read_text(encoding="utf-8").strip()
                if not text or looks_truncated(text):
                    return ""
                with self.db.write() as conn:
                    import_caption(conn, item["id"], video_id, text, source="imported",
                                   created_at=out_txt.stat().st_mtime, job_id=job_id)
                return text

            async def process_all_async():
                nonlocal completed_count
                sem = asyncio.Semaphore(concurrency)
                file_lock = asyncio.Lock()

                async with httpx.AsyncClient() as client:
                    async def process_single_item(item: dict[str, Any]) -> dict[str, Any] | None:
                        # One frame's timeout, upstream error or exhausted cascade must not fail the whole batch
                        try:
                            return await caption_item(item)
                        except Exception as ex:
                            failures.append(f"t={item['t']:.3f}s: {type(ex).__name__}: {ex}")
                            return None

                    async def caption_item(item: dict[str, Any]) -> dict[str, Any] | None:
                        nonlocal completed_count
                        if job_id in self._cancel_flags:
                            return None

                        fname_stem = f"{vslug}_c{item['chunk_pos'] + 1:02d}_{int(item['t'] * 1000):07d}"
                        out_png = per_video_dir / f"{fname_stem}.png"
                        out_txt = per_video_dir / f"{fname_stem}.txt"
                        merged_png = merged_dir / f"{fname_stem}.png"
                        merged_txt = merged_dir / f"{fname_stem}.txt"
                        stale_sidecars = (per_video_dir / f"{fname_stem}.json", merged_dir / f"{fname_stem}.json")

                        # Per-chunk model dispatch: if use_chunk_models is True, use chunk's assigned model
                        use_chunk_models = options.get("use_chunk_models", True)
                        item_cap_model = (item.get("caption_model") or "").strip()
                        l1_model = item_cap_model if (use_chunk_models and item_cap_model) else primary_model

                        # Resume support: a caption already in the captions table (or a VLM .txt on disk) is
                        # kept without re-billing. Exporter stubs and captions cut off mid-sentence get replaced.
                        caption_text = reusable_caption(item, out_txt)
                        if caption_text:
                            existing_png = cv2.imread(str(out_png)) if out_png.is_file() else None
                            needs_render = existing_png is None or (
                                normalize_buckets
                                and (existing_png.shape[1], existing_png.shape[0]) not in active_buckets
                            )
                            if needs_render:
                                img = render_frame(item)
                                if img is None:
                                    return None
                                cv2.imwrite(str(out_png), img)
                                link_or_copy(out_png, merged_png)
                            write_caption_files(caption_text, out_txt, merged_txt, stale_sidecars=stale_sidecars)
                            if not merged_png.exists():
                                link_or_copy(out_png, merged_png)
                            accepted["reused"] += 1
                            completed_count += 1
                            with self.db.write() as conn:
                                record_export(conn, item["id"], out_png, merged_png)
                            update_prog(completed_count, progress_step())
                            return {
                                "source_video": video["name"],
                                "video_id": video_id,
                                "chunk_id": item["chunk_id"],
                                "timestamp_s": item["t"],
                                "caption": caption_text,
                                "model": l1_model,
                                "layer": "layer1_cached",
                            }

                        if not Path(item["full_path"] or "").is_file():
                            return None

                        async with sem:
                            if job_id in self._cancel_flags:
                                return None

                            img_snapped = render_frame(item)
                            if img_snapped is None:
                                return None
                            h, w = img_snapped.shape[:2]

                            # -------------------------------------------------------------
                            # 3-Layer Cascade: a refused, cut-off or gate-rejected caption goes to the next
                            # layer; other errors (network, upstream 5xx) fail the frame at once.
                            # -------------------------------------------------------------
                            cascade = [
                                ("layer1", l1_model, gemma_temp, reasoning_effort if "gemma" in l1_model.lower() else None),
                                ("layer2", layer2_model, deepseek_temp, None),
                                ("layer3", layer3_model, grok_temp, None),
                            ]
                            frame_usage = empty_usage()
                            caption_text, warnings, layer_used, model_used = "", [], "", ""
                            rejections: list[str] = []
                            try:
                                for layer, layer_model, layer_temp, layer_reasoning in cascade:
                                    try:
                                        caption_text, warnings = await self.captioner.caption_single_frame(
                                            client=client,
                                            img_bgr=img_snapped,
                                            api_key=api_key,
                                            model=layer_model,
                                            mode=mode,
                                            trigger=trigger,
                                            class_noun=class_noun,
                                            max_tokens=max_tokens,
                                            temperature=layer_temp,
                                            top_p=top_p,
                                            reasoning_effort=layer_reasoning,
                                            max_dim=openrouter_max_dim,
                                            video_hash=video_title_hash,
                                            usage=frame_usage,
                                            tag_hints=self.image_tags.confirmed_tag_names(item["id"]),
                                        )
                                    except CASCADE_ERRORS as ex:
                                        rejected[layer] += 1
                                        rejections.append(f"{layer} {layer_model}: {type(ex).__name__}: {ex}")
                                        continue
                                    layer_used, model_used = layer, layer_model
                                    break
                            finally:
                                # Every billed attempt counts, including refusals and failed frames
                                for key in ("prompt_tokens", "completion_tokens", "cost_usd", "calls"):
                                    job_usage[key] += frame_usage.get(key) or 0
                                job_usage["cost_reported"] = job_usage["cost_reported"] or bool(frame_usage.get("cost_reported"))

                            if not layer_used:
                                raise RuntimeError("all 3 cascade layers rejected the frame: " + " | ".join(rejections))
                            accepted[layer_used] += 1
                            warnings = list(warnings or [])
                            caption_model_used = model_used
                            if layer_used != "layer1":
                                caption_model_used = f"{model_used} (fallback from {l1_model})"
                                warnings.append(f"Recovered via {layer_used} ({model_used}) after: " + " | ".join(rejections))

                            # Files are written only after the caption succeeded
                            cv2.imwrite(str(out_png), img_snapped)
                            out_txt.write_text(caption_text, encoding="utf-8")
                            # A stub sidecar would mark this real caption as a stub again
                            for stale in stale_sidecars:
                                stale.unlink(missing_ok=True)
                            for src, dst in [(out_png, merged_png), (out_txt, merged_txt)]:
                                link_or_copy(src, dst)

                            billed = frame_usage["calls"] > 0
                            frame_cost = round(frame_usage["cost_usd"], 8) if frame_usage["cost_reported"] else None
                            manifest_entry = {
                                "source_video": video["name"],
                                "video_id": video_id,
                                "video_title_hash": video_title_hash,
                                "chunk_id": item["chunk_id"],
                                "timestamp_s": item["t"],
                                "width": w,
                                "height": h,
                                "bucket": f"{w}x{h}",
                                "caption": caption_text,
                                "model": caption_model_used,
                                "layer": layer_used,
                                "mode": mode,
                                "prompt_version": p_version,
                                "cost_usd": frame_cost,
                                "prompt_tokens": frame_usage["prompt_tokens"] if billed else None,
                                "completion_tokens": frame_usage["completion_tokens"] if billed else None,
                                "cascade_stats": {
                                    "layer1_gemma_success": accepted["layer1"],
                                    "layer2_deepseek_success": accepted["layer2"],
                                    "layer3_grok_success": accepted["layer3"],
                                    "layer1_refusals": rejected["layer1"],
                                    "layer2_refusals": rejected["layer2"],
                                },
                                "metrics": item.get("metrics"),
                                "scores": item.get("zs_scores"),
                                "warnings": warnings,
                                "exported_at": time.time(),
                            }
                            async with file_lock:
                                append_manifest_record(manifest_path, manifest_entry)
                                with self.db.write() as conn:
                                    record_caption(
                                        conn, item["id"], video_id, caption_text,
                                        model=model_used, layer=layer_used, mode=mode, prompt_version=p_version,
                                        trigger=trigger, cost_usd=frame_cost,
                                        prompt_tokens=manifest_entry["prompt_tokens"],
                                        completion_tokens=manifest_entry["completion_tokens"],
                                        warnings=warnings, finish_reason=frame_usage.get("finish_reason"),
                                        job_id=job_id,
                                    )
                                    record_export(conn, item["id"], out_png, merged_png)
                                completed_count += 1
                                update_prog(completed_count, progress_step())
                            return manifest_entry

                    raw_results = await asyncio.gather(*(process_single_item(item) for item in kept_frames))
                    return [r for r in raw_results if r is not None]

            captioned_results = asyncio.run(process_all_async())

            # Frames captioned before the cancel keep their files and export rows (each one is complete)
            self._check_cancel(job_id)
            cost_summary = {
                "cost_usd": round(job_usage["cost_usd"], 6) if job_usage["cost_reported"] else None,
                "prompt_tokens": job_usage["prompt_tokens"],
                "completion_tokens": job_usage["completion_tokens"],
                "api_calls": job_usage["calls"],
            }
            cost_note = (
                f"Cost ${job_usage['cost_usd']:.4f}, {job_usage['prompt_tokens']} prompt + "
                f"{job_usage['completion_tokens']} completion tokens over {job_usage['calls']} calls."
            )
            if failures and not captioned_results:
                raise RuntimeError(f"All {len(failures)} frames failed. First error: {failures[0]} {cost_note}")

            failure_note = (
                f"{len(failures)} of {total_frames} frames failed and stay uncaptioned (re-run to retry). "
                f"First error: {failures[0]} {cost_note}"
                if failures else None
            )
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status='finished', error=?, progress=1.0, finished_at=? WHERE id=?",
                    (failure_note, time.time(), job_id),
                )
                # 'exported' only when every approved frame has a caption; otherwise back to curating
                self._settle_video_status(conn, video_id)

            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "exported": len(captioned_results),
                "failed": len(failures),
                "layers": dict(accepted),
                "rejected": dict(rejected),
                "prompt_version": p_version,
                **cost_summary,
            })
            # R6: incremental RAG reindex (background, never raises; only the API's manager has a handler)
            from pipeline.rag.auto_reindex import notify_caption_job_finished
            notify_caption_job_finished(self, job_id=job_id, video_id=video_id, captioned=len(captioned_results))

        except JobCancelled:
            self._finish_cancelled(job_id, video_id, video_name)
        except Exception as ex:
            err_msg = f"{type(ex).__name__}: {str(ex)}".strip(": ")
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (err_msg, time.time(), job_id))
                # Never leave video stuck in 'captioning'
                self._settle_video_status(conn, video_id)
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "error": str(ex),
            })
        finally:
            self._cancel_flags.discard(job_id)

    def _run_auto_process_job(self, job_id: int, video_id: str, video_name: str, target_frames: int) -> None:
        from pipeline.autoprocess import AutoProcessor

        processor = AutoProcessor(self.db, self.settings, job_manager=self)
        if not self._start_job(job_id, video_id, video_name):
            return

        def on_prog(pct: float, step: str):
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET progress=? WHERE id=?", (pct, job_id))
            self.broadcast_event("job_progress", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "progress": pct,
                "step": step,
            })

        try:
            res = processor.process_video(video_id, target_frames=target_frames, on_progress=on_prog)
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status='finished', progress=1.0, finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )
                conn.execute("UPDATE videos SET status='exported' WHERE id=?", (video_id,))
            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "exported": res["exported_frames"],
            })
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
                self._settle_video_status(conn, video_id)
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "error": str(ex),
            })
        finally:
            self._cancel_flags.discard(job_id)

    def _run_job(self, job_id: int, video_id: str, chunk_id: int | None, video_name: str) -> None:
        if not self._start_job(job_id, video_id, video_name, chunk_id):
            return

        try:
            if chunk_id:
                chunks = self.db.all("SELECT * FROM chunks WHERE id=?", (chunk_id,))
            else:
                chunks = self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))

            total_chunks = len(chunks)
            if total_chunks == 0:
                with self.db.write() as conn:
                    conn.execute("UPDATE jobs SET status='finished', progress=1.0, finished_at=? WHERE id=?", (time.time(), job_id))
                self.broadcast_event("job_finished", {
                    "job_id": job_id,
                    "video_id": video_id,
                    "video_name": video_name,
                    "chunk_id": chunk_id,
                })
                return

            user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))

            for idx, chunk in enumerate(chunks):
                if job_id in self._cancel_flags:
                    break

                def on_progress(stage: str, frac: float):
                    p = round((idx + frac) / total_chunks, 2)
                    with self.db.write() as conn:
                        conn.execute("UPDATE jobs SET progress=? WHERE id=?", (p, job_id))
                    self.broadcast_event("job_progress", {
                        "job_id": job_id,
                        "video_id": video_id,
                        "video_name": video_name,
                        "chunk_id": chunk["id"],
                        "progress": p,
                        "step": f"Chunk {idx + 1}/{total_chunks}: {stage}",
                    })

                # Extract single chunk with live stage callbacks
                self._process_chunk(video_id, chunk, user_settings, on_progress)

                progress = round((idx + 1) / total_chunks, 2)
                with self.db.write() as conn:
                    conn.execute("UPDATE jobs SET progress=? WHERE id=?", (progress, job_id))
                self.broadcast_event("job_progress", {
                    "job_id": job_id,
                    "video_id": video_id,
                    "video_name": video_name,
                    "chunk_id": chunk["id"],
                    "progress": progress,
                    "step": f"Chunk {idx + 1}/{total_chunks} Complete",
                })

            final_status = "cancelled" if job_id in self._cancel_flags else "finished"
            with self.db.write() as conn:
                conn.execute(
                    "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                    (final_status, time.time(), job_id),
                )
                conn.execute("UPDATE videos SET status='curating' WHERE id=? AND status='extracting'", (video_id,))

            self.broadcast_event(f"job_{final_status}", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": chunk_id,
            })

        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
                self._settle_video_status(conn, video_id)
            self.broadcast_event("job_error", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "error": str(ex),
            })
        finally:
            self._cancel_flags.discard(job_id)

    def _process_chunk(
        self,
        video_id: str,
        chunk: dict[str, Any],
        user_settings: dict[str, Any],
        on_progress: Any = None,
    ) -> None:
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            return

        vpath = Path(video["path"])
        sample_fps = float(user_settings.get("sample_fps", 4.0))
        survivor_frac = float(user_settings.get("survivor_frac", 0.5))
        weights = user_settings.get("weights", {})
        prompts = user_settings.get("prompts", {})

        # Step 1: Decode sampled frames
        if on_progress:
            on_progress("Decoding frames", 0.05)
        raw_samples = sample_chunk_frames(vpath, chunk["start_s"], chunk["end_s"], sample_fps)
        if not raw_samples:
            return

        # Step 2: Compute Tier-1 metrics
        if on_progress:
            on_progress("Quality metrics", 0.25)
        candidates: list[dict[str, Any]] = []
        for t, img_bgr in raw_samples:
            metrics = compute_tier1_metrics(img_bgr)
            candidates.append({
                "t": t,
                "metrics": metrics,
                "img_bgr": img_bgr,
            })

        # Step 3: Tier-1 filtering & ranking
        target_k = chunk.get("k", 5)
        survivors, rejected = filter_and_rank_tier1(
            candidates,
            weights=weights,
            survivor_frac=survivor_frac,
            min_survivors=max(target_k * 2, 16),
        )
        if not survivors:
            return

        # Step 4: Tier-2 Multimodal CLIP embedding & classification
        if on_progress:
            on_progress("CLIP scoring", 0.50)
        survivor_images = [s["img_bgr"] for s in survivors]
        embeddings = self.classifier.embed_images(survivor_images)
        zs_scores = self.classifier.classify_and_score(embeddings, prompts)

        target_pid = video.get("target_person_id")
        target_person_emb = None
        if target_pid:
            target_person_emb = person_embedding(self.db.get_person(target_pid))

        for i, s in enumerate(survivors):
            s["embedding"] = embeddings[i]
            s["zs_scores"] = zs_scores[i]
            if target_person_emb is not None:
                try:
                    face_res = self.face_engine.match_frame(s["img_bgr"], target_person_emb)
                    s["zs_scores"]["target_person_sim"] = face_res["target_person_sim"]
                    s["zs_scores"]["target_person_matched"] = face_res["matched"]
                    s["zs_scores"]["face_count"] = face_res["face_count"]
                    s["zs_scores"]["faces"] = face_res["faces"]
                except Exception:
                    pass

        # Step 5: MMR Diverse Top-K Selection
        if on_progress:
            on_progress("Diverse selection", 0.75)
        picks, runner_ups, unselected = select_diverse_top_k(
            survivors,
            k=target_k,
            runner_ups=max(target_k, int(user_settings.get("runner_ups", 20))),
            rule=chunk.get("rule", "person"),
            weights=weights,
            lam=user_settings.get("mmr_lambda", 0.7),
            alpha=user_settings.get("mmr_alpha", 0.8),
        )

        # Step 6: Save preview thumbnails and full-res frames
        preview_dir = self.settings.cache_dir / "previews" / video_id
        preview_dir.mkdir(parents=True, exist_ok=True)
        cand_dir = self.settings.cache_dir / "candidates" / video_id
        cand_dir.mkdir(parents=True, exist_ok=True)

        all_to_save = picks + runner_ups

        # Step 5b: Zero-shot Tagging for chunk and frames
        if on_progress:
            on_progress("Tagging frames", 0.82)
        chunk_summary_tags, per_frame_tags = self.tagger.tag_chunk_frames(all_to_save)
        for i, item in enumerate(all_to_save):
            if "zs_scores" not in item:
                item["zs_scores"] = {}
            item["zs_scores"]["tags"] = per_frame_tags[i]

        # Step 6: Save preview thumbnails and full-res frames to disk
        if on_progress:
            on_progress(f"Saving {len(all_to_save)} frames", 0.88)

        # Batch decode exact full-resolution native frames for all picks in one single pass
        pick_timestamps = [item["t"] for item in picks]
        exact_frames = decode_exact_frames(vpath, pick_timestamps)

        saved_records = []
        for item in all_to_save:
            t = item["t"]
            is_pick = item.get("role") == "pick"

            # Save 384px WebP preview
            prev_path = preview_dir / f"prev_c{chunk['id']}_{int(t * 1000)}.webp"
            h, w = item["img_bgr"].shape[:2]
            pw = 384
            ph = max(16, int(h * (pw / w)))
            small = cv2.resize(item["img_bgr"], (pw, ph), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(prev_path), small, [cv2.IMWRITE_WEBP_QUALITY, 85])

            # Save full-resolution frame (picks get exact full native res from single-pass decode)
            full_path = cand_dir / f"full_c{chunk['id']}_{int(t * 1000)}.png"
            if is_pick:
                exact_bgr = exact_frames.get(t)
                if exact_bgr is not None:
                    cv2.imwrite(str(full_path), exact_bgr)
                else:
                    cv2.imwrite(str(full_path), item["img_bgr"])
            else:
                cv2.imwrite(str(full_path), item["img_bgr"])

            saved_records.append({
                "item": item,
                "t": t,
                "prev_path": str(prev_path),
                "full_path": str(full_path),
            })

        # Step 7: Fast single-transaction database commit
        with self.db.write() as conn:
            cur = conn.execute("SELECT tags FROM chunks WHERE id=?", (chunk["id"],))
            r = cur.fetchone()
            existing_tags = []
            if r and r["tags"]:
                try:
                    existing_tags = json.loads(r["tags"]) if isinstance(r["tags"], str) else r["tags"]
                except Exception:
                    existing_tags = []
            merged_tags = list(dict.fromkeys(existing_tags + chunk_summary_tags))
            conn.execute("UPDATE chunks SET tags=? WHERE id=?", (json.dumps(merged_tags), chunk["id"]))

            # Delete previous frames for this chunk
            conn.execute("DELETE FROM frames WHERE chunk_id=?", (chunk["id"],))

            for rec in saved_records:
                item = rec["item"]
                cursor = conn.execute(
                    """
                    INSERT INTO frames (
                        video_id, chunk_id, t, metrics, phash, embedding,
                        zs_scores, preview_path, full_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        chunk["id"],
                        rec["t"],
                        json.dumps(item["metrics"]),
                        item["metrics"]["phash"],
                        item["embedding"].tobytes(),
                        json.dumps(item["zs_scores"]),
                        rec["prev_path"],
                        rec["full_path"],
                    ),
                )
                frame_id = cursor.lastrowid
                conn.execute(
                    """
                    INSERT INTO picks (frame_id, chunk_id, role, rank, state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (frame_id, chunk["id"], item.get("role", "pick"), item.get("rank", 0), item.get("state", "keep")),
                )

            # Mark chunk as clean
            conn.execute("UPDATE chunks SET dirty=0 WHERE id=?", (chunk["id"],))

        # Free memory and trigger garbage collection immediately
        del raw_samples, candidates, survivors, survivor_images, all_to_save, saved_records, exact_frames
        import gc
        gc.collect()
