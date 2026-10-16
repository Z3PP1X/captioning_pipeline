"""Central configuration: filesystem paths (env-overridable) and default user settings."""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
VIDEO_STATUSES = (
    "new", "chunked", "extracting_750", "curating_300", "captioning",
    "exported", "skipped", "ignored", "extracting", "curating", "broken"
)
CHUNK_RULES = ("person", "equipment", "both", "either", "none")


class Settings(BaseSettings):
    """Filesystem layout. Override via CP_* environment variables (see scripts/env.sh)."""

    model_config = SettingsConfigDict(env_prefix="CP_")

    video_dir: Path = Path("/mnt/c/Users/Patri/Videos/_girls")
    cache_dir: Path = Path.home() / ".cache" / "captioning_pipeline"
    finished_dir: Path = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished")
    min_duration_s: float = 30.0

    @property
    def project_root(self) -> Path:
        return self.finished_dir.parent.parent

    @property
    def curated_dir(self) -> Path:
        return self.project_root / "data" / "curated_2500"

    @property
    def openrouter_api_key(self) -> str:
        import os
        return os.environ.get("OPENROUTER_API_KEY", "")

    @property
    def db_path(self) -> Path:
        return self.cache_dir / "state.db"

    @property
    def filmstrip_dir(self) -> Path:
        return self.cache_dir / "filmstrips"

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.filmstrip_dir, self.finished_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# User-tunable pipeline settings, stored in SQLite and editable in the UI.
DEFAULT_USER_SETTINGS: dict = {
    "default_k": 5,
    "default_rule": "person",
    "snap_to_cuts": True,
    "snap_window_s": 1.5,
    "suggest_min_len_s": 4.0,
    "sample_fps": 4.0,
    "survivor_frac": 0.5,
    "weights": {"sharpness": 0.40, "exposure": 0.25, "detail": 0.15, "content": 0.20},
    "mmr_lambda": 0.7,
    "mmr_alpha": 0.8,
    "runner_ups": 5,
    "max_edge": 2048,
    "min_export_edge": 0,
    "trigger_word": "restrained_elegance",
    "clip_model": "Xenova/clip-vit-base-patch32:quantized",
    # Candidate extraction & curated reduction (target 150 - 200 images per video)
    "target_candidates": 750,
    "target_curated": 180,
    "target_curated_min": 150,
    "target_curated_max": 200,
    "similarity_dedup_threshold": 0.95,
    "video_hash_prefix": "v_",
    "video_hash_length": 8,
    # Learn-as-you-tag image tagging: minimum score for a suggestion, and pre-tagging after each extraction
    "tag_suggest_min_score": 0.5,
    "auto_suggest_tags": True,
    # Phase F (extraction / quality / selection v2)
    # Drop a raw sample whose dHash is within N bits of the last kept one, before metrics/faces/CLIP (0 = off)
    "dhash_dedup_max_distance": 4,
    # Keep the sharpest/stillest of `window_peak_oversample` sub-samples per sampling window
    "window_peak_sampling": True,
    "window_peak_oversample": 3,
    # Blend per-component quality weights fitted from curation_events into the composite score
    "use_learned_weights": False,
    "learned_weights_blend": 0.5,
    "learned_weights_min_events": 50,
    # Defaults of POST /api/videos/{id}/reselect (diversity 0 = pure quality, 1 = pure coverage)
    "reselect_diversity": 0.5,
    "reselect_elasticity": 0.2,
    # Phase F2: max share of the picks per category axis (multi_person, solo, equipment, face; {} = no caps)
    # and skipping candidates that near-duplicate frames already exported from other videos
    "reselect_axis_caps": {},
    "reselect_cross_dataset_dedup": False,
    # Reuse the per-video analysis cache (<cache>/analysis/<video>.npz: decoded samples, signals, embeddings)
    # instead of decoding again on re-extraction; invalidated when the video's size or mtime changes
    "reuse_analysis_cache": True,
    # Filmstrip cuts[] / autochunk: 'content' = frame-accurate HSV content detector, 'legacy' = keyframe histograms
    "cut_detector": "content",
    "cut_adaptive_threshold": 3.0,
    "cut_min_content_val": 15.0,
    "cut_min_scene_s": 0.5,
    "cut_fade_threshold": 12.0,
    # Quality v2: hard gates, then per-video percentile-normalised components feeding the composite
    "quality_v2": True,
    # Optional IQA component (quality v2 only): 'off', 'clip_iqa' (zero-shot, existing CLIP) or
    # 'topiq_nr' (TOPIQ-NR KonIQ ONNX, ~177 MB download from Hugging Face, CC BY-NC-SA 4.0)
    "iqa_model": "off",
    # Embeddings used by reselect for coverage/diversity: 'clip' or 'dinov2' (DINOv2-S ONNX, falls back to CLIP)
    "selection_embedder": "clip",
    "category_quotas": {
        "multiple_persons": 0.50,
        "one_person_min": 0.25,
        "one_person_max": 0.30,
        "tools_equipment_min": 0.20,
        "tools_equipment_max": 0.25,
    },
    # OpenRouter API settings targeting google/gemma-4-31b-it with cascade fallback to deepseek and grok.
    # Reasoning is off: it shares max_tokens with the caption and truncated it.
    "openrouter_api_key": "",
    "openrouter_model": "google/gemma-4-31b-it",
    "openrouter_fallback_layer2": "deepseek/deepseek-v4.1-flash",
    "openrouter_fallback_layer3": "x-ai/grok-4.7",
    "openrouter_reasoning_effort": "none",
    "openrouter_max_tokens": 600,
    # Captions want consistency: 0.3-0.5 (review §7.2)
    "openrouter_temperature": 0.4,
    "openrouter_top_p": 0.90,
    "openrouter_mode": "concept",  # 'concept', 'character', or 'style'
    "openrouter_trigger": "restrained_elegance",
    "openrouter_class_noun": "woman",
    "openrouter_max_dim": 512,
    "openrouter_concurrency": 4,
    # R6: after a caption job, embed only the new/changed captions into the RAG index (background)
    "rag_auto_reindex": True,
    # R3: embed the indexed captions' images (SigLIP, else CLIP) for "more like this frame"
    "rag_image_index": True,
    "prompts": {
        "person": ["a photo of a woman", "a photo of a person", "a photo of a man"],
        "equipment": ["rope bondage", "metal chains", "handcuffs", "a metal frame",
                      "a spreader bar", "a suspension rig", "leather restraints"],
        "negative": ["an empty room", "a black screen", "a title card with text", "a blurry photo"],
    },
}


