"""Pydantic Request & Response DTO schemas for REST & SSE/WebSocket APIs (Section 5)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.models.domain import ExtractionConfig


class SettingsUpdateRequest(BaseModel):
    openrouter_api_key: str | None = None
    primary_model: str | None = None
    fallback_model_1: str | None = None
    fallback_model_2: str | None = None
    max_tokens: int | None = Field(default=None, ge=64, le=4096)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.01, le=1.0)
    max_image_dim: int | None = Field(default=None, ge=256, le=2048)
    trigger_word: str | None = None
    trigger_position: Literal["prefix", "suffix", "natural_integration"] | None = None
    custom_rejection_regexes: list[str] | None = None
    quality_weights: dict[str, float] | None = None
    rag_dense_backend: Literal["tfidf_svd_128", "openrouter_embedding"] | None = None
    rag_embedding_model: str | None = None


class OpenRouterTestRequest(BaseModel):
    api_key: str | None = None


class DatasetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    source_type: Literal["image_folder", "video", "hybrid"] = "image_folder"
    source_path: str = Field(..., min_length=1)
    trigger_word: str = "ohwx_subject"
    trigger_position: Literal["prefix", "suffix", "natural_integration"] = "prefix"
    active_skill_id: str | None = "krea2_concept_v1"
    target_count: int = Field(default=150, ge=1, le=10000)
    extraction_config: ExtractionConfig = Field(default_factory=ExtractionConfig)
    auto_ingest: bool = True


class DatasetIngestRequest(BaseModel):
    mode: Literal["scene", "iframe", "interval"] = "scene"
    scene_threshold: float = Field(default=0.15, ge=0.01, le=0.95)
    interval_s: float = Field(default=0.5, ge=0.05, le=30.0)
    window_peak: int = Field(default=3, ge=1, le=10)
    target_candidates: int = Field(default=600, ge=1, le=10000)
    dedup_hamming_threshold: int = Field(default=6, ge=0, le=32)


class DatasetClusterRequest(BaseModel):
    hamming_threshold: int = Field(default=6, ge=0, le=32)
    dhash_threshold: int = Field(default=8, ge=0, le=32)
    hsv_min_cosine: float = Field(default=0.85, ge=0.0, le=1.0)
    auto_select_representatives: bool = True


class BatchTriageRequest(BaseModel):
    item_ids: list[int] = Field(..., min_length=1)
    curation_state: Literal["keep", "reject", "star", "candidate", "duplicate_hidden"] | None = None
    crop_geometry: dict[str, float] | None = None


class SwapRepresentativeRequest(BaseModel):
    new_representative_id: int


class StratifyRequest(BaseModel):
    target_count: int = Field(default=150, ge=1, le=10000)
    diversity_weight: float = Field(default=0.5, ge=0.0, le=1.0)


class AutotagRequest(BaseModel):
    plugin_id: str = "rule_cv_heuristics"
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class BatchTagRequest(BaseModel):
    item_ids: list[int] = Field(..., min_length=1)
    add_tags: list[dict[str, Any]] = Field(default_factory=list)
    remove_tag_ids: list[int] = Field(default_factory=list)
    remove_tag_slugs: list[str] = Field(default_factory=list)


class SkillCreateRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    training_type: Literal["character", "concept", "style", "general"] = "concept"
    system_prompt_template: str = Field(..., min_length=10)
    user_prompt_template: str = Field(..., min_length=10)
    glossary_template: str = ""
    min_words: int = Field(default=80, ge=10, le=500)
    max_words: int = Field(default=160, ge=20, le=1000)
    discouraged_regexes: list[str] = Field(default_factory=list)
    require_class_noun: bool = False


class DatasetCaptionJobRequest(BaseModel):
    skill_id: str | None = None
    trigger_word: str | None = None
    trigger_position: Literal["prefix", "suffix", "natural_integration"] | None = None
    class_noun: str = "woman"
    primary_model: str | None = None
    fallback_model_1: str | None = None
    fallback_model_2: str | None = None
    max_tokens: int | None = Field(default=None, ge=64, le=4096)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    concurrency: int = Field(default=4, ge=1, le=16)
    only_uncaptioned: bool = True
    run_synchronously: bool = True


class ManualCaptionUpdateRequest(BaseModel):
    caption_text: str = Field(..., min_length=1)
    enforce_trigger: bool = True
    trigger_word: str | None = None
    trigger_position: Literal["prefix", "suffix", "natural_integration"] | None = None


class DatasetExportRequest(BaseModel):
    image_format: Literal["png", "jpg"] = "png"
    max_edge: int = Field(default=2048, ge=64, le=8192)
    snap_multiple_16: bool = True
    trigger_word: str | None = None
    trigger_position: Literal["prefix", "suffix", "natural_integration"] | None = None
    create_zip: bool = True


class RagIndexRequest(BaseModel):
    dataset_id: str | None = None
    dense_backend: Literal["tfidf_svd_128", "openrouter_embedding"] = "tfidf_svd_128"
    embedding_model: str | None = "openai/text-embedding-3-small"


class RagRetrieveRequest(BaseModel):
    query: str = ""
    dataset_id: str | None = None
    trigger_word: str | None = None
    training_type: str | None = None
    tag_filters: dict[str, list[str]] = Field(default_factory=dict)
    top_k: int = Field(default=4, ge=1, le=50)
    diversity: float = Field(default=0.7, ge=0.0, le=1.0)
    retrieval_mode: Literal["hybrid", "fts5_only", "dense_only"] = "hybrid"
    dense_backend: Literal["tfidf_svd_128", "openrouter_embedding"] = "tfidf_svd_128"


class RagGenerateRequest(BaseModel):
    query: str = Field(default="", max_length=2000)
    selected_tags: dict[str, list[str]] = Field(default_factory=dict)
    dataset_id: str | None = None
    trigger_word: str = "ohwx_subject"
    style_trigger: str | None = None
    character: str | None = None
    model: str = "google/gemini-2.5-pro"
    top_k: int = Field(default=4, ge=1, le=20)
    n_variations: int = Field(default=1, ge=1, le=10)


class PromptExpandRequest(BaseModel):
    idea: str = Field(..., min_length=1, max_length=2000)
    dataset_id: str | None = None
    trigger_word: str = "ohwx_subject"
    style_trigger: str | None = None
    class_noun: str = "woman"
    mode: str = "CHARACTER"
    top_k: int = Field(default=3, ge=1, le=10)
    model: str = "google/gemini-2.5-pro"

