"""Re-evaluate the similarity of the selected frames and repair the selection.

Extraction already de-duplicates inside a chunk (MMR) and compares a short window of neighbouring picks
across chunks. This pass looks at the *finished* set as a whole, like a reviewer would:

1. Near-duplicates anywhere in the set (CLIP cosine or perceptual hash) are removed; the best runner-up of
   the same chunk that is not itself a lookalike takes the slot.
2. When a target person is configured, frames showing somebody else are swapped for runner-ups that are
   not, and non-matching picks are swapped for matching runner-ups until `person_focus` of the set shows
   the target person (as far as the runner-ups allow).
3. If removals left the set short, the gap is filled from the chunks furthest below their quota.

The decision logic (`refine_selection`) is pure and works on `FrameInfo` records; `Refiner` loads them from
SQLite, applies the swaps and returns a JSON-serializable report with before/after similarity statistics.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from pipeline.config import Settings, get_settings, merged_user_settings
from pipeline.db import DB

# Same cut-offs the scorer uses (SFace cosine): >= match is the target, < off_target is somebody else
MATCH_SIM = 0.363
OFF_TARGET_SIM = 0.28
MAX_REPORTED_SWAPS = 500


@dataclass
class RefineConfig:
    dup_similarity: float = 0.92
    dup_phash: int = 4
    person_focus: float = 0.0
    purge_off_target: bool = True
    fill_from_other_chunks: bool = True

    @classmethod
    def from_settings(cls, user_settings: dict[str, Any], **overrides: Any) -> "RefineConfig":
        auto = user_settings.get("autopilot") or {}
        cfg = cls(
            dup_similarity=float(auto.get("dup_similarity", cls.dup_similarity)),
            person_focus=float(auto.get("person_focus", 0.6)),
            purge_off_target=bool(auto.get("purge_off_target", True)),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


@dataclass
class FrameInfo:
    id: int
    chunk_id: int
    t: float
    role: str
    state: str
    rank: int
    emb: np.ndarray
    phash: int | None
    quality: float
    matched: bool
    face_count: int
    sim: float

    @property
    def off_target(self) -> bool:
        return self.face_count >= 1 and not self.matched and self.sim < OFF_TARGET_SIM


@dataclass
class Swap:
    drop_id: int | None
    add_id: int | None
    chunk_id: int
    reason: str


@dataclass
class RefineOutcome:
    kept: list[FrameInfo]
    swaps: list[Swap] = field(default_factory=list)
    unreplaced_drops: list[int] = field(default_factory=list)
    filled: int = 0


def _hamming(a: int | None, b: int | None) -> int:
    if a is None or b is None:
        return 64
    return (a ^ b).bit_count()


def measure(frames: list[FrameInfo], dup_similarity: float, dup_phash: int) -> dict[str, Any]:
    """Similarity statistics of a selection (the numbers shown in the report)."""
    n = len(frames)
    if n == 0:
        return {"count": 0, "mean_nn_sim": 0.0, "max_sim": 0.0, "dup_pairs": 0, "target_person_pct": 0.0, "off_target": 0}
    embs = np.stack([f.emb for f in frames]).astype(np.float32)
    sims = embs @ embs.T
    np.fill_diagonal(sims, -1.0)
    nn = sims.max(axis=1) if n > 1 else np.zeros(1)
    dup_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= dup_similarity or _hamming(frames[i].phash, frames[j].phash) <= dup_phash:
                dup_pairs += 1
    matched = sum(1 for f in frames if f.matched)
    return {
        "count": n,
        "mean_nn_sim": round(float(nn.mean()), 4),
        "max_sim": round(float(sims.max()) if n > 1 else 0.0, 4),
        "dup_pairs": dup_pairs,
        "target_person_pct": round(100.0 * matched / n, 1),
        "off_target": sum(1 for f in frames if f.off_target),
    }


def refine_selection(
    frames: list[FrameInfo],
    chunk_targets: dict[int, int],
    cfg: RefineConfig,
    has_target: bool,
) -> RefineOutcome:
    """Decide which picks to drop and which runner-ups take their place. Pure: nothing is mutated."""
    picks = [f for f in frames if f.role == "pick" and f.state in ("keep", "star")]
    pool: dict[int, list[FrameInfo]] = {}
    for f in frames:
        if f.role == "runner_up" and f.state != "reject":
            pool.setdefault(f.chunk_id, []).append(f)
    for runners in pool.values():
        runners.sort(key=lambda f: (-f.quality, f.rank))

    kept: list[FrameInfo] = []
    out = RefineOutcome(kept=kept)
    used: set[int] = set()

    def is_dup_of_kept(cand: FrameInfo, against: list[FrameInfo]) -> bool:
        if not against:
            return False
        embs = np.stack([k.emb for k in against])
        if float((embs @ cand.emb).max()) >= cfg.dup_similarity:
            return True
        return any(_hamming(cand.phash, k.phash) <= cfg.dup_phash for k in against)

    def best_replacement(
        chunk_id: int,
        against: list[FrameInfo],
        pred: Callable[[FrameInfo], bool] | None = None,
    ) -> FrameInfo | None:
        for cand in pool.get(chunk_id, []):
            if cand.id in used or (pred and not pred(cand)):
                continue
            if is_dup_of_kept(cand, against):
                continue
            return cand
        return None

    def promote(cand: FrameInfo) -> None:
        used.add(cand.id)
        kept.append(cand)

    # 1. Near-duplicates: starred frames first (the human chose them), then best quality first
    ordered = sorted(picks, key=lambda f: (f.state != "star", -f.quality, f.rank))
    dropped_dups: list[FrameInfo] = []
    for f in ordered:
        if f.state != "star" and is_dup_of_kept(f, kept):
            dropped_dups.append(f)
        else:
            kept.append(f)
    for f in dropped_dups:
        rep = best_replacement(f.chunk_id, kept)
        if rep:
            promote(rep)
            out.swaps.append(Swap(f.id, rep.id, f.chunk_id, "duplicate"))
        else:
            out.unreplaced_drops.append(f.id)

    # 2a. Frames that show somebody else instead of the target person
    if has_target and cfg.purge_off_target:
        for f in [k for k in kept if k.off_target and k.state != "star"]:
            others = [k for k in kept if k.id != f.id]
            rep = best_replacement(f.chunk_id, others, pred=lambda c: not c.off_target)
            if rep:
                kept.remove(f)
                promote(rep)
                out.swaps.append(Swap(f.id, rep.id, f.chunk_id, "off_target"))

    # 2b. Raise the share of frames that show the target person
    if has_target and cfg.person_focus > 0:
        def share() -> float:
            return sum(1 for k in kept if k.matched) / max(1, len(kept))

        for f in sorted([k for k in kept if not k.matched and k.state != "star"], key=lambda k: k.quality):
            if share() >= cfg.person_focus:
                break
            others = [k for k in kept if k.id != f.id]
            rep = best_replacement(f.chunk_id, others, pred=lambda c: c.matched)
            if rep:
                kept.remove(f)
                promote(rep)
                out.swaps.append(Swap(f.id, rep.id, f.chunk_id, "person_focus"))

    # 3. Refill the quota lost to unreplaced duplicates, from the chunks furthest below their target
    if cfg.fill_from_other_chunks:
        while len(kept) < len(picks):
            counts: dict[int, int] = {}
            for k in kept:
                counts[k.chunk_id] = counts.get(k.chunk_id, 0) + 1
            by_deficit = sorted(pool, key=lambda c: counts.get(c, 0) - chunk_targets.get(c, 0))
            rep = None
            for cid in by_deficit:
                rep = best_replacement(cid, kept)
                if rep:
                    break
            if rep is None:
                break
            promote(rep)
            out.swaps.append(Swap(None, rep.id, rep.chunk_id, "fill"))
            out.filled += 1

    return out


class Refiner:
    """Loads the picks of a video, runs `refine_selection`, applies the swaps and builds the report."""

    def __init__(self, db: DB, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()

    def _load(self, video_id: str) -> list[FrameInfo]:
        rows = self.db.all(
            """
            SELECT f.id, f.chunk_id, f.t, f.phash, f.embedding, f.zs_scores,
                   p.role, p.rank, p.state
            FROM frames f JOIN picks p ON p.frame_id = f.id
            WHERE f.video_id = ? AND f.embedding IS NOT NULL
            """,
            (video_id,),
        )
        frames: list[FrameInfo] = []
        for r in rows:
            emb = np.frombuffer(r["embedding"], dtype=np.float32)
            norm = float(np.linalg.norm(emb))
            if emb.size == 0 or norm < 1e-9:
                continue
            zs = r.get("zs_scores")
            if isinstance(zs, str):
                zs = json.loads(zs)
            zs = zs or {}
            sim = float(zs.get("target_person_sim", 0.0) or 0.0)
            matched = bool(zs.get("target_person_matched", False)) or sim >= MATCH_SIM
            try:
                ph = int(r["phash"], 16) if r.get("phash") else None
            except ValueError:
                ph = None
            frames.append(FrameInfo(
                id=r["id"], chunk_id=r["chunk_id"], t=float(r["t"]), role=r["role"], state=r["state"] or "keep",
                rank=int(r["rank"] or 0), emb=(emb / norm).astype(np.float32), phash=ph,
                quality=float(zs.get("composite", 0.5)) + (0.25 if matched else 0.0),
                matched=matched, face_count=int(zs.get("face_count", 0) or 0), sim=sim,
            ))
        return frames

    def run(
        self,
        video_id: str,
        cfg: RefineConfig | None = None,
        check_cancel: Callable[[], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        video = self.db.one("SELECT id, target_person_id, target_person_name FROM videos WHERE id=?", (video_id,))
        if not video:
            raise ValueError(f"Video {video_id} not found")
        cfg = cfg or RefineConfig.from_settings(merged_user_settings(self.db.get_setting("user_settings", {})))
        has_target = bool(video.get("target_person_id"))

        def progress(p: float, msg: str) -> None:
            if on_progress:
                on_progress(p, msg)

        progress(0.1, "Loading the selected frames...")
        frames = self._load(video_id)
        approved = [f for f in frames if f.role == "pick" and f.state in ("keep", "star")]
        if not approved:
            raise RuntimeError("No approved frames to evaluate")

        chunk_targets: dict[int, int] = {}
        for f in approved:
            chunk_targets[f.chunk_id] = chunk_targets.get(f.chunk_id, 0) + 1

        before = measure(approved, cfg.dup_similarity, cfg.dup_phash)
        if check_cancel:
            check_cancel()
        progress(0.4, f"Re-evaluating similarity of {len(approved)} frames...")
        outcome = refine_selection(frames, chunk_targets, cfg, has_target)
        after = measure(outcome.kept, cfg.dup_similarity, cfg.dup_phash)

        if check_cancel:
            check_cancel()
        progress(0.7, f"Applying {len(outcome.swaps)} swaps...")
        promoted = self._apply(video_id, frames, outcome)

        # Promoted runner-ups need their native-resolution frame before export or captioning
        if promoted:
            from pipeline.decode import ensure_native_frames
            try:
                ensure_native_frames(self.db, promoted)
            except Exception:
                pass  # export/caption decode missing natives on demand

        reasons: dict[str, int] = {}
        for sw in outcome.swaps:
            reasons[sw.reason] = reasons.get(sw.reason, 0) + 1
        report = {
            "config": {
                "dup_similarity": cfg.dup_similarity,
                "dup_phash": cfg.dup_phash,
                "person_focus": cfg.person_focus if has_target else 0.0,
                "purge_off_target": cfg.purge_off_target and has_target,
            },
            "target_person": {
                "enabled": has_target,
                "name": video.get("target_person_name"),
                "pct_before": before["target_person_pct"],
                "pct_after": after["target_person_pct"],
            },
            "before": before,
            "after": after,
            "picks_before": len(approved),
            "picks_after": len(outcome.kept),
            "swaps_by_reason": reasons,
            "removed_without_replacement": len(outcome.unreplaced_drops),
            "filled": outcome.filled,
            "swaps": [
                {"drop_frame_id": s.drop_id, "add_frame_id": s.add_id, "chunk_id": s.chunk_id, "reason": s.reason}
                for s in outcome.swaps[:MAX_REPORTED_SWAPS]
            ],
        }
        progress(1.0, "Similarity re-evaluation complete")
        return report

    def _apply(self, video_id: str, frames: list[FrameInfo], outcome: RefineOutcome) -> list[int]:
        by_id = {f.id: f for f in frames}
        runner_rank: dict[int, int] = {}
        pick_rank: dict[int, int] = {}
        for f in frames:
            table = runner_rank if f.role == "runner_up" else pick_rank
            table[f.chunk_id] = max(table.get(f.chunk_id, -1), f.rank)

        promoted: list[int] = []
        with self.db.write() as conn:
            def demote(frame_id: int) -> None:
                chunk_id = by_id[frame_id].chunk_id
                runner_rank[chunk_id] = runner_rank.get(chunk_id, -1) + 1
                conn.execute(
                    "UPDATE picks SET role='runner_up', state='candidate', rank=? WHERE frame_id=?",
                    (runner_rank[chunk_id], frame_id),
                )

            for sw in outcome.swaps:
                if sw.drop_id is not None:
                    demote(sw.drop_id)
                rank = by_id[sw.drop_id].rank if sw.drop_id is not None else pick_rank.get(sw.chunk_id, -1) + 1
                if sw.drop_id is None:
                    pick_rank[sw.chunk_id] = rank
                conn.execute(
                    "UPDATE picks SET role='pick', state='keep', rank=? WHERE frame_id=?",
                    (rank, sw.add_id),
                )
                promoted.append(sw.add_id)
            for frame_id in outcome.unreplaced_drops:
                demote(frame_id)
        return promoted
