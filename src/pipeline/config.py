"""Central configuration: filesystem paths (env-overridable) and default user settings."""
from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
VIDEO_STATUSES = (
    "new", "chunking", "extracting_750", "curating_300", "captioning",
    "exported", "skipped", "ignored", "extracting", "curating"
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
    # 750 candidate extraction & 300 reduction
    "target_candidates": 750,
    "target_curated": 300,
    "category_quotas": {
        "multiple_persons": 0.50,
        "one_person_min": 0.25,
        "one_person_max": 0.30,
        "tools_equipment_min": 0.20,
        "tools_equipment_max": 0.25,
    },
    # OpenRouter API settings targeting google/gemma-4-31b-it (high reasoning) with cascade fallback to deepseek and grok
    "openrouter_api_key": "",
    "openrouter_model": "google/gemma-4-31b-it",
    "openrouter_fallback_layer2": "deepseek/deepseek-v4.1-flash",
    "openrouter_fallback_layer3": "x-ai/grok-4.7",
    "openrouter_reasoning_effort": "high",
    "openrouter_max_tokens": 300,
    "openrouter_temperature": 0.80,
    "openrouter_top_p": 0.90,
    "openrouter_mode": "concept",  # 'concept', 'character', or 'style'
    "openrouter_trigger": "restrained_elegance",
    "openrouter_class_noun": "woman",
    "openrouter_max_dim": 512,
    "openrouter_concurrency": 4,
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
