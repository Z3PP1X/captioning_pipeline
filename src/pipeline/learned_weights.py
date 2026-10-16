"""Curation event log and per-component quality weights fitted from it (Phase F, review §5.2).

Every human curation action (keep, reject, star, swap, bulk) is stored in `curation_events` with a snapshot of
the frame's score components at that moment. Once enough labelled events exist, an L2-regularised logistic
regression (numpy, Newton/IRLS, standardised features) predicts P(keep) from the components; its positive
coefficients, mapped back to the raw [0, 1] component scale and normalised to sum to 1, become composite
weights. With the user setting `use_learned_weights` the scoring engine blends them into the default composite
(`learned_weights_blend`); the default scoring path is unchanged otherwise.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

CURATION_ACTIONS = ("keep", "reject", "star", "unstar", "swap_in", "swap_out", "bulk_keep", "bulk_reject")
POSITIVE_ACTIONS = frozenset({"keep", "star", "bulk_keep", "swap_in"})
NEGATIVE_ACTIONS = frozenset({"reject", "bulk_reject", "swap_out"})

# The composite's components (ScoringEngine, no-target branch) and their default weights
COMPONENTS = ("sharpness", "quality", "persons", "objects", "face")
DEFAULT_COMPONENT_WEIGHTS = {"sharpness": 0.35, "quality": 0.25, "persons": 0.18, "objects": 0.12, "face": 0.10}
SETTING_KEY = "learned_weights"
DEFAULT_MIN_EVENTS = 50
DEFAULT_L2 = 1.0

# Raw metrics copied into the snapshot for later analysis (not used by the fit)
_METRIC_KEYS = ("tile_sharp", "mean_luma", "clipping", "contrast", "entropy", "motion_energy", "face_count")


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            out = json.loads(value)
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def components_from_scores(zs: dict | None) -> dict[str, float]:
    """The composite components of a frame from its `zs_scores` (missing values count as 0)."""
    zs = zs or {}
    return {
        "sharpness": _num(zs.get("sharpness")),
        "quality": _num(zs.get("quality")),
        "persons": max(_num(zs.get("multiple_persons")), _num(zs.get("one_person"))),
        "objects": max(_num(zs.get("tools")), _num(zs.get("equipment"))),
        "face": _num(zs.get("face_score")),
    }


def base_composite(zs: dict | None) -> float:
    """The composite without any learned blend (what the default scoring path produced)."""
    zs = zs or {}
    return _num(zs.get("composite_base", zs.get("composite", 0.0)))


def blend_composite(base: float, components: dict[str, float], weights: dict[str, float], blend: float) -> float:
    blend = min(1.0, max(0.0, float(blend)))
    learned = sum(float(weights.get(k, 0.0)) * float(components.get(k, 0.0)) for k in COMPONENTS)
    return (1.0 - blend) * float(base) + blend * learned


def snapshot_features(frame: dict) -> dict[str, Any]:
    """JSON-ready snapshot of a frame's metric components (frames row joined with picks role/state)."""
    zs = _as_dict(frame.get("zs_scores"))
    metrics = _as_dict(frame.get("metrics"))
    snap: dict[str, Any] = {k: round(v, 4) for k, v in components_from_scores(zs).items()}
    snap["composite"] = round(_num(zs.get("composite")), 4)
    snap["target_person_sim"] = round(_num(zs.get("target_person_sim")), 4)
    for key in _METRIC_KEYS:
        if key in metrics:
            snap[key] = metrics[key]
    # Role/state when the event was logged (endpoints log right after their write, so: after the action)
    snap["role"] = frame.get("role")
    snap["state"] = frame.get("state")
    snap["t"] = frame.get("t")
    return snap


