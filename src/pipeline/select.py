"""Diverse Top-K selection using Maximum Marginal Relevance (MMR)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from pipeline.classifier import check_rule


def temporal_similarity(t1: float, t2: float, tau: float = 6.0) -> float:
    """Decaying temporal similarity between two timestamps in seconds."""
    return math.exp(-abs(t1 - t2) / tau)


def select_diverse_top_k(
    candidates: list[dict[str, Any]],
    k: int = 5,
    runner_ups: int = 5,
    rule: str = "person",
    weights: dict | None = None,
    lam: float = 0.7,
    alpha: float = 0.8,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run rule filtering and MMR diverse subset selection.
    
    Returns (picks, runner_ups, rejected_or_unselected).
    """
    if not candidates:
        return [], [], []

    w_c = (weights or {}).get("content", 0.20)
    w_q = 1.0 - w_c  # Weight for Tier-1 composite quality

    # Step 1: Rule filtering
    rule_passed: list[dict[str, Any]] = []
    rule_rejected: list[dict[str, Any]] = []

    for c in candidates:
        zs = c.get("zs_scores") or {}
        if check_rule(rule, zs):
            # Compute composite quality Q_i
            t1_score = c.get("tier1_score", 0.5)
            # Content confidence based on rule
            if rule == "person":
                c_conf = zs.get("person", 0.5)
            elif rule == "equipment":
                c_conf = zs.get("equipment", 0.5)
            elif rule in ("both", "either"):
                c_conf = (zs.get("person", 0.5) + zs.get("equipment", 0.5)) / 2.0
            else:
                c_conf = 1.0 - zs.get("negative", 0.0)

            # Target person identity prioritization
            if "target_person_sim" in zs:
                sim = float(zs.get("target_person_sim", 0.0))
                if zs.get("target_person_matched"):
                    c_conf = min(1.0, c_conf + 0.35 * sim)
                elif zs.get("face_count", 0) > 0 and sim < 0.25:
                    c_conf = max(0.0, c_conf - 0.30)

            c["composite_q"] = round(w_q * t1_score + w_c * c_conf, 4)
            rule_passed.append(c)
        else:
            c["rejected_reason"] = f"Failed content rule: {rule}"
            rule_rejected.append(c)

    # If not enough candidates strictly pass the content rule, fallback to best quality candidates
    if len(rule_passed) < k and rule_rejected:
        for c in rule_rejected:
            t1_score = c.get("tier1_score", 0.5)
            c["composite_q"] = round(w_q * t1_score, 4)
        rule_rejected.sort(key=lambda x: x["composite_q"], reverse=True)
        needed = k - len(rule_passed)
        rule_passed.extend(rule_rejected[:needed])
        rule_rejected = rule_rejected[needed:]

    if not rule_passed:
        return [], [], rule_rejected

    # Sort passed by composite_q
    rule_passed.sort(key=lambda x: x["composite_q"], reverse=True)

    # Step 2: MMR Selection
    picks: list[dict[str, Any]] = []
    selected_indices: set[int] = set()
    n = len(rule_passed)
    target_k = min(k, n)

    # First pick is the highest quality candidate
    picks.append(rule_passed[0])
    selected_indices.add(0)

    # Select remaining K - 1 picks
    while len(picks) < target_k:
        best_score = -float("inf")
        best_idx = -1

        for i in range(n):
            if i in selected_indices:
                continue

            cand = rule_passed[i]
            q_i = cand["composite_q"]
            cand_emb = cand["embedding"]  # normalized vector

            # Maximum similarity to already selected picks
            max_sim = 0.0
            for sel_cand in picks:
                emb_sim = float(np.dot(cand_emb, sel_cand["embedding"]))
                t_sim = temporal_similarity(cand["t"], sel_cand["t"])
                combined_sim = alpha * max(0.0, emb_sim) + (1.0 - alpha) * t_sim
                if combined_sim > max_sim:
                    max_sim = combined_sim

            mmr_score = lam * q_i - (1.0 - lam) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        if best_idx >= 0:
            picks.append(rule_passed[best_idx])
            selected_indices.add(best_idx)
        else:
            break

    # Tag picks
    for rank, p in enumerate(picks):
        p["role"] = "pick"
        p["rank"] = rank
        p["state"] = "keep"

    # Step 3: Runner-ups (next highest composite_q from unselected)
    runner_up_list: list[dict[str, Any]] = []
    unselected: list[dict[str, Any]] = []

    for i in range(n):
        if i not in selected_indices:
            cand = rule_passed[i]
            if len(runner_up_list) < runner_ups:
                cand["role"] = "runner_up"
                cand["rank"] = len(runner_up_list)
                cand["state"] = "candidate"
                runner_up_list.append(cand)
            else:
                cand["role"] = "unselected"
                cand["state"] = "candidate"
                unselected.append(cand)

    return picks, runner_up_list, unselected + rule_rejected
