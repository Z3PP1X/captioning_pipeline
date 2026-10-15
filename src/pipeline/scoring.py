"""Multi-criteria 7-dimension scoring engine and constrained 750 -> 300 frame reduction."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.metrics import compute_tier1_metrics


class ScoringEngine:
    """Scores candidate frames across 7 dimensions and reduces 750 candidates to the best 300."""

    def __init__(self, classifier: ClipClassifier | None = None):
        self.classifier = classifier or ClipClassifier()
        self._prompt_embeddings: dict[str, np.ndarray] = {}

    def _get_dimension_prompts(self) -> dict[str, tuple[list[str], list[str]]]:
        """Returns positive and negative text prompts for each semantic scoring dimension."""
        return {
            "multiple_persons": (
                ["two women interacting", "two people together", "multiple persons in scene", "group of people"],
                ["a single solo person alone", "one person only", "empty room without people"],
            ),
            "one_person": (
                ["a single solo person alone", "one woman alone in frame", "solo portrait of a woman"],
                ["two people together", "multiple women", "empty room without people"],
            ),
            "tools": (
                ["bondage tools", "metal clover clamps", "silicone ball gag", "spreader bar", "posture collar and leash", "handcuffs", "hemp ropes"],
                ["no tools", "plain scene without props", "no equipment"],
            ),
            "equipment": (
                ["bondage apparatus", "suspension rigging frame", "heavy welded steel chains", "bondage furniture", "saint andrew cross frame"],
                ["no equipment", "plain domestic room", "empty floor"],
            ),
            "face": (
                ["clear visible face of a woman looking at camera", "close-up portrait clearly showing eyes and facial features", "sharp visible facial expression"],
                ["back view", "back of head", "faceless framing", "face turned completely away", "face obscured or headless framing"],
            ),
        }

    def _ensure_prompt_vectors(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Pre-embeds positive and negative prompt centroids for all 5 semantic dimensions."""
        if not self._prompt_embeddings:
            prompts_dict = self._get_dimension_prompts()
            all_texts = []
            slices: dict[str, tuple[slice, slice]] = {}
            idx = 0

            for dim, (pos_list, neg_list) in prompts_dict.items():
                p_slice = slice(idx, idx + len(pos_list))
                idx += len(pos_list)
                n_slice = slice(idx, idx + len(neg_list))
                idx += len(neg_list)
                slices[dim] = (p_slice, n_slice)
                all_texts.extend(pos_list)
                all_texts.extend(neg_list)

            raw_embs = self.classifier.embed_texts(all_texts)
            for dim, (p_sl, n_sl) in slices.items():
                p_vec = np.mean(raw_embs[p_sl], axis=0)
                p_vec /= max(1e-6, np.linalg.norm(p_vec))
                n_vec = np.mean(raw_embs[n_sl], axis=0)
                n_vec /= max(1e-6, np.linalg.norm(n_vec))
                self._prompt_embeddings[f"{dim}_pos"] = p_vec
                self._prompt_embeddings[f"{dim}_neg"] = n_vec

        res = {}
        for dim in ("multiple_persons", "one_person", "tools", "equipment", "face"):
            res[dim] = (self._prompt_embeddings[f"{dim}_pos"], self._prompt_embeddings[f"{dim}_neg"])
        return res

    def score_candidates_batch(
        self,
        candidates: list[dict[str, Any]],
        image_embeddings: np.ndarray,
        target_person_embedding: np.ndarray | None = None,
    ) -> list[dict[str, Any]]:
        """Compute the 7 scoring dimensions and optional target person prioritization."""
        if len(candidates) == 0:
            return []

        prompt_vecs = self._ensure_prompt_vectors()

        for idx, cand in enumerate(candidates):
            emb = image_embeddings[idx]  # shape (512,)
            metrics = cand.get("metrics") or compute_tier1_metrics(cand["img_bgr"])

            # 1. Tile Sharpness (normalized: 400+ variance maps to 1.0)
            raw_sharp = metrics.get("tile_sharp", 0.0)
            sharpness_score = round(min(1.0, max(0.0, raw_sharp / 350.0)), 4)

            # 2. Tier-1 Quality (composite of exposure balance, contrast, entropy, clipping)
            luma = metrics.get("mean_luma", 128.0)
            contrast_val = metrics.get("contrast", 0.0)
            if luma < 128.0 and contrast_val >= 35.0:
                # Chiaroscuro adaptation: artistic low-key studio lighting with strong subject contrast
                luma_target = 85.0 if contrast_val >= 45.0 else 110.0
                exp_score = max(0.2, 1.0 - abs(luma - luma_target) / 95.0)
            else:
                exp_score = max(0.0, 1.0 - abs(luma - 128.0) / 115.0)

            contrast_score = min(1.0, contrast_val / 60.0)
            clipping_penalty = max(0.0, 1.0 - metrics.get("clipping", 0.0) * 10.0)
            entropy_score = min(1.0, metrics.get("entropy", 0.0) / 7.5)
            quality_score = round(
                0.35 * exp_score + 0.30 * contrast_score + 0.20 * entropy_score + 0.15 * clipping_penalty,
                4,
            )

            # 3-7. Semantic dimensions via two-probe sigmoid contrast
            scores: dict[str, float] = {}
            for dim, (p_vec, n_vec) in prompt_vecs.items():
                pos_sim = float(np.dot(emb, p_vec))
                neg_sim = float(np.dot(emb, n_vec))
                # Logit difference scaled by temperature 10.0
                logit = (pos_sim - neg_sim) * 10.0
                prob = 1.0 / (1.0 + math.exp(-max(-15.0, min(15.0, logit))))
                scores[dim] = round(prob, 4)

            face_score = scores["face"]
            face_visible_50pct = face_score >= 0.50

            target_sim = float(
                cand.get("target_person_sim")
                or metrics.get("target_person_sim")
                or 0.0
            )
            target_matched = bool(
                cand.get("target_person_matched")
                or metrics.get("target_person_matched")
                or (target_sim >= 0.363)
            )
            face_count = int(
                cand.get("face_count")
                or metrics.get("face_count")
                or (1 if face_visible_50pct else 0)
            )

            # Composite ranking score: prioritize target person when tagged
            if target_person_embedding is not None or target_sim > 0.0:
                is_target = target_sim >= 0.363
                is_off_target_person = (face_count >= 1) and (target_sim < 0.28)

                person_boost = 0.0
                if is_target:
                    person_boost = 0.40 * min(1.0, target_sim / 0.70)
                    if face_count == 1:
                        person_boost += 0.20  # Solo target person bonus
                elif is_off_target_person:
                    person_boost = -0.50  # Deprioritize other people

                comp = (
                    0.25 * sharpness_score
                    + 0.20 * quality_score
                    + 0.15 * max(scores["multiple_persons"], scores["one_person"])
                    + 0.10 * max(scores["tools"], scores["equipment"])
                    + 0.10 * face_score
                    + 0.35 * person_boost
                )
            else:
                comp = (
                    0.35 * sharpness_score
                    + 0.25 * quality_score
                    + 0.18 * max(scores["multiple_persons"], scores["one_person"])
                    + 0.12 * max(scores["tools"], scores["equipment"])
                    + 0.10 * face_score
                )

            scored_dict = {
                "sharpness": sharpness_score,
                "quality": quality_score,
                "multiple_persons": scores["multiple_persons"],
                "one_person": scores["one_person"],
                "tools": scores["tools"],
                "equipment": scores["equipment"],
                "face_score": face_score,
                "face_visible_50pct": face_visible_50pct,
                "target_person_sim": round(target_sim, 4),
                "target_person_matched": target_matched,
                "face_count": face_count,
                "composite": round(comp, 4),
            }

            cand["scores"] = scored_dict
            cand["zs_scores"] = scored_dict
            cand["embedding"] = emb
            cand["metrics"] = metrics

        return candidates

    def compute_distribution_stats(self, frames: list[dict[str, Any]]) -> dict[str, float]:
        """Calculates current category distribution percentages across selected frames."""
        total = len(frames)
        if total == 0:
            return {
                "multiple_pct": 0.0,
                "one_person_pct": 0.0,
                "tools_equipment_pct": 0.0,
                "face_visible_pct": 0.0,
                "target_person_pct": 0.0,
            }

        mult_count = 0
        solo_count = 0
        tool_equip_count = 0
        face_count = 0
        target_count = 0

        for f in frames:
            sc = f.get("scores") or f.get("zs_scores") or {}
            if sc.get("multiple_persons", 0.0) >= 0.45:
                mult_count += 1
            if sc.get("one_person", 0.0) >= 0.45:
                solo_count += 1
            if max(sc.get("tools", 0.0), sc.get("equipment", 0.0)) >= 0.45:
                tool_equip_count += 1
            if sc.get("face_visible_50pct") or sc.get("face_score", 0.0) >= 0.50:
                face_count += 1
            if sc.get("target_person_matched") or sc.get("target_person_sim", 0.0) >= 0.363:
                target_count += 1

        return {
            "multiple_pct": round((mult_count / total) * 100.0, 1),
            "one_person_pct": round((solo_count / total) * 100.0, 1),
            "tools_equipment_pct": round((tool_equip_count / total) * 100.0, 1),
            "face_visible_pct": round((face_count / total) * 100.0, 1),
            "target_person_pct": round((target_count / total) * 100.0, 1),
            "total_frames": total,
        }

    def reduce_750_to_300(
        self,
        candidates: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
        target_total: int = 180,
        category_quotas: dict[str, float] | None = None,
        mmr_lambda: float = 0.65,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
        """Reduces candidate pool (up to 750) down to the best target_total (300) frames.
        
        Constraints:
        - Respects chunk budget percentages: K_i = round(target_total * (budget_pct_i / 100))
        - Balances target quotas:
            - Multiple people: ~50%
            - One person: ~25-30%
            - Tools & equipment: ~20-25%
            - Face >= 50% prioritized
        - High sharpness & quality
        - MMR visual and temporal diversity
        - Redistributes budget shortfall when chunks have fewer frames than quota
        - Ranks runners by quality score
        """
        if not candidates:
            return [], [], {}

        # 1. Determine target frame quota K_i per chunk
        num_chunks = len(chunks)
        chunk_targets: dict[int, int] = {}
        allocated = 0

        for ch in chunks:
            cid = ch["id"]
            bpct = float(ch.get("budget_pct", 100.0 / max(1, num_chunks)))
            k_i = max(1, round(target_total * (bpct / 100.0)))
            chunk_targets[cid] = k_i
            allocated += k_i

        # Adjust any rounding discrepancy on chunk with highest target
        diff = target_total - allocated
        if diff != 0 and chunks:
            highest_cid = max(chunk_targets.keys(), key=lambda c: chunk_targets[c])
            chunk_targets[highest_cid] = max(1, chunk_targets[highest_cid] + diff)

        # 2. Group candidates by chunk
        cands_by_chunk: dict[int, list[dict[str, Any]]] = {}
        for ch in chunks:
            cands_by_chunk[ch["id"]] = []
        for c in candidates:
            cid = c.get("chunk_id")
            if cid in cands_by_chunk:
                cands_by_chunk[cid].append(c)

        # Redistribute budget shortfall (M5): if a chunk pool is smaller than its target,
        # redistribute the deficit to chunks that have surplus candidates.
        shortfall = 0
        for ch in chunks:
            cid = ch["id"]
            pool_len = len(cands_by_chunk[cid])
            if pool_len < chunk_targets[cid]:
                shortfall += chunk_targets[cid] - pool_len
                chunk_targets[cid] = pool_len

        while shortfall > 0:
            surplus_cids = [
                ch["id"] for ch in chunks
                if len(cands_by_chunk[ch["id"]]) > chunk_targets[ch["id"]]
            ]
            if not surplus_cids:
                break
            surplus_cids.sort(
                key=lambda cid: len(cands_by_chunk[cid]) - chunk_targets[cid],
                reverse=True,
            )
            chunk_targets[surplus_cids[0]] += 1
            shortfall -= 1

        all_picks: list[dict[str, Any]] = []
        all_runners: list[dict[str, Any]] = []

        # Target quota counts for global tracking (configurable via category_quotas)
        quotas = category_quotas or {}
        mult_quota = float(quotas.get("multiple_persons", 0.50))
        solo_quota = float(quotas.get("one_person_min", 0.28))
        tools_quota = float(quotas.get("tools_equipment_min", 0.22))

        target_multiple = round(target_total * mult_quota)
        target_solo = round(target_total * solo_quota)
        target_tools_equip = round(target_total * tools_quota)

        curr_multiple = 0
        curr_solo = 0
        curr_tools_equip = 0

        # 3. Stratified MMR Selection per Chunk
        for ch in chunks:
            cid = ch["id"]
            c_pool = cands_by_chunk.get(cid, [])
            target_k = min(len(c_pool), chunk_targets.get(cid, 5))

            if not c_pool:
                continue

            chunk_picks: list[dict[str, Any]] = []
            selected_indices: set[int] = set()

            while len(chunk_picks) < target_k:
                best_score = -float("inf")
                best_idx = -1

                # Quota urgency weights masked by chunk rule
                chunk_rule = ch.get("rule", "person")
                if chunk_rule == "equipment":
                    need_mult = 0.2
                    need_solo = 0.3
                    need_tools = 1.3
                elif chunk_rule == "none":
                    need_mult = 0.5
                    need_solo = 0.5
                    need_tools = 0.5
                else:
                    need_mult = 1.0 if curr_multiple < target_multiple else 0.4
                    need_solo = 1.0 if curr_solo < target_solo else 0.4
                    need_tools = 1.0 if curr_tools_equip < target_tools_equip else 0.4

                for i, cand in enumerate(c_pool):
                    if i in selected_indices:
                        continue

                    sc = cand.get("scores") or {}
                    base_q = cand.get("composite") or sc.get("composite", 0.5)

                    # Dynamic category alignment bonus
                    cat_bonus = 0.0
                    if sc.get("multiple_persons", 0.0) >= 0.45:
                        cat_bonus += 0.25 * need_mult
                    if sc.get("one_person", 0.0) >= 0.45:
                        cat_bonus += 0.20 * need_solo
                    if max(sc.get("tools", 0.0), sc.get("equipment", 0.0)) >= 0.45:
                        cat_bonus += 0.20 * need_tools
                    if sc.get("face_visible_50pct"):
                        cat_bonus += 0.10
                    if sc.get("target_person_matched"):
                        cat_bonus += 0.35 * min(1.0, sc.get("target_person_sim", 0.7) / 0.7)
                    elif sc.get("face_count", 0) >= 1 and sc.get("target_person_sim", 0.0) < 0.28:
                        cat_bonus -= 0.30

                    cand_q = base_q + cat_bonus

                    # MMR diversity penalty against already selected picks in chunk
                    max_sim = 0.0
                    cand_emb = cand["embedding"]
                    cand_t = cand["t"]

                    for sel in chunk_picks:
                        emb_sim = max(0.0, float(np.dot(cand_emb, sel["embedding"])))
                        t_dist = abs(cand_t - sel["t"])
                        t_sim = math.exp(-t_dist / 5.0)
                        combined_sim = 0.75 * emb_sim + 0.25 * t_sim
                        if combined_sim > max_sim:
                            max_sim = combined_sim

                    lam = mmr_lambda
                    mmr_score = lam * cand_q - (1.0 - lam) * max_sim

                    if mmr_score > best_score:
                        best_score = mmr_score
                        best_idx = i

                if best_idx >= 0:
                    chosen = c_pool[best_idx]
                    chunk_picks.append(chosen)
                    selected_indices.add(best_idx)

                    # Update global counters
                    sc = chosen.get("scores") or {}
                    if sc.get("multiple_persons", 0.0) >= 0.45:
                        curr_multiple += 1
                    if sc.get("one_person", 0.0) >= 0.45:
                        curr_solo += 1
                    if max(sc.get("tools", 0.0), sc.get("equipment", 0.0)) >= 0.45:
                        curr_tools_equip += 1
                else:
                    break

            # Mark picks and runners
            for rank, p in enumerate(chunk_picks):
                p["role"] = "pick"
                p["rank"] = rank
                p["state"] = "keep"
                all_picks.append(p)

            # Rank runners by composite quality score descending so p.rank is meaningful (C2/H2)
            unselected_cands = [cand for i, cand in enumerate(c_pool) if i not in selected_indices]
            unselected_cands.sort(
                key=lambda c: (
                    1 if (c.get("scores") or {}).get("target_person_matched") else 0,
                    (c.get("scores") or {}).get("composite", c.get("composite", 0.0)),
                ),
                reverse=True,
            )
            for runner_rank, cand in enumerate(unselected_cands):
                cand["role"] = "runner_up"
                cand["rank"] = runner_rank
                cand["state"] = "candidate"
                all_runners.append(cand)

        # 4. Cross-chunk near-duplicate deduplication pass
        from pipeline.metrics import phash_hamming_distance
        runners_by_chunk: dict[int, list[dict[str, Any]]] = {}
        for r in all_runners:
            runners_by_chunk.setdefault(r["chunk_id"], []).append(r)

        for i in range(len(all_picks)):
            for j in range(i + 1, min(len(all_picks), i + 20)):
                p1 = all_picks[i]
                p2 = all_picks[j]
                if p1.get("chunk_id") == p2.get("chunk_id"):
                    continue

                ph1 = (p1.get("metrics") or {}).get("phash")
                ph2 = (p2.get("metrics") or {}).get("phash")
                is_dupe = False
                if ph1 and ph2 and phash_hamming_distance(ph1, ph2) <= 4:
                    is_dupe = True
                elif float(np.dot(p1["embedding"], p2["embedding"])) >= 0.94:
                    is_dupe = True

                if is_dupe:
                    cid = p2.get("chunk_id")
                    c_runners = runners_by_chunk.get(cid, [])
                    if c_runners:
                        # Pick best ranked runner (C2)
                        replacement = c_runners.pop(0)
                        replacement["role"] = "pick"
                        replacement["rank"] = p2["rank"]
                        replacement["state"] = "keep"

                        p2["role"] = "runner_up"
                        p2["state"] = "candidate"
                        p2["rank"] = len(c_runners)
                        c_runners.append(p2)

                        all_picks[j] = replacement
                        all_runners.append(p2)
                        break

        stats = self.compute_distribution_stats(all_picks)
        return all_picks, all_runners, stats
