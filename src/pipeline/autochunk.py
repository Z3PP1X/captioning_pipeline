"""Intelligent auto-chunking & scene segmentation with target-frame budget allocation."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB
from pipeline.decode import sample_chunk_frames
from pipeline.indexer import Indexer
from pipeline.metrics import compute_tier1_metrics, hard_filter_check


class DestructiveRechunkError(ValueError):
    """Saving would delete chunks that hold picks or exports (cascades to frames/picks/exports)."""

    def __init__(self, affected: list[dict[str, Any]]):
        picks = sum(a["picks"] for a in affected)
        exports = sum(a["exports"] for a in affected)
        super().__init__(
            f"Re-chunking would delete {len(affected)} chunk(s) with {picks} curated pick(s) "
            f"and {exports} export record(s). Resend with confirm_destructive=true to proceed."
        )
        self.affected = affected


PRIORITIES = ("skip", "low", "normal", "high")
# Relative share of the frame budget a scene gets per unit of duration. `skip` removes the scene from
# extraction entirely; `high` doubles and `low` more than halves its share.
PRIORITY_WEIGHTS: dict[str, float] = {"skip": 0.0, "low": 0.4, "normal": 1.0, "high": 2.0}
MAX_CHUNKS = 15
MIN_SCENE_KEEP_S = 10.0


def normalize_ranges(ranges: list[Any] | None, duration: float) -> list[tuple[float, float]]:
    """Clamp (start, end) pairs to [0, duration], drop empty ones, sort and merge overlapping ones."""
    clean: list[tuple[float, float]] = []
    for r in ranges or []:
        try:
            a, b = (float(r[0]), float(r[1])) if not isinstance(r, dict) else (float(r["start_s"]), float(r["end_s"]))
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        a, b = max(0.0, min(a, b)), min(float(duration), max(a, b))
        if b - a > 1e-6:
            clean.append((round(a, 3), round(b, 3)))
    clean.sort()
    merged: list[tuple[float, float]] = []
    for a, b in clean:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def subtract_ranges(start: float, end: float, exclusions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of [start, end] not covered by the (normalized) exclusion ranges."""
    segments: list[tuple[float, float]] = []
    cursor = start
    for a, b in exclusions:
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            segments.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    if cursor < end:
        segments.append((cursor, end))
    return [(round(a, 3), round(b, 3)) for a, b in segments if b - a > 1e-6]


def allocate_budgets(
    durations: list[float],
    priorities: list[str] | None = None,
    floor_pct: float = 2.0,
) -> list[float]:
    """Frame budget percentages (one decimal, summing to 100.0) proportional to duration x priority weight.

    Scenes that are not skipped never fall below `floor_pct`; skipped scenes get exactly 0.
    """
    n = len(durations)
    prios = priorities or ["normal"] * n
    if len(prios) != n:
        raise ValueError("priorities must match durations")
    for pr in prios:
        if pr not in PRIORITY_WEIGHTS:
            raise ValueError(f"Unknown priority '{pr}'. Allowed: {list(PRIORITIES)}")
    weights = [max(0.0, d) * PRIORITY_WEIGHTS[pr] for d, pr in zip(durations, prios)]
    total = sum(weights)
    if n == 0 or total <= 0:
        raise ValueError("At least one scene must not be skipped")

    raw = [max(floor_pct, (w / total) * 100.0) if w > 0 else 0.0 for w in weights]
    raw_sum = sum(raw)
    pcts = [round((r / raw_sum) * 100.0, 1) for r in raw]
    diff = round(100.0 - sum(pcts), 1)
    if diff != 0:
        idx = pcts.index(max(pcts))
        pcts[idx] = round(pcts[idx] + diff, 1)
    return pcts


