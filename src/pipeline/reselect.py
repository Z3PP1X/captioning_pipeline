"""Selection v2: re-run pick vs runner_up over a video's stored frames (review §6.2-6.3).

Quality-weighted facility location, greedy, using the frames' stored CLIP embeddings and composite scores (no
decode, no model): F(S) = sum_i w_i * max_{j in S} sim(i, j) blended with the quality of the picks.

- Embeddings are whitened per video (mean removed, L2-normalised) so cosine values spread out;
  sim = max(0, cos)^2 + 0.25 * [same chunk] * exp(-|dt| / 6 s).
- Gain of j = diversity * coverage_gain_j / max(coverage gains) + (1 - diversity) * q_j, with q the
  per-video min-max normalised composite (learned weights blended in when active).
- Per-chunk quota k_c = largest-remainder share of the target by budget_pct; floor = floor((1 - e) * k_c),
  cap = ceil((1 + e) * k_c) for elasticity e, unless absolute `per_chunk_floor` / `per_chunk_cap` are given.
  Phase 1 fills floors, phase 2 adds globally under the caps until the target is reached.
- Starred and exported frames are locked: always picks (they count toward chunk counts and the target).
  Rejected frames are never picked. A candidate whose raw cosine to a pick exceeds `dup_threshold` is skipped.
- Runners are ranked per chunk by their final marginal gain.

Phase F2 additions:
- Selection embedder: the coverage similarity can use DINOv2-S embeddings of the letterboxed frames (setting
  `selection_embedder`, from the analysis cache; `embed_rows` callable) instead of CLIP; the hard near-duplicate
  check always uses the stored CLIP embeddings, where `similarity_dedup_threshold` is calibrated.
- Axis caps (`axis_caps = {axis: max_share}` over multi_person / solo / equipment / face, membership from
  `zs_scores`, see `frame_axes`): a frame whose axis already holds `floor(max_share * target)` picks is only taken
  when no uncapped eligible frame is left (reported as `overflow`); locked frames count toward the caps.
- Cross-dataset dedup: candidates whose CLIP cosine to a frame already exported from another video is
  >= the dedup threshold are never picked (locked frames excepted).
Shot ids from §6.3 are not used; chunks stand in.
"""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from pipeline.learned_weights import base_composite, blend_composite, components_from_scores


class ReselectError(ValueError):
    """Invalid knobs for this video (mapped to HTTP 400)."""


def _zs(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}


AXES = ("multi_person", "solo", "equipment", "face")
AXIS_ALIASES = {"multiple_persons": "multi_person", "multiple": "multi_person", "multi": "multi_person",
                "one_person": "solo", "tools_equipment": "equipment", "tools": "equipment",
                "face_visible": "face"}


def frame_axes(zs: dict) -> dict[str, bool]:
    """Category axes of a frame (same 0.45 / 0.5 thresholds as the distribution stats)."""
    def num(k: str) -> float:
        try:
            return float(zs.get(k) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "multi_person": num("multiple_persons") >= 0.45 or num("face_count") >= 2,
        "solo": num("one_person") >= 0.45,
        "equipment": max(num("tools"), num("equipment")) >= 0.45,
        "face": bool(zs.get("face_visible_50pct")) or num("face_score") >= 0.50,
    }