def merged_user_settings(stored: dict | None) -> dict:
    """Deep-merge stored overrides onto defaults (one level of nesting)."""
    out = copy.deepcopy(DEFAULT_USER_SETTINGS)
    for k, v in (stored or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


# Numeric bounds for user settings (inclusive). Keys without an entry only get the type check.
SETTING_RANGES: dict[str, tuple[float, float]] = {
    "default_k": (1, 100),
    "snap_window_s": (0.0, 10.0),
    "suggest_min_len_s": (0.5, 600.0),
    "sample_fps": (0.1, 60.0),
    "survivor_frac": (0.01, 1.0),
    "mmr_lambda": (0.0, 1.0),
    "mmr_alpha": (0.0, 1.0),
    "runner_ups": (0, 100),
    "max_edge": (256, 8192),
    "min_export_edge": (0, 8192),
    "target_candidates": (10, 5000),
    "target_curated": (1, 2000),
    "target_curated_min": (1, 2000),
    "target_curated_max": (1, 2000),
    "similarity_dedup_threshold": (0.5, 1.0),
    "video_hash_length": (4, 32),
    "tag_suggest_min_score": (0.05, 0.99),
    "dhash_dedup_max_distance": (0, 32),
    "window_peak_oversample": (2, 8),
    "learned_weights_blend": (0.0, 1.0),
    "learned_weights_min_events": (10, 100000),
    "reselect_diversity": (0.0, 1.0),
    "reselect_elasticity": (0.0, 1.0),
    "cut_adaptive_threshold": (1.0, 20.0),
    "cut_min_content_val": (1.0, 255.0),
    "cut_min_scene_s": (0.0, 30.0),
    "cut_fade_threshold": (0.0, 64.0),
    "openrouter_max_tokens": (64, 8192),
    "openrouter_temperature": (0.0, 2.0),
    "openrouter_top_p": (0.0, 1.0),
    "openrouter_max_dim": (128, 4096),
    "openrouter_concurrency": (1, 32),
}
SETTING_CHOICES: dict[str, tuple[str, ...]] = {
    "default_rule": CHUNK_RULES,
    "openrouter_mode": ("concept", "character", "style"),
    "openrouter_reasoning_effort": ("none", "minimal", "low", "medium", "high"),
    "cut_detector": ("content", "legacy"),
    "iqa_model": ("off", "clip_iqa", "topiq_nr"),
    "selection_embedder": ("clip", "dinov2"),
}
# Category axes of reselect's axis caps (frame membership from zs_scores, see reselect.frame_axes)
SELECTION_AXES = ("multi_person", "solo", "equipment", "face")


def validate_user_settings(payload: dict) -> tuple[dict, list[str], list[str]]:
    """Check a settings update against the defaults: (clean values, errors, ignored unknown keys).

    The type of each default decides what is accepted (ints may come as whole floats, floats as ints; NaN and
    infinities are rejected); `SETTING_RANGES` and `SETTING_CHOICES` add bounds. Unknown keys are dropped and
    reported rather than stored, so a typo cannot silently become a setting.
    """
    import math

    clean: dict = {}
    errors: list[str] = []
    ignored: list[str] = []
    for key, value in payload.items():
        if key not in DEFAULT_USER_SETTINGS:
            ignored.append(key)
            continue
        default = DEFAULT_USER_SETTINGS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                errors.append(f"{key}: expected true/false")
                continue
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                errors.append(f"{key}: expected a finite number")
                continue
            if isinstance(default, int) and not isinstance(default, bool):
                if float(value) != int(value):
                    errors.append(f"{key}: expected a whole number")
                    continue
                value = int(value)
            else:
                value = float(value)
            lo_hi = SETTING_RANGES.get(key)
            if lo_hi and not (lo_hi[0] <= value <= lo_hi[1]):
                errors.append(f"{key}: must be between {lo_hi[0]} and {lo_hi[1]}")
                continue
        elif isinstance(default, str):
            if not isinstance(value, str):
                errors.append(f"{key}: expected text")
                continue
            choices = SETTING_CHOICES.get(key)
            if choices and value not in choices:
                errors.append(f"{key}: must be one of {', '.join(choices)}")
                continue
        elif isinstance(default, dict):
            if not isinstance(value, dict):
                errors.append(f"{key}: expected an object")
                continue
            if key == "reselect_axis_caps":
                bad = [
                    a for a, share in value.items()
                    if a not in SELECTION_AXES or isinstance(share, bool) or not isinstance(share, (int, float))
                    or not math.isfinite(share) or not (0.0 <= float(share) <= 1.0)
                ]
                if bad:
                    errors.append(
                        f"{key}: axes must be {', '.join(SELECTION_AXES)} with a max share between 0 and 1"
                    )
                    continue
                value = {a: float(share) for a, share in value.items()}
        elif isinstance(default, list):
            if not isinstance(value, list):
                errors.append(f"{key}: expected a list")
                continue
        clean[key] = value
    lo, hi = clean.get("target_curated_min"), clean.get("target_curated_max")
    if lo is not None and hi is not None and lo > hi:
        errors.append("target_curated_min must not exceed target_curated_max")
    return clean, errors, ignored