class AutoChunker:
    """Detects scene boundaries, drops intros/standstills, and distributes frame budgets."""

    def __init__(self, db: DB, settings: Settings | None = None, indexer: Indexer | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.indexer = indexer or Indexer(self.db, self.settings)

    @staticmethod
    def _segment_boundaries(
        cuts: list[float],
        a: float,
        b: float,
        min_scene_s: float,
        max_scene_s: float,
        floor_splits: int,
    ) -> list[float]:
        """Scene boundaries inside one uninterrupted stretch [a, b] of the video."""
        seg_len = b - a
        if seg_len < min_scene_s:
            return [a, b]

        boundaries: list[float] = [a]
        for c in cuts:
            if a < c < b and c - boundaries[-1] >= min_scene_s:
                boundaries.append(c)
        if b - boundaries[-1] >= min_scene_s:
            boundaries.append(b)
        elif len(boundaries) > 1:
            boundaries[-1] = b

        # Too few cuts detected: uniform interval splitting
        if len(boundaries) <= 2:
            num_splits = max(floor_splits, min(12, int(seg_len // 120.0)))
            step = seg_len / num_splits
            boundaries = [a + i * step for i in range(num_splits + 1)]

        # A stretch without cuts must not become one giant scene
        bounded: list[float] = [boundaries[0]]
        for nxt in boundaries[1:]:
            span = nxt - bounded[-1]
            if span > max_scene_s:
                parts = math.ceil(span / max_scene_s)
                base = bounded[-1]
                bounded.extend(base + span * i / parts for i in range(1, parts))
            bounded.append(nxt)
        return bounded

    def plan_scenes(
        self,
        video_id: str,
        min_scene_s: float = 25.0,
        max_scene_s: float = 360.0,
        exclude_ranges: list[Any] | None = None,
    ) -> list[tuple[float, float]]:
        """Detect scenes (at most 15) without touching the DB.

        `exclude_ranges` are (start_s, end_s) spans the human wants left out (credits, ads, an unwanted
        sequence): no scene overlaps them and scene boundaries always fall on their edges.
        """
        video = self.db.one("SELECT * FROM videos WHERE id=?", (video_id,))
        if not video or video.get("broken"):
            return []

        dur = float(video["duration"])
        exclusions = normalize_ranges(exclude_ranges, dur)

        # Safe bounds: skip intro titles / outro credits on long videos
        start_bound = 15.0 if dur > 180.0 else 0.0
        end_bound = dur - 15.0 if dur > 180.0 else dur
        segments = subtract_ranges(start_bound, end_bound, exclusions)
        if not segments:
            return []

        if dur < min_scene_s:
            candidate_scenes = list(segments)
        else:
            raw_cuts = self.indexer.get_or_build_filmstrip(video_id).get("cuts", [])
            floor_splits = 3 if not exclusions else 1
            candidate_scenes = []
            for seg_a, seg_b in segments:
                bounds = self._segment_boundaries(raw_cuts, seg_a, seg_b, min_scene_s, max_scene_s, floor_splits)
                candidate_scenes.extend((bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1))

        # Filter candidate scenes (drop empty/static/corrupt scenes)
        vpath = Path(video["path"])
        valid_scenes: list[tuple[float, float]] = []
        for raw_start, raw_end in candidate_scenes:
            s_start, s_end = round(raw_start, 2), round(raw_end, 2)
            scene_len = s_end - s_start
            if scene_len < MIN_SCENE_KEEP_S and dur >= min_scene_s:
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
            # Fall back to the uninterrupted stretches themselves
            valid_scenes = [(round(a, 2), round(b, 2)) for a, b in segments]

        # Enforce the strict maximum number of chunks: merge the shortest touching pair, never across
        # an excluded gap; if nothing can merge any more, drop the shortest scene
        while len(valid_scenes) > MAX_CHUNKS:
            best_idx, best_len = -1, float("inf")
            for i in range(len(valid_scenes) - 1):
                if abs(valid_scenes[i + 1][0] - valid_scenes[i][1]) > 0.011:
                    continue
                comb_len = valid_scenes[i + 1][1] - valid_scenes[i][0]
                if comb_len < best_len:
                    best_idx, best_len = i, comb_len
            if best_idx >= 0:
                merged = (valid_scenes[best_idx][0], valid_scenes[best_idx + 1][1])
                valid_scenes = valid_scenes[:best_idx] + [merged] + valid_scenes[best_idx + 2:]
            else:
                shortest = min(range(len(valid_scenes)), key=lambda i: valid_scenes[i][1] - valid_scenes[i][0])
                valid_scenes.pop(shortest)
        return valid_scenes

    @staticmethod
    def build_chunk_rows(
        video_id: str,
        scenes: list[tuple[float, float]],
        target_frames: int = 150,
        priorities: list[str] | None = None,
        person_presence: list[float | None] | None = None,
        face_presence: list[float | None] | None = None,
    ) -> list[dict[str, Any]]:
        """Chunk dicts for `scenes` with budgets from duration x priority (see `allocate_budgets`)."""
        durations = [e - s for s, e in scenes]
        prios = priorities or ["normal"] * len(scenes)
        budget_pcts = allocate_budgets(durations, prios)

        rows: list[dict[str, Any]] = []
        for idx, (s_start, s_end) in enumerate(scenes):
            bpct = budget_pcts[idx]
            rows.append({
                "video_id": video_id,
                "position": idx,
                "start_s": s_start,
                "end_s": s_end,
                # base_k proportional to budget pct for target_frames
                "k": max(5, round(target_frames * (bpct / 100.0))),
                "budget_pct": bpct,
                "caption_model": "google/gemma-4-31b-it",
                "rule": "person",
                "tags": [],
                "notes": f"Auto scene #{idx + 1}",
                "priority": prios[idx],
                "person_presence": person_presence[idx] if person_presence else None,
                "face_presence": face_presence[idx] if face_presence else None,
            })
        return rows

    def generate_balanced_chunks(
        self,
        video_id: str,
        target_frames: int = 150,
        min_scene_s: float = 25.0,
        max_scene_s: float = 360.0,
        confirm_destructive: bool = False,
        exclude_ranges: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Automatically segment video into coherent scenes and allocate K to hit target_frames."""
        scenes = self.plan_scenes(video_id, min_scene_s, max_scene_s, exclude_ranges)
        if not scenes:
            return []
        rows = self.build_chunk_rows(video_id, scenes, target_frames)
        # Suggestions carry no ids, so every existing chunk is replaced (guarded when it holds picks)
        return self.persist_chunks(video_id, rows, confirm_destructive=confirm_destructive)

    def apply_priorities(
        self,
        video_id: str,
        priorities: dict[int, str],
        target_frames: int | None = None,
    ) -> list[dict[str, Any]]:
        """Human-in-the-loop: set per-scene priorities and re-derive every budget from them.

        Overwrites hand-tuned budgets (duration x priority is the single source of truth here). Skipped
        scenes keep their row but get no budget and are ignored by extraction. Never deletes anything.
        """
        chunks = self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC, start_s ASC", (video_id,))
        if not chunks:
            raise ValueError("Video has no chunks")
        known = {c["id"] for c in chunks}
        unknown = [cid for cid in priorities if cid not in known]
        if unknown:
            raise ValueError(f"Unknown chunk id(s): {unknown}")

        final_prios = [priorities.get(c["id"], c.get("priority") or "normal") for c in chunks]
        budgets = allocate_budgets([c["end_s"] - c["start_s"] for c in chunks], final_prios)
        total = target_frames
        if total is None:
            total = int(merged_user_settings(self.db.get_setting("user_settings", {})).get("target_curated", 300))

        with self.db.write() as conn:
            for c, prio, bpct in zip(chunks, final_prios, budgets):
                conn.execute(
                    "UPDATE chunks SET priority=?, budget_pct=?, k=? WHERE id=?",
                    (prio, bpct, max(5, round(total * (bpct / 100.0))), c["id"]),
                )
        return self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC, start_s ASC", (video_id,))

    def persist_chunks(
        self,
        video_id: str,
        chunks: list[dict[str, Any]],
        confirm_destructive: bool = False,
    ) -> list[dict[str, Any]]:
        """Upsert chunks by id; delete only chunks missing from `chunks`.

        Deleting a chunk cascades to its frames, picks and exports, so removing a chunk that holds picks
        requires `confirm_destructive`. A chunk whose time range changed keeps its frames and is marked
        dirty until the next extraction.
        """
        existing = {c["id"]: c for c in self.db.all("SELECT * FROM chunks WHERE video_id=?", (video_id,))}
        keep_ids = {c.get("id") for c in chunks if c.get("id") in existing}
        removed_ids = [cid for cid in existing if cid not in keep_ids]

        if removed_ids and not confirm_destructive:
            affected = []
            for cid in removed_ids:
                counts = self.db.one(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM picks WHERE chunk_id=? AND role='pick') AS picks,
                        (SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.chunk_id=?) AS exports
                    """,
                    (cid, cid),
                )
                if counts["picks"] or counts["exports"]:
                    affected.append({"id": cid, "position": existing[cid]["position"], **counts})
            if affected:
                raise DestructiveRechunkError(affected)

        with self.db.write() as conn:
            for cid in removed_ids:
                conn.execute("DELETE FROM chunks WHERE id=?", (cid,))
            for idx, ch in enumerate(chunks):
                tags = ch.get("tags", [])
                old = existing.get(ch.get("id"))
                # Fields a client may omit keep their stored value (e.g. a UI that predates priorities)
                priority = ch.get("priority") or (old["priority"] if old else "normal")
                if priority not in PRIORITIES:
                    raise ValueError(f"Unknown priority '{priority}'. Allowed: {list(PRIORITIES)}")
                person_presence = ch["person_presence"] if "person_presence" in ch else (old or {}).get("person_presence")
                face_presence = ch["face_presence"] if "face_presence" in ch else (old or {}).get("face_presence")
                values = (
                    idx,
                    round(float(ch["start_s"]), 2),
                    round(float(ch["end_s"]), 2),
                    ch["k"],
                    ch["budget_pct"],
                    ch.get("caption_model") or "google/gemma-4-31b-it",
                    ch.get("rule", "person"),
                    json.dumps(tags) if isinstance(tags, list) else tags,
                    ch.get("notes", f"Scene #{idx + 1}"),
                    priority,
                    person_presence,
                    face_presence,
                )
                if old is None:
                    conn.execute(
                        """
                        INSERT INTO chunks (video_id, position, start_s, end_s, k, budget_pct, caption_model, rule, tags,
                                            notes, priority, person_presence, face_presence, dirty)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (video_id, *values),
                    )
                else:
                    moved = (abs(float(old["start_s"]) - values[1]) > 1e-6
                             or abs(float(old["end_s"]) - values[2]) > 1e-6)
                    conn.execute(
                        """
                        UPDATE chunks SET position=?, start_s=?, end_s=?, k=?, budget_pct=?, caption_model=?,
                                          rule=?, tags=?, notes=?, priority=?, person_presence=?, face_presence=?,
                                          dirty=?
                        WHERE id=?
                        """,
                        (*values, 1 if moved else old["dirty"], old["id"]),
                    )
            # Only a video without curation work goes back to 'chunked'; editing the budgets of a curated
            # or exported video must not demote it.
            conn.execute(
                """
                UPDATE videos SET status='chunked'
                WHERE id=? AND status NOT IN ('extracting', 'extracting_750', 'captioning')
                  AND (status IN ('new', 'chunking', 'chunked') OR NOT EXISTS (SELECT 1 FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=?))
                """,
                (video_id, video_id),
            )

        return self.db.all("SELECT * FROM chunks WHERE video_id=? ORDER BY position ASC", (video_id,))

    def save_user_chunks(
        self,
        video_id: str,
        chunks_data: list[dict[str, Any]],
        confirm_destructive: bool = False,
    ) -> list[dict[str, Any]]:
        """Human-in-the-loop update: save modified chunk timecodes and budget weights (max 15 chunks)."""
        if not chunks_data:
            raise ValueError("Chunks list cannot be empty")
        if len(chunks_data) > 15:
            raise ValueError(f"Exceeded maximum of 15 chunks (received {len(chunks_data)})")

        # Skipped scenes carry no budget; the rest is normalized to sum to 100.0%
        existing_prio = {
            c["id"]: c.get("priority") or "normal"
            for c in self.db.all("SELECT id, priority FROM chunks WHERE video_id=?", (video_id,))
        }
        prios = [c.get("priority") or existing_prio.get(c.get("id"), "normal") for c in chunks_data]
        bad = [p for p in prios if p not in PRIORITIES]
        if bad:
            raise ValueError(f"Unknown priority '{bad[0]}'. Allowed: {list(PRIORITIES)}")
        if all(p == "skip" for p in prios):
            raise ValueError("At least one scene must not be skipped")
        raw_pcts = [0.0 if p == "skip" else float(c.get("budget_pct", 0.0)) for c, p in zip(chunks_data, prios)]
        if sum(raw_pcts) <= 0:
            raw_pcts = [0.0 if p == "skip" else 1.0 for p in prios]
        tot = sum(raw_pcts)
        norm_pcts = [round((p / tot) * 100.0, 1) for p in raw_pcts]
        diff = round(100.0 - sum(norm_pcts), 1)
        if diff != 0:
            max_idx = norm_pcts.index(max(norm_pcts))
            norm_pcts[max_idx] = round(norm_pcts[max_idx] + diff, 1)

        rows = []
        for idx, c in enumerate(chunks_data):
            bpct = norm_pcts[idx]
            rows.append({
                **c,
                "k": max(5, int(c.get("k", round(300 * (bpct / 100.0))))),
                "budget_pct": bpct,
                "priority": prios[idx],
            })
        return self.persist_chunks(video_id, rows, confirm_destructive=confirm_destructive)