def normalise_axis_caps(axis_caps: dict[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, share in (axis_caps or {}).items():
        axis = AXIS_ALIASES.get(str(key), str(key))
        if axis not in AXES:
            raise ReselectError(f"Unknown axis '{key}' (use {', '.join(AXES)})")
        try:
            val = float(share)
        except (TypeError, ValueError):
            raise ReselectError(f"axis_caps.{key} must be a number between 0 and 1")
        if not (0.0 <= val <= 1.0) or math.isnan(val):
            raise ReselectError(f"axis_caps.{key} must be between 0 and 1")
        out[axis] = val
    return out


def exported_reference(db: Any, video_id: str) -> np.ndarray:
    """L2-normalised CLIP embeddings of frames exported from other videos (cross-dataset dedup index)."""
    vecs: list[np.ndarray] = []
    for r in db.all(
        """
        SELECT f.embedding FROM frames f JOIN exports e ON e.frame_id = f.id
        WHERE f.video_id != ? AND f.embedding IS NOT NULL
        """,
        (video_id,),
    ):
        try:
            v = np.frombuffer(r["embedding"], dtype=np.float32).astype(np.float64)
        except Exception:
            continue
        if v.size:
            vecs.append(v)
    if not vecs:
        return np.zeros((0, 0))
    dim = max(set(v.size for v in vecs), key=lambda d: sum(1 for v in vecs if v.size == d))
    return _l2(np.stack([v for v in vecs if v.size == dim]))


def chunk_quotas(chunk_ids: list[int], budgets: dict[int, float], total: int) -> dict[int, int]:
    """Largest-remainder split of `total` over chunks by budget_pct (equal shares when all budgets are 0)."""
    if not chunk_ids or total <= 0:
        return {c: 0 for c in chunk_ids}
    weights = np.array([max(0.0, float(budgets.get(c, 0.0))) for c in chunk_ids])
    if weights.sum() <= 0:
        weights = np.ones(len(chunk_ids))
    exact = weights / weights.sum() * total
    base = np.floor(exact).astype(int)
    rest = total - int(base.sum())
    for i in np.argsort(-(exact - base), kind="stable")[:rest]:
        base[i] += 1
    return {c: int(k) for c, k in zip(chunk_ids, base)}


def _embedding_matrix(blobs: list[Any]) -> np.ndarray:
    dim = 0
    vecs: list[np.ndarray | None] = []
    for b in blobs:
        v = None
        if b:
            try:
                v = np.frombuffer(b, dtype=np.float32).astype(np.float64)
            except Exception:
                v = None
        vecs.append(v)
        if v is not None and v.size > dim:
            dim = v.size
    if dim == 0:
        return np.zeros((len(blobs), 1))
    out = np.zeros((len(blobs), dim))
    for i, v in enumerate(vecs):
        if v is not None and v.size == dim:
            out[i] = v
    return out


def _l2(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms < 1e-9, 1.0, norms)


def select_facility_location(
    E: np.ndarray,
    q: np.ndarray,
    chunk_of: np.ndarray,
    t: np.ndarray,
    target: int,
    floors: dict[int, int],
    caps: dict[int, int],
    locked: np.ndarray,
    excluded: np.ndarray,
    diversity: float = 0.5,
    dup_threshold: float = 0.95,
    E_dup: np.ndarray | None = None,
    axis_members: dict[str, np.ndarray] | None = None,
    axis_cap_n: dict[str, int] | None = None,
) -> tuple[list[int], np.ndarray, dict[str, Any]]:
    """Greedy selection. Returns (selected indices in order, final marginal gain per frame, counters).

    `E` drives coverage, `E_dup` (default `E`) the raw-cosine near-duplicate check. `axis_members[axis]` is a
    boolean membership vector and `axis_cap_n[axis]` the maximum number of picks for it (soft-hard: exceeded
    only when nothing uncapped is eligible).
    """
    n = E.shape[0]
    raw = _l2(E)
    white = _l2(E - E.mean(axis=0, keepdims=True)) if n > 1 else raw
    sim = np.clip(white @ white.T, 0.0, None) ** 2
    same_chunk = chunk_of[:, None] == chunk_of[None, :]
    sim += 0.25 * same_chunk * np.exp(-np.abs(t[:, None] - t[None, :]) / 6.0)
    raw_dup = _l2(E_dup) if E_dup is not None else raw
    cos_raw = raw_dup @ raw_dup.T
    axis_members = {a: np.asarray(m, dtype=bool) for a, m in (axis_members or {}).items() if a in (axis_cap_n or {})}
    axis_cap_n = dict(axis_cap_n or {})
    axis_count = {a: 0 for a in axis_members}
    axis_overflow = {a: 0 for a in axis_members}

    q_rng = float(q.max() - q.min()) if n else 0.0
    q_norm = (q - q.min()) / q_rng if q_rng > 1e-9 else np.ones(n)
    omega = 0.05 + 0.95 * q_norm  # coverage weight: good-looking regions of the pool matter more
    d = min(1.0, max(0.0, float(diversity)))

    selected: list[int] = []
    in_s = np.zeros(n, dtype=bool)
    blocked = excluded.copy()
    best_cover = np.zeros(n)
    count_c: dict[int, int] = {}
    counters: dict[str, Any] = {"locked": 0, "dup_skipped": 0}

    def add(j: int) -> None:
        nonlocal best_cover
        selected.append(j)
        in_s[j] = True
        cid = int(chunk_of[j])
        count_c[cid] = count_c.get(cid, 0) + 1
        best_cover = np.maximum(best_cover, sim[:, j])
        for a, members in axis_members.items():
            if members[j]:
                if axis_count[a] >= axis_cap_n[a]:
                    axis_overflow[a] += 1
                axis_count[a] += 1

    def capped_mask() -> np.ndarray:
        m = np.zeros(n, dtype=bool)
        for a, members in axis_members.items():
            if axis_count[a] >= axis_cap_n[a]:
                m |= members
        return m

    def gains(cols: np.ndarray) -> np.ndarray:
        cov = omega @ np.maximum(0.0, sim[:, cols] - best_cover[:, None])
        top = float(cov.max()) if cov.size else 0.0
        cov_n = cov / top if top > 1e-12 else np.zeros_like(cov)
        return d * cov_n + (1.0 - d) * q_norm[cols]

    for j in np.flatnonzero(locked):
        add(int(j))
        counters["locked"] += 1

    def run_phase(allowed_chunk) -> None:
        while len(selected) < target:
            mask = ~in_s & ~blocked & np.array([allowed_chunk(int(c)) for c in chunk_of], dtype=bool)
            if axis_members:
                uncapped = mask & ~capped_mask()
                if uncapped.any():
                    mask = uncapped
            cols = np.flatnonzero(mask)
            if cols.size == 0:
                return
            g = gains(cols)
            j = int(cols[int(np.argmax(g))])
            if selected and float(cos_raw[j, selected].max()) > dup_threshold:
                blocked[j] = True
                counters["dup_skipped"] += 1
                continue
            add(j)

    # Phase 1: floors per chunk, phase 2: global under caps
    run_phase(lambda c: count_c.get(c, 0) < floors.get(c, 0))
    run_phase(lambda c: count_c.get(c, 0) < caps.get(c, 0))

    final_gain = np.zeros(n)
    rest = np.flatnonzero(~in_s)
    if rest.size:
        final_gain[rest] = gains(rest)
    counters["axis_count"] = axis_count
    counters["axis_overflow"] = axis_overflow
    return selected, final_gain, counters


def reselect_video(
    db: Any,
    video_id: str,
    *,
    target_count: int | None = None,
    per_chunk_floor: int | None = None,
    per_chunk_cap: int | None = None,
    diversity: float = 0.5,
    elasticity: float = 0.2,
    dup_threshold: float = 0.95,
    learned_weights: dict[str, float] | None = None,
    learned_blend: float = 0.5,
    write: bool = True,
    before_write: Any = None,
    axis_caps: dict[str, float] | None = None,
    cross_dataset_dedup: bool = False,
    embed_rows: Any = None,
) -> dict[str, Any]:
    """Re-select picks/runners for a video from stored frames and write the new roles/ranks/states.

    `before_write` (optional callable) runs inside the write transaction, e.g. to re-check the job lock.
    `embed_rows(rows) -> (matrix | None, fallback reason | None)` supplies selection embeddings (DINOv2) aligned
    with the frame rows; None or a failure falls back to the stored CLIP embeddings.
    Returns a report: target, picks, promoted/demoted frame ids, per-chunk quotas and counts.
    """
    caps_share = normalise_axis_caps(axis_caps)
    rows = db.all(
        """
        SELECT f.id, f.chunk_id, f.t, f.embedding, f.zs_scores, f.preview_path, f.full_path, p.role, p.state,
               EXISTS (SELECT 1 FROM exports e WHERE e.frame_id = f.id) AS exported
        FROM frames f JOIN picks p ON p.frame_id = f.id
        WHERE f.video_id = ? AND f.chunk_id IS NOT NULL
        ORDER BY f.chunk_id, f.t
        """,
        (video_id,),
    )
    if not rows:
        raise ReselectError("Video has no extracted frames to select from")

    chunk_rows = db.all("SELECT id, position, budget_pct FROM chunks WHERE video_id=? ORDER BY position", (video_id,))
    budgets = {int(c["id"]): float(c["budget_pct"] or 0.0) for c in chunk_rows}
    chunk_ids = [int(c["id"]) for c in chunk_rows if any(int(r["chunk_id"]) == int(c["id"]) for r in rows)]

    n = len(rows)
    chunk_of = np.array([int(r["chunk_id"]) for r in rows])
    t = np.array([float(r["t"]) for r in rows])
    E_clip = _embedding_matrix([r["embedding"] for r in rows])
    E = E_clip
    embedder, embedder_fallback = "clip", None
    if embed_rows is not None:
        try:
            E_sel, embedder_fallback = embed_rows(rows)
        except Exception as exc:  # model/IO trouble must not break reselect
            E_sel, embedder_fallback = None, str(exc)
        if E_sel is not None and len(E_sel) == n:
            E, embedder = np.asarray(E_sel, dtype=np.float64), "dinov2"
    q = np.zeros(n)
    for i, r in enumerate(rows):
        zs = _zs(r["zs_scores"])
        base = base_composite(zs)
        q[i] = blend_composite(base, components_from_scores(zs), learned_weights, learned_blend) if learned_weights else base
    locked = np.array([(r["state"] == "star") or bool(r["exported"]) for r in rows], dtype=bool)
    excluded = np.array([(r["state"] == "reject") and not locked[i] for i, r in enumerate(rows)], dtype=bool)
    excluded_rejected = int(excluded.sum())

    # Cross-dataset dedup: never pick a near-duplicate of a frame already exported from another video
    cross = {"enabled": bool(cross_dataset_dedup), "reference_frames": 0, "blocked": 0, "blocked_ids": []}
    if cross_dataset_dedup:
        ref = exported_reference(db, video_id)
        cross["reference_frames"] = int(ref.shape[0])
        if ref.size and ref.shape[1] == E_clip.shape[1]:
            best = (_l2(E_clip) @ ref.T).max(axis=1)
            blocked_x = (best >= float(dup_threshold)) & ~locked & ~excluded
            cross["blocked"] = int(blocked_x.sum())
            cross["blocked_ids"] = [int(rows[i]["id"]) for i in np.flatnonzero(blocked_x)]
            excluded = excluded | blocked_x

    current_picks = sum(1 for r in rows if r["role"] == "pick")
    target = int(target_count) if target_count else (current_picks or n)
    if target < 1:
        raise ReselectError("target_count must be at least 1")
    eligible = int((~excluded).sum())
    target = max(int(locked.sum()), min(target, eligible))

    pool = {c: int(((chunk_of == c) & ~excluded).sum()) for c in chunk_ids}
    k_c = chunk_quotas(chunk_ids, budgets, target)
    e = min(1.0, max(0.0, float(elasticity)))
    floors: dict[int, int] = {}
    caps: dict[int, int] = {}
    for c in chunk_ids:
        fl = int(per_chunk_floor) if per_chunk_floor is not None else int(math.floor((1.0 - e) * k_c[c]))
        cp = int(per_chunk_cap) if per_chunk_cap is not None else int(math.ceil((1.0 + e) * k_c[c]))
        if per_chunk_floor is not None and per_chunk_cap is not None and fl > cp:
            raise ReselectError("per_chunk_floor must not exceed per_chunk_cap")
        if per_chunk_floor is None:
            fl = min(fl, cp)  # an explicit cap beats a derived floor
        else:
            cp = max(cp, fl)  # an explicit floor beats a derived cap
        floors[c] = max(0, min(fl, pool[c]))
        caps[c] = cp
    if sum(floors.values()) > target:
        raise ReselectError(
            f"Per-chunk floors add up to {sum(floors.values())} frames, more than the target of {target}"
        )

    axis_members: dict[str, np.ndarray] = {}
    axis_cap_n: dict[str, int] = {}
    if caps_share:
        axes_rows = [frame_axes(_zs(r["zs_scores"])) for r in rows]
        for a, share in caps_share.items():
            axis_members[a] = np.array([ax[a] for ax in axes_rows], dtype=bool)
            axis_cap_n[a] = int(math.floor(share * target + 1e-9))

    selected, final_gain, counters = select_facility_location(
        E, q, chunk_of, t, target, floors, caps, locked, excluded, diversity=diversity, dup_threshold=dup_threshold,
        E_dup=E_clip, axis_members=axis_members, axis_cap_n=axis_cap_n,
    )

    in_s = np.zeros(n, dtype=bool)
    in_s[selected] = True
    pick_rank: dict[int, int] = {}
    per_chunk_pick_count: dict[int, int] = {}
    for j in selected:
        c = int(chunk_of[j])
        pick_rank[j] = per_chunk_pick_count.get(c, 0)
        per_chunk_pick_count[c] = pick_rank[j] + 1
    runner_rank: dict[int, int] = {}
    for c in chunk_ids:
        idx = [i for i in np.flatnonzero((chunk_of == c) & ~in_s)]
        idx.sort(key=lambda i: (bool(excluded[i]), -final_gain[i]))
        for r, i in enumerate(idx):
            runner_rank[int(i)] = r

    updates: list[tuple[str, int, str, int]] = []
    promoted: list[int] = []
    demoted: list[int] = []
    for i, r in enumerate(rows):
        fid = int(r["id"])
        state = r["state"]
        if in_s[i]:
            new_role, rank = "pick", pick_rank[i]
            new_state = state if (locked[i] or (r["role"] == "pick" and state in ("keep", "star"))) else "keep"
            if r["role"] != "pick":
                promoted.append(fid)
        else:
            new_role, rank = "runner_up", runner_rank.get(i, 0)
            new_state = "reject" if state == "reject" else "candidate"
            if r["role"] == "pick":
                demoted.append(fid)
        updates.append((new_role, rank, new_state, fid))

    if write:
        with db.write() as conn:
            if before_write is not None:
                before_write()
            conn.executemany("UPDATE picks SET role=?, rank=?, state=? WHERE frame_id=?", updates)

    return {
        "target_count": target,
        "picks_count": len(selected),
        "shortfall": max(0, target - len(selected)),
        "promoted": promoted,
        "demoted": demoted,
        "locked": counters["locked"],
        "dup_skipped": counters["dup_skipped"],
        "excluded_rejected": excluded_rejected,
        "diversity": min(1.0, max(0.0, float(diversity))),
        "elasticity": e,
        "learned_weights_active": bool(learned_weights),
        "embedder": embedder,
        "embedder_fallback": embedder_fallback if embedder == "clip" and embed_rows is not None else None,
        "axis_caps": {
            a: {
                "max_share": caps_share[a],
                "cap": axis_cap_n[a],
                "picks": int(counters["axis_count"].get(a, 0)),
                "pool": int(axis_members[a].sum()),
                "overflow": int(counters["axis_overflow"].get(a, 0)),
            }
            for a in caps_share
        },
        "cross_dataset": cross,
        "chunks": [
            {
                "chunk_id": c,
                "quota": k_c[c],
                "floor": floors[c],
                "cap": caps[c],
                "pool": pool[c],
                "picks": per_chunk_pick_count.get(c, 0),
            }
            for c in chunk_ids
        ],
    }
