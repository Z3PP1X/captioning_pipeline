"""End-to-end autonomous processor: auto-chunking, QC, tagging, and dataset export."""
from __future__ import annotations

import time
from typing import Any, Callable

from pipeline.autochunk import AutoChunker
from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.export import Exporter
from pipeline.indexer import Indexer
from pipeline.jobs import JobManager


class AutoProcessor:
    """Orchestrates end-to-end zero-touch processing for a single video or batch."""

    def __init__(
        self,
        db: DB,
        settings: Settings | None = None,
        job_manager: JobManager | None = None,
        exporter: Exporter | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.job_manager = job_manager or JobManager(self.db, self.settings)
        self.exporter = exporter or Exporter(self.db, self.settings)
        self.autochunker = AutoChunker(self.db, self.settings)

    def process_video(
        self,
        video_id: str,
        target_frames: int = 150,
        on_progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        """Runs the entire pipeline autonomously for a video:
        1. Auto-chunk & scene filter (targeting 100-200 frames total).
        2. Fast PyAV decoding, Tier-1 QC, CLIP equipment scoring, and MMR diversity.
        3. Local fine-grained zero-shot tagging (posture, gear, staging).
        4. Lossless PNG Snap-to-16 export + comma-separated .txt tag files.
        5. Hardlink to merged dataset and manifest.jsonl.
        """
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video:
            raise ValueError(f"Video {video_id} not found")

        vname = video["name"]
        t_start = time.time()

        if on_progress:
            on_progress(0.05, f"{vname}: Auto-detecting scenes...")

        # Step 1: Auto-detect scenes & allocate K per chunk
        chunks = self.autochunker.generate_balanced_chunks(video_id, target_frames=target_frames)
        if not chunks:
            raise RuntimeError(f"Could not generate scene chunks for {vname}")

        num_chunks = len(chunks)
        user_settings = merged_user_settings(self.db.get_setting("user_settings", {}))

        # Mark video as extracting
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET status='extracting' WHERE id=?", (video_id,))

        # Step 2 & 3: Process each chunk (QC, CLIP, MMR, Local Tagging)
        for idx, ch in enumerate(chunks):
            sub_start = 0.10 + 0.70 * (idx / num_chunks)
            sub_span = 0.70 / num_chunks

            def chunk_cb(stage: str, frac: float):
                if on_progress:
                    curr_p = round(sub_start + sub_span * frac, 2)
                    on_progress(curr_p, f"{vname} [Chunk {idx + 1}/{num_chunks}]: {stage}")

            self.job_manager._process_chunk(video_id, ch, user_settings, on_progress=chunk_cb)

        if on_progress:
            on_progress(0.85, f"{vname}: Exporting lossless PNGs and tags...")

        # Step 4: Export to finished per-video and merged datasets
        export_res = self.exporter.export_video(video_id)
        exported_count = export_res.get("exported", 0)

        # Mark video as exported
        with self.db.write() as conn:
            conn.execute("UPDATE videos SET status='exported' WHERE id=?", (video_id,))

        elapsed = round(time.time() - t_start, 2)
        if on_progress:
            on_progress(1.0, f"{vname}: Completed ({exported_count} frames exported in {elapsed}s)")

        return {
            "video_id": video_id,
            "video_name": vname,
            "chunks_count": num_chunks,
            "exported_frames": exported_count,
            "elapsed_s": elapsed,
            "status": "exported",
        }