# ----------------------------------------------------------------------------- Logging
def log_curation_events(db: Any, entries: list[tuple[int, str]]) -> int:
    """Insert one event per (frame_id, action). Never raises: curation must not fail because of logging.

    Call it outside any `db.write()` block (the write lock is not re-entrant). The snapshot reads the frame's
    scores and its current role/state.
    """
    entries = [(int(fid), a) for fid, a in entries if fid is not None and a in CURATION_ACTIONS]
    if not entries:
        return 0
    try:
        ids = sorted({fid for fid, _ in entries})
        rows: dict[int, dict] = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            ph = ",".join("?" for _ in part)
            for r in db.all(
                f"""
                SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.zs_scores, p.role, p.state
                FROM frames f LEFT JOIN picks p ON p.frame_id = f.id
                WHERE f.id IN ({ph})
                """,
                tuple(part),
            ):
                rows[int(r["id"])] = r
        now = time.time()
        with db.write() as conn:
            for fid, action in entries:
                fr = rows.get(fid)
                if not fr:
                    continue
                conn.execute(
                    "INSERT INTO curation_events (video_id, frame_id, chunk_id, action, features, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (fr["video_id"], fid, fr["chunk_id"], action, json.dumps(snapshot_features(fr)), now),
                )
        return len(entries)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("curation event logging failed: %s", exc)
        return 0


def state_action(old_state: str | None, new_state: str | None) -> str | None:
    """Curation action for a pick state change (None = nothing worth logging)."""
    if new_state == old_state:
        return None
    if new_state in ("keep", "reject", "star"):
        return new_state
    if old_state == "star":
        return "unstar"
    return None


# ----------------------------------------------------------------------------- Training data
def label_for(action: str) -> int | None:
    if action in POSITIVE_ACTIONS:
        return 1
    if action in NEGATIVE_ACTIONS:
        return 0
    return None


def training_set(db: Any) -> tuple[np.ndarray, np.ndarray]:
    """(X, y) from curation_events: the latest labelled event per frame wins (a keep then reject = reject)."""
    latest: dict[int, tuple[str, dict]] = {}
    orphans: list[tuple[str, dict]] = []
    for r in db.all("SELECT frame_id, action, features FROM curation_events ORDER BY id ASC"):
        entry = (r["action"], _as_dict(r["features"]))
        if r["frame_id"] is None:
            orphans.append(entry)
        else:
            latest[int(r["frame_id"])] = entry
    xs: list[list[float]] = []
    ys: list[int] = []
    for action, feats in list(latest.values()) + orphans:
        y = label_for(action)
        if y is None:
            continue
        xs.append([_num(feats.get(k)) for k in COMPONENTS])
        ys.append(y)
    if not xs:
        return np.zeros((0, len(COMPONENTS))), np.zeros(0)
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def count_labelled_events(db: Any) -> int:
    return int(training_set(db)[1].size)


