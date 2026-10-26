"""Application configuration & environment variable bindings for the modular backend."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_appdata_dir() -> Path:
    env_dir = os.environ.get("PIPELINE_APPDATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    root = _default_project_root()
    return root / "cache" / "appdata"


def _default_input_dir() -> Path:
    env_dir = os.environ.get("PIPELINE_INPUT_DIR", "").strip() or os.environ.get("HOST_MEDIA_ROOT", "").strip()
    if env_dir:
        return Path(env_dir)
    root = _default_project_root()
    data_input = Path("/data/input")
    if data_input.is_dir():
        return data_input
    return root / "data"


def _default_export_dir() -> Path:
    env_dir = os.environ.get("PIPELINE_EXPORT_DIR", "").strip() or os.environ.get("HOST_EXPORT_ROOT", "").strip()
    if env_dir:
        return Path(env_dir)
    root = _default_project_root()
    data_exports = Path("/data/exports")
    if data_exports.is_dir():
        return data_exports
    return root / "finished"


class AppSettings(BaseModel):
    """Runtime settings for the Captioning Pipeline & LoRA Dataset Studio backend."""

    project_root: Path = Field(default_factory=_default_project_root)
    appdata_dir: Path = Field(default_factory=_default_appdata_dir)
    input_dir: Path = Field(default_factory=_default_input_dir)
    export_dir: Path = Field(default_factory=_default_export_dir)
    db_path: Path | None = None

    openrouter_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", "").strip()
    )
    openrouter_base_url: str = Field(
        default_factory=lambda: os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/")
    )
    primary_model: str = Field(
        default_factory=lambda: os.environ.get("PRIMARY_MODEL", "google/gemini-2.5-pro")
    )
    fallback_model_1: str = Field(
        default_factory=lambda: os.environ.get("FALLBACK_MODEL_1", "qwen/qwen-2.5-vl-72b-instruct")
    )
    fallback_model_2: str = Field(
        default_factory=lambda: os.environ.get(
            "FALLBACK_MODEL_2", "meta-llama/llama-3.2-90b-vision-instruct"
        )
    )
    default_max_tokens: int = 600
    default_temperature: float = 0.4
    default_top_p: float = 0.90
    default_trigger_word: str = "ohwx_subject"
    default_trigger_position: Literal["prefix", "suffix", "natural_integration"] = "prefix"

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path is not None:
            return Path(self.db_path)
        env_db = os.environ.get("PIPELINE_DB_PATH", "").strip()
        if env_db:
            return Path(env_db)
        return self.appdata_dir / "db" / "state.db"

    @property
    def frames_dir(self) -> Path:
        return self.appdata_dir / "frames"

    @property
    def thumbnails_dir(self) -> Path:
        return self.appdata_dir / "thumbnails"

    @property
    def rag_dir(self) -> Path:
        return self.appdata_dir / "rag"

    def ensure_dirs(self) -> None:
        self.resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self.rag_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    settings = AppSettings()
    settings.ensure_dirs()
    return settings
