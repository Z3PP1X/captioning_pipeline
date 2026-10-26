"""MOSS-Image pipeline models and configurations."""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_AESTHETIC_STYLE_LOCKED_TRAITS = [
    "painting",
    "illustration",
    "drawing",
    "sketch",
    "3d render",
    "cgi",
    "anime",
    "manga",
    "watercolor",
    "oil on canvas",
    "impasto",
    "brushstrokes",
    "lineart",
    "cel-shaded",
    "pastel",
    "color palette",
    "artstyle",
]

DEFAULT_CHARACTER_LOCKED_TRAITS = [
    "green eyes",
    "silver hair",
    "braided hair",
    "scar on left cheek",
    "freckles",
]

DEFAULT_OBJECT_CONCEPT_LOCKED_TRAITS = [
    "metallic finish",
    "weathering",
    "panel lines",
    "hexagonal barrel",
]


class TargetArchitecture(StrEnum):
    KREA_2 = "KREA_2"
    MOSS_IMAGE = "MOSS_IMAGE"


class TrainingMode(StrEnum):
    CHARACTER = "CHARACTER"
    AESTHETIC_STYLE = "AESTHETIC_STYLE"
    OBJECT_CONCEPT = "OBJECT_CONCEPT"


class OpenRouterConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary_model: str = "google/gemini-2.5-flash"
    fallback_model_1: str = "meta-llama/llama-3.2-11b-vision-instruct"
    fallback_model_2: str = "qwen/qwen-2.5-vl-72b-instruct"
    temperature: float = 0.2
    max_tokens: int = 1024


class MossDatasetConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_architecture: TargetArchitecture = TargetArchitecture.MOSS_IMAGE
    training_mode: TrainingMode = TrainingMode.CHARACTER
    trigger_phrase: str = ""
    class_noun: str = ""
    locked_traits: list[str] = Field(default_factory=list)
    openrouter_config: OpenRouterConfig = Field(default_factory=OpenRouterConfig)


class WatermarkBox(BaseModel):
    model_config = ConfigDict(extra="ignore")

    box_2d: list[int] | tuple[int, int, int, int]
    label: str = "watermark"


class MossCaptionTier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    long: str
    medium: str
    short: str


class MossCaptionRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    frame_id: int
    video_id: str | None = None
    trigger_phrase: str = ""
    caption_long: str = ""
    caption_medium: str = ""
    caption_short: str = ""
    watermark: list[dict[str, Any]] | list[WatermarkBox] | None = None
    borders: list[str] | None = None
    suggested_crop: dict[str, Any] | None = None
    applied_crop: dict[str, Any] | None = None
    leaked_traits: list[str] | None = None
    review_status: str = "PENDING"
    model_used: str | None = None
    created_at: float | None = None