# ----------------------------------------------------------------------------- Fit
def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = DEFAULT_L2, max_iter: int = 100) -> dict[str, Any]:
    """L2 logistic regression on standardised features (Newton/IRLS; the intercept is not penalised)."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    Z = (X - mean) / std
    Zb = np.hstack([np.ones((Z.shape[0], 1)), Z])
    reg = np.eye(Zb.shape[1]) * l2
    reg[0, 0] = 0.0
    w = np.zeros(Zb.shape[1])
    for _ in range(max_iter):
        p = _sigmoid(Zb @ w)
        grad = Zb.T @ (p - y) + reg @ w
        hess = (Zb * (p * (1.0 - p))[:, None]).T @ Zb + reg + np.eye(Zb.shape[1]) * 1e-9
        step = np.linalg.solve(hess, grad)
        w -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    p = _sigmoid(Zb @ w)
    return {"intercept": float(w[0]), "coef": w[1:], "mean": mean, "std": std, "p": p}


def _auc(y: np.ndarray, score: np.ndarray) -> float | None:
    pos, neg = score[y == 1], score[y == 0]
    if pos.size == 0 or neg.size == 0:
        return None
    # Mann-Whitney U with ties counted half
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    allv = np.concatenate([pos, neg])[order]
    ranks = np.empty(allv.size)
    i = 0
    while i < allv.size:
        j = i
        while j + 1 < allv.size and allv[j + 1] == allv[i]:
            j += 1
        ranks[i:j + 1] = (i + j) / 2.0 + 1.0
        i = j + 1
    rank_of = np.empty(allv.size)
    rank_of[order] = ranks
    r_pos = rank_of[: pos.size].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size))


class NotEnoughEvents(ValueError):
    """Raised when there are too few (or one-class) labelled curation events to fit."""


def fit_learned_weights(db: Any, min_events: int = DEFAULT_MIN_EVENTS, l2: float = DEFAULT_L2) -> dict[str, Any]:
    """Fit, store (settings key `learned_weights`) and return the model."""
    X, y = training_set(db)
    n = int(y.size)
    if n < min_events:
        raise NotEnoughEvents(f"Need at least {min_events} labelled curation events to fit, have {n}")
    n_pos = int(y.sum())
    if n_pos == 0 or n_pos == n:
        raise NotEnoughEvents("Curation events must contain both kept and rejected frames to fit")
    fit = fit_logistic(X, y, l2=l2)
    coef = fit["coef"]
    raw = coef / fit["std"]
    positive = np.clip(raw, 0.0, None)
    total = float(positive.sum())
    weights = {k: round(float(v / total), 4) for k, v in zip(COMPONENTS, positive)} if total > 1e-12 else None
    accuracy = float(((fit["p"] >= 0.5).astype(float) == y).mean())
    model = {
        "version": 1,
        "features": list(COMPONENTS),
        "weights": weights,
        "coefficients": {k: round(float(v), 4) for k, v in zip(COMPONENTS, coef)},
        "raw_coefficients": {k: round(float(v), 4) for k, v in zip(COMPONENTS, raw)},
        "intercept": round(fit["intercept"], 4),
        "mean": {k: round(float(v), 4) for k, v in zip(COMPONENTS, fit["mean"])},
        "std": {k: round(float(v), 4) for k, v in zip(COMPONENTS, fit["std"])},
        "n_events": n,
        "n_positive": n_pos,
        "n_negative": n - n_pos,
        "accuracy": round(accuracy, 4),
        "auc": (round(a, 4) if (a := _auc(y, fit["p"])) is not None else None),
        "l2": l2,
        "fitted_at": time.time(),
    }
    db.set_setting(SETTING_KEY, model)
    return model


# ----------------------------------------------------------------------------- Use
def stored_model(db: Any) -> dict[str, Any] | None:
    model = db.get_setting(SETTING_KEY, None)
    return model if isinstance(model, dict) else None


def active_learned_weights(db: Any, user_settings: dict) -> dict[str, float] | None:
    """The weights to blend in, or None (setting off, never fitted, too few events or no positive weight)."""
    if not user_settings.get("use_learned_weights"):
        return None
    model = stored_model(db)
    if not model or not model.get("weights"):
        return None
    if int(model.get("n_events", 0)) < int(user_settings.get("learned_weights_min_events", DEFAULT_MIN_EVENTS)):
        return None
    return {k: float(model["weights"].get(k, 0.0)) for k in COMPONENTS}


def learned_status(db: Any, user_settings: dict) -> dict[str, Any]:
    model = stored_model(db)
    min_events = int(user_settings.get("learned_weights_min_events", DEFAULT_MIN_EVENTS))
    n_now = count_labelled_events(db)
    return {
        "weights": (model or {}).get("weights"),
        "default_weights": dict(DEFAULT_COMPONENT_WEIGHTS),
        "coefficients": (model or {}).get("coefficients"),
        "intercept": (model or {}).get("intercept"),
        "n_events": n_now,
        "n_events_at_fit": (model or {}).get("n_events"),
        "n_positive": (model or {}).get("n_positive"),
        "n_negative": (model or {}).get("n_negative"),
        "accuracy": (model or {}).get("accuracy"),
        "auc": (model or {}).get("auc"),
        "fitted_at": (model or {}).get("fitted_at"),
        "fitted": model is not None,
        "min_events": min_events,
        "can_fit": n_now >= min_events,
        "use_learned_weights": bool(user_settings.get("use_learned_weights")),
        "blend": float(user_settings.get("learned_weights_blend", 0.5)),
        "active": active_learned_weights(db, user_settings) is not None,
        "features": list(COMPONENTS),
    }
