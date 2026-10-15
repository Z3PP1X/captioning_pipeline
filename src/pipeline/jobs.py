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
    snap_to_multiple_of_16,
)
from pipeline.metrics import compute_tier1_metrics, filter_and_rank_tier1, hard_filter_check, phash_hamming_distance
from pipeline.openrouter import (
    DEFAULT_CAPTION_MAX_TOKENS,
    ModelRefusalError,
    OpenRouterCaptioner,
    looks_truncated,
    normalize_reasoning_effort,
)
from pipeline.autopilot import Autopilot, JobContext
from pipeline.face import FaceEngine, person_embedding
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

    def _start_job(self, job_id: int, video_id: str, video_name: str, chunk_id: int | None = None) -> bool:
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

    def _finish_cancelled(self, job_id: int, video_id: str, video_name: str) -> None:
        with self.db.write() as conn:
            conn.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                (time.time(), job_id),
            )
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
        self.decode_executor.submit(self._run_autopilot_job, job_id, video_id, video_name, runner)
        return job_id

    def _run_autopilot_job(
        self,
        job_id: int,
        video_id: str,
        video_name: str,
        runner: Callable[[JobContext], dict[str, Any]],
    ) -> None:
        if not self._start_job(job_id, video_id, video_name):
            self.autopilot.on_job_cancelled(video_id)
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
            self.autopilot.on_job_cancelled(video_id)
            return
        except Exception as ex:
            with self.db.write() as conn:
                conn.execute("UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?", (str(ex), time.time(), job_id))
            self.broadcast_event("job_error", {"job_id": job_id, "video_id": video_id, "video_name": video_name, "error": str(ex)})
            self.autopilot.on_job_failed(video_id, str(ex))
            return
        finally:
            self._cancel_flags.discard(job_id)

        for callback in ctx.after_finish:
            try:
                callback()
            except Exception as ex:  # a failed follow-up must not turn the finished job into an error
                self.autopilot.on_job_failed(video_id, str(ex))

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

    def _run_extract_750_job(self, job_id: int, video_id: str, video_name: str, options: dict[str, Any] | None = None) -> None:
        if not self._start_job(job_id, video_id, video_name):
            return

        try:
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

            target_person = None
            target_person_emb = None
            target_pid = video.get("target_person_id")
            if target_pid:
                target_person = self.db.get_person(target_pid)
                target_person_emb = person_embedding(target_person)
                if target_person_emb is None:
                    target_person = None

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

            sample_desc = (
                f"Sampling candidate frames with target person '{target_person['name']}' face recognition..."
                if target_person else f"Sampling candidate frames across {total_chunks} chunks..."
            )
            update_job_progress(0.05, sample_desc)

            # 1. Sample candidate frames per chunk (streamed at 512px with pre-crop & gates)
            all_candidates: list[dict[str, Any]] = []
            seen_cand_ts: set[float] = set()

            for idx, ch in enumerate(chunks):
                self._check_cancel(job_id)

                bpct = float(ch.get("budget_pct", 100.0 / max(1, total_chunks)))
                chunk_n = max(15, round(target_total * (bpct / 100.0)))
                dur = max(1.0, float(ch["end_s"]) - float(ch["start_s"]))
                needed_fps = max(1.0, (chunk_n * 1.5) / dur)

                raw_samples = sample_chunk_frames(
                    vpath,
                    float(ch["start_s"]),
                    float(ch["end_s"]),
                    sample_fps=needed_fps,
                    max_samples=chunk_n * 2,
                    max_dim=512,  # Stream at 512px to keep RAM minimal (C4/H5)
                )

                chunk_cands: list[dict[str, Any]] = []
                for t, img_bgr in raw_samples:
                    t_val = round(float(t), 3)
                    if t_val in seen_cand_ts:
                        continue

                    # Pre-crop letterbox and watermark before metrics & CLIP (C5/H8/H9)
                    img_cropped = apply_letterbox_crop(img_bgr, letterbox)
                    img_cropped = apply_watermark_crop(img_cropped, watermark_rect)

                    metrics = compute_tier1_metrics(img_cropped)

                    # Hard filter check (C5/H8)
                    ok, _ = hard_filter_check(metrics, sharpness_floor=15.0)
                    if not ok:
                        continue

                    # Intra-chunk pHash deduplication <= 4 (C5/H8)
                    if any(phash_hamming_distance(metrics["phash"], kept["metrics"]["phash"]) <= 4 for kept in chunk_cands):
                        continue

                    # Face detection & target person matching via YuNet + SFace
                    if target_person_emb is not None:
                        match_res = self.face_engine.match_frame(img_cropped, target_person_emb)
                        metrics["target_person_sim"] = match_res["max_sim"]
                        metrics["target_person_matched"] = match_res["matched"]
                        metrics["face_count"] = match_res["face_count"]
                        metrics["faces"] = match_res["faces"]
                    else:
                        detected_faces = self.face_engine.detect_faces(img_cropped)
                        metrics["target_person_sim"] = 0.0
                        metrics["target_person_matched"] = False
                        metrics["face_count"] = len(detected_faces)
                        metrics["faces"] = [{"box": f["box"], "score": f["score"]} for f in detected_faces]

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
                update_job_progress(round(p, 2), f"Sampled Chunk {idx + 1}/{total_chunks} ({len(chunk_cands)} valid frames)")

            if not all_candidates:
                raise RuntimeError("No candidate frames could be sampled from video")

            # 2. Batch Embeddings & 7-Dimension Scoring (metrics are already computed during sampling)
            self._check_cancel(job_id)
            update_job_progress(0.50, f"Computing CLIP embeddings on {len(all_candidates)} filtered frames...")
            images_to_embed = [c["img_bgr"] for c in all_candidates]
            embeddings = self.classifier.embed_images(images_to_embed, batch_size=32)

            self._check_cancel(job_id)
            step_desc = (
                f"Evaluating scoring with target person '{target_person['name']}' priority..."
                if target_person else "Evaluating 7-dimension scoring criteria..."
            )
            update_job_progress(0.68, step_desc)
            self.scoring_engine.score_candidates_batch(
                all_candidates, embeddings, target_person_embedding=target_person_emb
            )

            # 3. Multi-objective reduction
            update_job_progress(0.78, f"Optimizing best {target_curated} frame subset...")
            picks_300, runners_450, stats = self.scoring_engine.reduce_750_to_300(
                all_candidates, chunks, target_total=target_curated, category_quotas=category_quotas, mmr_lambda=mmr_lambda
            )

            # 4. Save previews and decode exact full-resolution frames for picks
            update_job_progress(0.82, "Saving thumbnails and native frames...")
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
                self._check_cancel(job_id)
                ts = [p["t"] for p in ch_picks]
                def write_pick(target_t: float, native_img: np.ndarray):
                    pick_path = cand_dir / f"full_c{cid}_{int(target_t * 1000)}.png"
                    cv2.imwrite(str(pick_path), native_img)
                decode_exact_frames(vpath, ts, on_frame=write_pick)

            # Last cancel point: commit replaces the current frames, picks and exports.
            self._check_cancel(job_id)
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
            update_job_progress(0.95, "Committing records to database...")
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

                conn.execute("UPDATE videos SET status='curating_300' WHERE id=?", (video_id,))
                conn.execute(
                    "UPDATE jobs SET status='finished', error=NULL, progress=1.0, finished_at=? WHERE id=?",
                    (time.time(), job_id),
                )


            self.broadcast_event("job_finished", {
                "job_id": job_id,
                "video_id": video_id,
                "video_name": video_name,
                "chunk_id": None,
                "picks_count": len(picks_300),
                "runners_count": len(runners_450),
                "stats": stats,
            })

            del all_candidates, images_to_embed, embeddings, picks_300, runners_450, saved_records
            import gc
            gc.collect()

            # An autopilot run continues with the similarity pass (no-op for manual extractions)
            self.autopilot.on_extract_finished(video_id)

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
            mode = options.get("mode", "concept")
            trigger = options.get("trigger", "restrained_elegance")
            class_noun = options.get("class_noun", "woman")
            max_tokens = int(options.get("max_tokens") or DEFAULT_CAPTION_MAX_TOKENS)
            temperature = float(options.get("temperature", 0.80))
            top_p = float(options.get("top_p", 0.90))
            gemma_temp = float(options.get("gemma_temperature", options.get("primary_temperature", temperature)))
            deepseek_temp = float(options.get("deepseek_temperature", options.get("fallback_temperature", temperature)))
            grok_temp = float(options.get("grok_temperature", options.get("fallback_layer3_temperature", temperature)))

            l1_success_count = 0
            l2_success_count = 0
            l3_success_count = 0
            l1_refusals = 0
            l2_refusals = 0
            completed_count = 0
            failures: list[str] = []

            query = """
                SELECT f.id, f.chunk_id, f.t, f.full_path, f.metrics, f.zs_scores,
                       c.position as chunk_pos, c.caption_model
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

            update_prog(0, f"Starting 3-Layer Cascade ({primary_model} ➜ {layer2_model} ➜ {layer3_model}) for {total_frames} frames...")

            user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))
            concurrency = int(options.get("concurrency") or user_settings.get("openrouter_concurrency", 4))
            openrouter_max_dim = int(options.get("max_dim") or user_settings.get("openrouter_max_dim", 512))

            async def process_all_async():
                nonlocal l1_success_count, l2_success_count, l3_success_count, l1_refusals, l2_refusals, completed_count
                sem = asyncio.Semaphore(concurrency)
                file_lock = asyncio.Lock()

                async with httpx.AsyncClient() as client:
                    async def process_single_item(item: dict[str, Any]) -> dict[str, Any] | None:
                        # One frame's timeout, upstream error or triple refusal must not fail the whole batch
                        try:
                            return await caption_item(item)
                        except Exception as ex:
                            failures.append(f"t={item['t']:.3f}s: {type(ex).__name__}: {ex}")
                            return None

                    async def caption_item(item: dict[str, Any]) -> dict[str, Any] | None:
                        nonlocal l1_success_count, l2_success_count, l3_success_count, l1_refusals, l2_refusals, completed_count
                        if job_id in self._cancel_flags:
                            return None

                        fname_stem = f"{vslug}_c{item['chunk_pos'] + 1:02d}_{int(item['t'] * 1000):07d}"
                        out_png = per_video_dir / f"{fname_stem}.png"
                        out_txt = per_video_dir / f"{fname_stem}.txt"

                        # Per-chunk model dispatch: if use_chunk_models is True, use chunk's assigned model
                        use_chunk_models = options.get("use_chunk_models", True)
                        item_cap_model = (item.get("caption_model") or "").strip()
                        l1_model = item_cap_model if (use_chunk_models and item_cap_model) else primary_model

                        # Resume support: if already captioned, keep existing caption without re-billing.
                        # Exporter stubs and captions cut off by the old 300-token cap get replaced.
                        caption_text = (
                            out_txt.read_text(encoding="utf-8").strip()
                            if existing_caption_kind(out_txt) == "caption" else ""
                        )
                        if caption_text and not looks_truncated(caption_text) and out_png.is_file():
                            l1_success_count += 1
                            completed_count += 1
                            # Ensure recorded in DB exports table
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
                                    (item["id"], str(out_png), str(merged_dir / out_png.name), time.time()),
                                )
                            update_prog(
                                completed_count,
                                f"Captioned {completed_count}/{total_frames} [L1: {l1_success_count} | L2 DeepSeek: {l2_success_count} | L3 Grok: {l3_success_count}]"
                            )
                            return {
                                "source_video": video["name"],
                                "video_id": video_id,
                                "chunk_id": item["chunk_id"],
                                "timestamp_s": item["t"],
                                "caption": caption_text,
                                "model": l1_model,
                                "layer": "layer1_cached",
                            }

                        full_path = Path(item["full_path"])
                        if not full_path.is_file():
                            return None

                        async with sem:
                            if job_id in self._cancel_flags:
                                return None

                            raw_img = cv2.imread(str(full_path))
                            if raw_img is None:
                                return None

                            # Render export image with crop, letterbox, watermark, max_edge, and multiples of 16
                            img_snapped = render_export_image(
                                raw_img,
                                crop=item.get("crop"),
                                letterbox=video.get("letterbox"),
                                watermark_rect=video.get("watermark_rect"),
                                max_edge=int(user_settings.get("max_edge", 2048)),
                            )
                            if img_snapped is None:
                                return None
                            h, w = img_snapped.shape[:2]
                            cv2.imwrite(str(out_png), img_snapped)

                            caption_model_used = l1_model
                            layer_used = "layer1"
                            warnings: list[str] = []

                            # -------------------------------------------------------------
                            # 3-Layer Cascade: Only send refused images to the next layer!
                            # -------------------------------------------------------------
                            try:
                                # Layer 1: Chunk-specific Model (Default: Google Gemma 4 31B)
                                l1_reasoning = reasoning_effort if "gemma" in l1_model.lower() else None
                                caption_text, warnings = await self.captioner.caption_single_frame(
                                    client=client,
                                    img_bgr=img_snapped,
                                    api_key=api_key,
                                    model=l1_model,
                                    mode=mode,
                                    trigger=trigger,
                                    class_noun=class_noun,
                                    max_tokens=max_tokens,
                                    temperature=gemma_temp,
                                    top_p=top_p,
                                    reasoning_effort=l1_reasoning,
                                    max_dim=openrouter_max_dim,
                                )
                                l1_success_count += 1
                            except ModelRefusalError:
                                l1_refusals += 1
                                # Layer 1 Refused -> Send ONLY this refused image to Layer 2 (DeepSeek V4.1 Flash)
                                try:
                                    caption_text, warnings = await self.captioner.caption_single_frame(
                                        client=client,
                                        img_bgr=img_snapped,
                                        api_key=api_key,
                                        model=layer2_model,
                                        mode=mode,
                                        trigger=trigger,
                                        class_noun=class_noun,
                                        max_tokens=max_tokens,
                                        temperature=deepseek_temp,
                                        top_p=top_p,
                                        reasoning_effort=None,
                                        max_dim=openrouter_max_dim,
                                    )
                                    l2_success_count += 1
                                    caption_model_used = f"{layer2_model} (fallback from {primary_model})"
                                    layer_used = "layer2"
                                    warnings.append(f"Recovered from {primary_model} refusal via Layer 2 ({layer2_model})")
                                except ModelRefusalError:
                                    l2_refusals += 1
                                    # Layer 2 also Refused -> Send this refused image to Layer 3 (Grok 4.7)
                                    caption_text, warnings = await self.captioner.caption_single_frame(
                                        client=client,
                                        img_bgr=img_snapped,
                                        api_key=api_key,
                                        model=layer3_model,
                                        mode=mode,
                                        trigger=trigger,
                                        class_noun=class_noun,
                                        max_tokens=max_tokens,
                                        temperature=grok_temp,
                                        top_p=top_p,
                                        reasoning_effort=None,
                                        max_dim=openrouter_max_dim,
                                    )
                                    l3_success_count += 1
                                    caption_model_used = f"{layer3_model} (fallback from {layer2_model} & {primary_model})"
                                    layer_used = "layer3"
                                    warnings.append(f"Recovered from {primary_model} & {layer2_model} refusals via Layer 3 ({layer3_model})")

                            out_txt.write_text(caption_text, encoding="utf-8")
                            # A stub sidecar would mark this real caption as a stub again
                            for stale in (per_video_dir / f"{fname_stem}.json", merged_dir / f"{fname_stem}.json"):
                                stale.unlink(missing_ok=True)

                            merged_png = merged_dir / f"{fname_stem}.png"
                            merged_txt = merged_dir / f"{fname_stem}.txt"
                            for src, dst in [(out_png, merged_png), (out_txt, merged_txt)]:
                                link_or_copy(src, dst)

                            manifest_entry = {
                                "source_video": video["name"],
                                "video_id": video_id,
                                "chunk_id": item["chunk_id"],
                                "timestamp_s": item["t"],
                                "width": w,
                                "height": h,
                                "caption": caption_text,
                                "model": caption_model_used,
                                "layer": layer_used,
                                "cascade_stats": {
                                    "layer1_gemma_success": l1_success_count,
                                    "layer2_deepseek_success": l2_success_count,
                                    "layer3_grok_success": l3_success_count,
                                    "layer1_refusals": l1_refusals,
                                    "layer2_refusals": l2_refusals,
                                },
                                "metrics": item.get("metrics"),
                                "scores": item.get("zs_scores"),
                                "warnings": warnings,
                                "exported_at": time.time(),
                            }
                            async with file_lock:
                                append_manifest_record(manifest_path, manifest_entry)
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
                                        (item["id"], str(out_png), str(merged_png), time.time()),
                                    )
                                completed_count += 1
                                update_prog(
                                    completed_count,
                                    f"Captioned {completed_count}/{total_frames} [L1 Gemma: {l1_success_count} | L2 DeepSeek: {l2_success_count} | L3 Grok: {l3_success_count}]"
                                )
                            return manifest_entry

                    raw_results = await asyncio.gather(*(process_single_item(item) for item in kept_frames))
                    return [r for r in raw_results if r is not None]


            captioned_results = asyncio.run(process_all_async())

            # Frames captioned before the cancel keep their files and export rows (each one is complete)
            self._check_cancel(job_id)
            if failures and not captioned_results:
                raise RuntimeError(f"All {len(failures)} frames failed. First error: {failures[0]}")

            failure_note = (
                f"{len(failures)} of {total_frames} frames failed and stay uncaptioned (re-run to retry). "
                f"First error: {failures[0]}"
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
            })

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
