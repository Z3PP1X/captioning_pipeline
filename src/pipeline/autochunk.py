"""Intelligent auto-chunking & scene segmentation with target-frame budget allocation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.config import Settings, get_settings
from pipeline.db import DB
from pipeline.decode import sample_chunk_frames
from pipeline.indexer import Indexer
from pipeline.metrics import compute_tier1_metrics, hard_filter_check


class AutoChunker:
    """Detects scene boundaries, drops intros/standstills, and distributes frame budgets."""

    def __init__(self, db: DB, settings: Settings | None = None, indexer: Indexer | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.indexer = indexer or Indexer(self.db, self.settings)

    def generate_balanced_chunks(
        self,
        video_id: str,
        target_frames: int = 150,
        min_scene_s: float = 25.0,
        max_scene_s: float = 360.0,
    ) -> list[dict[str, Any]]:
        """Automatically segment video into coherent scenes and allocate K to hit target_frames."""
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video or video.get("broken"):
            return []

        dur = float(video["duration"])
        if dur < min_scene_s:
            # Single short video
            return [{
                "video_id": video_id,
                "position": 0,
                "start_s": 0.0,
                "end_s": round(dur, 2),
                "k": max(5, target_frames),
                "rule": "person",
                "tags": [],
                "notes": "Full short video",
            }]

        filmstrip = self.indexer.get_or_build_filmstrip(video_id)
        raw_cuts = filmstrip.get("cuts", [])

        # 1. Determine safe processing bounds (skip intro titles / outro credits if long)
        start_bound = 15.0 if dur > 180.0 else 0.0
        end_bound = dur - 15.0 if dur > 180.0 else dur

        # 2. Filter raw cuts within bounds
        cuts_in_range = [c for c in raw_cuts if start_bound < c < end_bound]

        # 3. Build coherent scene boundaries
        boundaries: list[float] = [start_bound]
        for c in cuts_in_range:
            curr_len = c - boundaries[-1]
            if curr_len >= min_scene_s:
                boundaries.append(c)
            elif curr_len > max_scene_s:
                # Force intermediate split
                boundaries.append(boundaries[-1] + max_scene_s)

        if end_bound - boundaries[-1] >= min_scene_s:
            boundaries.append(end_bound)
        elif len(boundaries) > 1:
            boundaries[-1] = end_bound

        # Fallback if too few cuts detected: uniform interval splitting
        if len(boundaries) <= 2:
            num_splits = max(3, min(12, int(dur // 120.0)))
            step = (end_bound - start_bound) / num_splits
            boundaries = [start_bound + i * step for i in range(num_splits + 1)]

        # 4. Filter candidate scenes (drop empty/static/corrupt scenes)
        vpath = Path(video["path"])
        valid_scenes: list[tuple[float, float]] = []

        for i in range(len(boundaries) - 1):
            s_start = round(boundaries[i], 2)
            s_end = round(boundaries[i + 1], 2)
            scene_len = s_end - s_start
            if scene_len < 10.0:
                continue

            # Quick quality sanity check: sample 1 frame near mid-point
            mid_t = s_start + scene_len * 0.5
            try:
                probe_samples = sample_chunk_frames(vpath, mid_t, min(dur, mid_t + 1.0), sample_fps=1.0)
                if probe_samples:
                    _, probe_img = probe_samples[0]
                    m = compute_tier1_metrics(probe_img)
                    ok, _ = hard_filter_check(m)
                    if not ok:
                        # Skip pure black, uniform, or blown-out scene
                        continue
            except Exception:
                pass

            valid_scenes.append((s_start, s_end))

        if not valid_scenes:
            # Fallback to single chunk across duration
            valid_scenes = [(start_bound, end_bound)]

        # 4b. Enforce strict constraint: MAXIMUM 15 CHUNKS
        max_allowed_chunks = 15
        while len(valid_scenes) > max_allowed_chunks:
            # Merge shortest adjacent pair
            shortest_idx = 0
            min_combined_len = float("inf")
            for i in range(len(valid_scenes) - 1):
                comb_len = valid_scenes[i + 1][1] - valid_scenes[i][0]
                if comb_len < min_combined_len:
                    min_combined_len = comb_len
                    shortest_idx = i

            # Merge shortest_idx and shortest_idx + 1
            merged = (valid_scenes[shortest_idx][0], valid_scenes[shortest_idx + 1][1])
            valid_scenes = (
                valid_scenes[:shortest_idx]
                + [merged]
                + valid_scenes[shortest_idx + 2 :]
            )

        # 5. Distribute budget percentages (totaling 100.0%) and base K
        num_valid = len(valid_scenes)
        durs = [s_end - s_start for s_start, s_end in valid_scenes]
        tot_dur = max(1.0, sum(durs))

        # Initial proportional allocation with min floor of 2.0%
        raw_pcts = [max(2.0, (d / tot_dur) * 100.0) for d in durs]
        pct_sum = sum(raw_pcts)
        budget_pcts = [round((p / pct_sum) * 100.0, 1) for p in raw_pcts]
        # Adjust rounding difference on largest chunk
        diff = round(100.0 - sum(budget_pcts), 1)
        if diff != 0:
            max_p_idx = budget_pcts.index(max(budget_pcts))
            budget_pcts[max_p_idx] = round(budget_pcts[max_p_idx] + diff, 1)

        chunks: list[dict[str, Any]] = []
        for idx, (s_start, s_end) in enumerate(valid_scenes):
            bpct = budget_pcts[idx]
            # base_k proportional to budget pct for target_frames
            chunk_k = max(5, round(target_frames * (bpct / 100.0)))
            chunks.append({
                "video_id": video_id,
                "position": idx,
                "start_s": s_start,
                "end_s": s_end,
                "k": chunk_k,
                "budget_pct": bpct,
                "rule": "person",
                "tags": [],
                "notes": f"Auto scene #{idx + 1}",
            })

        # Save to database
        with self.db.write() as conn:
            conn.execute("DELETE FROM chunks WHERE video_id=?", (video_id,))
            for ch in chunks:
                conn.execute(
                    """
                    INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, rule, tags, notes, dirty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        video_id,
                        ch["position"],
                        ch["start_s"],
                        ch["end_s"],
                        ch["k"],
                        ch["budget_pct"],
                        ch["rule"],
                        json.dumps(ch["tags"]),
                        ch["notes"],
                    ),
                )
            conn.execute("UPDATE videos SET status='chunked' WHERE id=?", (video_id,))

        return self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))

    def save_user_chunks(self, video_id: str, chunks_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Human-in-the-loop update: save modified chunk timecodes and budget weights (max 15 chunks)."""
        if not chunks_data:
            raise ValueError("Chunks list cannot be empty")
        if len(chunks_data) > 15:
            raise ValueError(f"Exceeded maximum of 15 chunks (received {len(chunks_data)})")

        # Normalize budget percentages to sum to 100.0%
        raw_pcts = [float(c.get("budget_pct", 0.0)) for c in chunks_data]
        if sum(raw_pcts) <= 0:
            raw_pcts = [100.0 / len(chunks_data)] * len(chunks_data)
        tot = sum(raw_pcts)
        norm_pcts = [round((p / tot) * 100.0, 1) for p in raw_pcts]
        diff = round(100.0 - sum(norm_pcts), 1)
        if diff != 0:
            max_idx = norm_pcts.index(max(norm_pcts))
            norm_pcts[max_idx] = round(norm_pcts[max_idx] + diff, 1)

        with self.db.write() as conn:
            conn.execute("DELETE FROM chunks WHERE video_id=?", (video_id,))
            for idx, c in enumerate(chunks_data):
                bpct = norm_pcts[idx]
                chunk_k = max(5, int(c.get("k", round(300 * (bpct / 100.0)))))
                tags = c.get("tags", [])
                tags_json = json.dumps(tags) if isinstance(tags, list) else tags
                conn.execute(
                    """
                    INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, rule, tags, notes, dirty)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        video_id,
                        idx,
                        round(float(c["start_s"]), 2),
                        round(float(c["end_s"]), 2),
                        chunk_k,
                        bpct,
                        c.get("rule", "person"),
                        tags_json,
                        c.get("notes", f"Scene #{idx + 1}"),
                    ),
                )
            conn.execute("UPDATE videos SET status='chunked' WHERE id=?", (video_id,))

        return self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))
