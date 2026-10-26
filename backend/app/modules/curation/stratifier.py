"""Rule-Based & Plugin-Ready Dataset Stratifier (`RuleBasedStratifier`).

Implements `DatasetStratifier` (Module B.3):
- Computes histogram distributions across aspect ratio, brightness key, color temperature,
  video timeline deciles [0.0, 0.1, ..., 1.0], quality score quartiles, and TagCategory coverage.
- Detects statistical quality outliers, over-represented buckets, and coverage gaps.
- Selects an optimal diverse subset of target size K using a submodular / Maximal Marginal
  Relevance (MMR) objective balancing composite quality Q with bucket inverse-frequency,
  tag diversity, temporal spread, and 64-bit pHash Hamming distance.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from backend.app.models.domain import Tag
from backend.app.modules.curation.interfaces import DatasetStratifier


def _item_aspect_bucket(item: dict[str, Any]) -> str:
    ar = float(item.get("aspect_ratio") or 1.0)
    if ar < 0.80:
        return "portrait_tall"
    if ar > 1.20:
        return "landscape_wide"
    return "square"


def _item_brightness_bucket(item: dict[str, Any], tags: list[Tag]) -> str:
    slugs = {t.slug for t in tags}
    if "chiaroscuro_high_contrast" in slugs:
        return "chiaroscuro"
    if "low_key_dark" in slugs:
        return "low_key"
    if "high_key_bright" in slugs:
        return "high_key"

    luma = float(item.get("exposure_mean_luma", 128.0))
    contrast = float(item.get("contrast_rms", 40.0))
    if luma < 115.0 and contrast >= 45.0:
        return "chiaroscuro"
    if luma < 75.0:
        return "low_key"
    if luma > 180.0:
        return "high_key"
    return "balanced_midtone"


def _item_color_temp_bucket(item: dict[str, Any], tags: list[Tag]) -> str:
    slugs = {t.slug for t in tags}
    if "monochrome_desaturated" in slugs:
        return "monochrome"
    if "warm_tungsten_cast" in slugs:
        return "warm"
    if "cool_daylight_cast" in slugs:
        return "cool"
    if "neutral_color_balance" in slugs:
        return "neutral"

    # Fallback: inspect 24-bin HSV histogram if present
    hsv_blob = item.get("hsv_hist")
    if hsv_blob is not None:
        arr = (
            np.frombuffer(hsv_blob, dtype=np.float32)
            if isinstance(hsv_blob, (bytes, bytearray, memoryview))
            else np.asarray(hsv_blob, dtype=np.float32)
        )
        if arr.size == 24:
            # First 8 bins are Hue [0..180), next 8 are Saturation
            s_bins = arr[8:16]
            if float(s_bins[:2].sum()) > 0.80 * float(s_bins.sum() + 1e-6):
                return "monochrome"
            h_bins = arr[:8]
            warm_energy = float(h_bins[0] + h_bins[1] + h_bins[7])
            cool_energy = float(h_bins[3] + h_bins[4] + h_bins[5])
            if warm_energy > cool_energy * 1.35:
                return "warm"
            if cool_energy > warm_energy * 1.35:
                return "cool"
    return "neutral"


class RuleBasedStratifier(DatasetStratifier):
    """Non-ML dataset distribution analyzer, outlier detector, and diverse subset selector."""

    def compute_distribution(
        self,
        media_items: list[dict[str, Any]],
        tags_by_item: dict[int, list[Tag]] | None = None,
    ) -> dict[str, Any]:
        tags_map = tags_by_item or {}
        total = len(media_items)
        if total == 0:
            return {
                "total_items": 0,
                "aspect_ratio_buckets": {"portrait_tall": 0, "square": 0, "landscape_wide": 0},
                "brightness_buckets": {
                    "low_key": 0,
                    "chiaroscuro": 0,
                    "balanced_midtone": 0,
                    "high_key": 0,
                },
                "color_temp_buckets": {"warm": 0, "neutral": 0, "cool": 0, "monochrome": 0},
                "temporal_deciles": [0] * 10,
                "quality_quartiles": {"q1_0_25": 0, "q2_25_50": 0, "q3_50_75": 0, "q4_75_100": 0},
                "tag_category_coverage": {},
                "outlier_ids": [],
            }

        aspect_counts: Counter[str] = Counter({"portrait_tall": 0, "square": 0, "landscape_wide": 0})
        brightness_counts: Counter[str] = Counter(
            {"low_key": 0, "chiaroscuro": 0, "balanced_midtone": 0, "high_key": 0}
        )
        color_counts: Counter[str] = Counter({"warm": 0, "neutral": 0, "cool": 0, "monochrome": 0})
        quality_quartiles: Counter[str] = Counter(
            {"q1_0_25": 0, "q2_25_50": 0, "q3_50_75": 0, "q4_75_100": 0}
        )
        tag_cat_counts: dict[str, Counter[str]] = defaultdict(Counter)

        timestamps = [
            float(m["timestamp_s"])
            for m in media_items
            if m.get("timestamp_s") is not None
        ]
        min_ts = min(timestamps) if timestamps else 0.0
        max_ts = max(timestamps) if timestamps else 0.0
        span_ts = max(1e-6, max_ts - min_ts)
        temporal_deciles = [0] * 10

        q_scores: list[float] = []
        for item in media_items:
            item_id = int(item.get("id", item.get("item_id", 0)))
            it_tags = tags_map.get(item_id) or item.get("tags") or []
            norm_tags = [
                t if isinstance(t, Tag) else Tag(**t)
                for t in it_tags
                if isinstance(t, (Tag, dict))
            ]

            aspect_counts[_item_aspect_bucket(item)] += 1
            brightness_counts[_item_brightness_bucket(item, norm_tags)] += 1
            color_counts[_item_color_temp_bucket(item, norm_tags)] += 1

            q = float(item.get("quality_score", 0.5))
            q_scores.append(q)
            if q < 0.25:
                quality_quartiles["q1_0_25"] += 1
            elif q < 0.50:
                quality_quartiles["q2_25_50"] += 1
            elif q < 0.75:
                quality_quartiles["q3_50_75"] += 1
            else:
                quality_quartiles["q4_75_100"] += 1

            if item.get("timestamp_s") is not None:
                rel = (float(item["timestamp_s"]) - min_ts) / span_ts
                decile = min(9, max(0, int(rel * 10.0)))
                temporal_deciles[decile] += 1

            for t in norm_tags:
                tag_cat_counts[t.category][t.slug] += 1

        # Statistical outlier detection (low quality z-score < -2.0 or auto_rejected)
        q_arr = np.asarray(q_scores, dtype=np.float64)
        q_mean = float(q_arr.mean())
        q_std = float(q_arr.std())
        q_cutoff = max(0.20, q_mean - 2.0 * q_std) if q_std > 1e-4 else 0.20

        outlier_ids: list[int] = []
        for item in media_items:
            item_id = int(item.get("id", item.get("item_id", 0)))
            q = float(item.get("quality_score", 0.5))
            state = str(item.get("curation_state", "candidate"))
            if state == "auto_rejected" or q < q_cutoff:
                outlier_ids.append(item_id)

        tag_coverage = {
            cat: {
                "total_assignments": int(sum(counter.values())),
                "unique_tags": len(counter),
                "top_tags": dict(counter.most_common(10)),
            }
            for cat, counter in tag_cat_counts.items()
        }

        return {
            "total_items": total,
            "aspect_ratio_buckets": dict(aspect_counts),
            "brightness_buckets": dict(brightness_counts),
            "color_temp_buckets": dict(color_counts),
            "temporal_deciles": temporal_deciles,
            "quality_quartiles": dict(quality_quartiles),
            "quality_mean": round(q_mean, 4),
            "quality_std": round(q_std, 4),
            "tag_category_coverage": tag_coverage,
            "outlier_ids": outlier_ids,
        }

    def detect_outliers_and_gaps(
        self,
        distribution: dict[str, Any],
        target_size: int,
    ) -> dict[str, Any]:
        total = max(1, int(distribution.get("total_items", 0)))
        recommendations: list[str] = []
        over_represented: list[str] = []
        under_represented: list[str] = []

        for bucket_group in ("aspect_ratio_buckets", "brightness_buckets", "color_temp_buckets"):
            buckets = distribution.get(bucket_group, {})
            for name, count in buckets.items():
                share = float(count) / float(total)
                if share > 0.70 and total >= 10:
                    over_represented.append(f"{bucket_group}:{name} ({share * 100:.0f}%)")
                    recommendations.append(
                        f"Bucket '{name}' dominates {bucket_group} ({share * 100:.0f}%). "
                        f"Consider increasing diversity_weight during stratification."
                    )

        deciles = distribution.get("temporal_deciles", [])
        if sum(deciles) >= 10:
            empty_deciles = [i for i, c in enumerate(deciles) if c == 0]
            if len(empty_deciles) >= 3:
                under_represented.append(f"temporal_deciles:{empty_deciles}")
                recommendations.append(
                    f"Video timeline has {len(empty_deciles)} empty deciles {empty_deciles}; "
                    f"consider interval sampling to cover missing scenes."
                )

        if total < target_size:
            recommendations.append(
                f"Available items ({total}) are below target_size ({target_size}). "
                f"Lower scene_threshold or dedup_hamming_threshold to retain more candidates."
            )

        outlier_ids = list(distribution.get("outlier_ids", []))
        if outlier_ids:
            recommendations.append(
                f"Detected {len(outlier_ids)} quality/gate outliers eligible for automatic demotion."
            )

        return {
            "target_size": target_size,
            "total_available": int(distribution.get("total_items", 0)),
            "over_represented": over_represented,
            "under_represented": under_represented,
            "outlier_ids": outlier_ids,
            "recommendations": recommendations,
        }

    def stratify_selection(
        self,
        media_items: list[dict[str, Any]],
        tags_by_item: dict[int, list[Tag]] | None = None,
        target_count: int = 150,
        diversity_weight: float = 0.5,
    ) -> dict[str, list[int]]:
        """Select optimal diverse subset of size `target_count` via greedy MMR + bucket balancing."""
        tags_map = tags_by_item or {}
        distribution = self.compute_distribution(media_items, tags_map)
        outlier_set = set(distribution.get("outlier_ids", []))

        if not media_items or target_count <= 0:
            all_ids = [int(m.get("id", m.get("item_id", i))) for i, m in enumerate(media_items)]
            return {
                "recommended_keep_ids": [],
                "recommended_demote_ids": all_ids,
                "outlier_ids": sorted(outlier_set),
            }

        # Prepare candidate features
        timestamps = [
            float(m["timestamp_s"])
            for m in media_items
            if m.get("timestamp_s") is not None
        ]
        min_ts = min(timestamps) if timestamps else 0.0
        span_ts = max(1e-6, (max(timestamps) - min_ts) if timestamps else 1.0)

        candidates: list[dict[str, Any]] = []
        demoted_ids: list[int] = []

        for idx, item in enumerate(media_items):
            item_id = int(item.get("id", item.get("item_id", idx)))
            state = str(item.get("curation_state", "candidate"))
            is_rep = bool(item.get("is_cluster_representative", True))

            if state in ("auto_rejected", "reject", "duplicate_hidden") or not is_rep:
                demoted_ids.append(item_id)
                continue

            it_tags = tags_map.get(item_id) or item.get("tags") or []
            norm_tags = [
                t if isinstance(t, Tag) else Tag(**t)
                for t in it_tags
                if isinstance(t, (Tag, dict))
            ]

            phash_str = item.get("phash") or "0000000000000000"
            try:
                phash_u64 = int(phash_str, 16)
            except ValueError:
                phash_u64 = 0

            decile = 0
            if item.get("timestamp_s") is not None:
                decile = min(9, max(0, int(((float(item["timestamp_s"]) - min_ts) / span_ts) * 10.0)))

            candidates.append(
                {
                    "item_id": item_id,
                    "quality_score": float(item.get("quality_score", 0.5)),
                    "is_starred": state == "star",
                    "aspect_bucket": _item_aspect_bucket(item),
                    "brightness_bucket": _item_brightness_bucket(item, norm_tags),
                    "color_bucket": _item_color_temp_bucket(item, norm_tags),
                    "decile": decile,
                    "tag_slugs": {t.slug for t in norm_tags},
                    "phash_u64": phash_u64,
                }
            )

        if len(candidates) <= target_count:
            keep_ids = [c["item_id"] for c in candidates]
            return {
                "recommended_keep_ids": keep_ids,
                "recommended_demote_ids": demoted_ids,
                "outlier_ids": sorted(outlier_set),
            }

        lam = float(np.clip(diversity_weight, 0.0, 1.0))
        selected: list[dict[str, Any]] = []
        remaining = list(candidates)

        # Bucket & tag counters among already-selected items
        sel_aspect: Counter[str] = Counter()
        sel_bright: Counter[str] = Counter()
        sel_color: Counter[str] = Counter()
        sel_decile: Counter[int] = Counter()
        sel_tags: Counter[str] = Counter()

        # Always include explicitly starred items first (up to target_count)
        starred = [c for c in remaining if c["is_starred"]]
        starred.sort(key=lambda c: c["quality_score"], reverse=True)
        for c in starred[:target_count]:
            selected.append(c)
            remaining.remove(c)
            sel_aspect[c["aspect_bucket"]] += 1
            sel_bright[c["brightness_bucket"]] += 1
            sel_color[c["color_bucket"]] += 1
            sel_decile[c["decile"]] += 1
            for slug in c["tag_slugs"]:
                sel_tags[slug] += 1

        # Seed with highest quality item if none starred
        if not selected and remaining:
            best_first = max(remaining, key=lambda c: c["quality_score"])
            selected.append(best_first)
            remaining.remove(best_first)
            sel_aspect[best_first["aspect_bucket"]] += 1
            sel_bright[best_first["brightness_bucket"]] += 1
            sel_color[best_first["color_bucket"]] += 1
            sel_decile[best_first["decile"]] += 1
            for slug in best_first["tag_slugs"]:
                sel_tags[slug] += 1

        # Track minimum Hamming distance from each remaining candidate to the selected set
        min_hamming = np.full(len(remaining), 64, dtype=np.int32)
        for s in selected:
            sh = s["phash_u64"]
            for i, rem in enumerate(remaining):
                d = (rem["phash_u64"] ^ sh).bit_count()
                if d < min_hamming[i]:
                    min_hamming[i] = d

        while len(selected) < target_count and remaining:
            n_sel = float(len(selected))
            best_idx = 0
            best_obj = -float("inf")

            for i, cand in enumerate(remaining):
                q_term = cand["quality_score"]

                # Inverse bucket frequency reward in [0, 1]
                b_aspect = 1.0 / (1.0 + sel_aspect[cand["aspect_bucket"]] / max(1.0, n_sel / 3.0))
                b_bright = 1.0 / (1.0 + sel_bright[cand["brightness_bucket"]] / max(1.0, n_sel / 4.0))
                b_color = 1.0 / (1.0 + sel_color[cand["color_bucket"]] / max(1.0, n_sel / 4.0))
                b_decile = 1.0 / (1.0 + sel_decile[cand["decile"]] / max(1.0, n_sel / 10.0))
                bucket_gain = 0.25 * (b_aspect + b_bright + b_color + b_decile)

                # Tag novelty gain in [0, 1]
                if cand["tag_slugs"]:
                    tag_gain = float(
                        sum(1.0 / (1.0 + sel_tags[s]) for s in cand["tag_slugs"])
                    ) / float(len(cand["tag_slugs"]))
                else:
                    tag_gain = 0.5

                # Perceptual hash visual distance in [0, 1]
                phash_div = float(min_hamming[i]) / 64.0

                div_term = 0.45 * phash_div + 0.35 * bucket_gain + 0.20 * tag_gain
                score = (1.0 - lam) * q_term + lam * div_term
                if cand["item_id"] in outlier_set:
                    score -= 0.35

                if score > best_obj:
                    best_obj = score
                    best_idx = i

            chosen = remaining.pop(best_idx)
            min_hamming = np.delete(min_hamming, best_idx)
            selected.append(chosen)

            sel_aspect[chosen["aspect_bucket"]] += 1
            sel_bright[chosen["brightness_bucket"]] += 1
            sel_color[chosen["color_bucket"]] += 1
            sel_decile[chosen["decile"]] += 1
            for slug in chosen["tag_slugs"]:
                sel_tags[slug] += 1

            ch_hash = chosen["phash_u64"]
            for i, rem in enumerate(remaining):
                d = (rem["phash_u64"] ^ ch_hash).bit_count()
                if d < min_hamming[i]:
                    min_hamming[i] = d

        keep_ids = [c["item_id"] for c in selected]
        demoted_ids.extend(c["item_id"] for c in remaining)

        return {
            "recommended_keep_ids": keep_ids,
            "recommended_demote_ids": demoted_ids,
            "outlier_ids": sorted(outlier_set),
        }
