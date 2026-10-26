"""Canonical domain models and value objects for Modules A, B, C, and D."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

TagSource = Literal["manual", "rule", "ml_plugin"]
TagCategory = Literal[
    "pose",
    "lighting",
    "outfit",
    "expression",
    "style_element",
    "shot_scale",
    "environment",
    "equipment",
]
CurationState = Literal[
    "keep",
    "star",
    "reject",
    "candidate",
    "duplicate_hidden",
    "auto_rejected",
]
ExtractionMode = Literal["scene", "iframe", "interval"]
FrameExtractType = Literal["image", "scene_cut", "iframe", "interval_peak"]


class Tag(BaseModel):
    """Unified Tag domain model supporting manual, rule-based, and ML plugin sources."""

    name: str = Field(..., min_length=1, max_length=64)
    slug: str = Field(..., min_length=1, max_length=64)
    source: TagSource = "manual"
    plugin_id: str = "manual"
    category: TagCategory = "style_element"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox: dict[str, float] | None = None  # Optional normalized {x, y, w, h}


class QualityWeights(BaseModel):
    """Configurable weights for composite mathematical quality scoring Q in [0, 1]."""

    sharpness: float = Field(default=0.45, ge=0.0, le=1.0)
    exposure: float = Field(default=0.25, ge=0.0, le=1.0)
    entropy: float = Field(default=0.15, ge=0.0, le=1.0)
    contrast: float = Field(default=0.15, ge=0.0, le=1.0)

    def normalized(self) -> tuple[float, float, float, float]:
        total = self.sharpness + self.exposure + self.entropy + self.contrast
        if total <= 1e-9:
            return (0.45, 0.25, 0.15, 0.15)
        return (
            self.sharpness / total,
            self.exposure / total,
            self.entropy / total,
            self.contrast / total,
        )


@dataclass(slots=True)
class QualityScoreResult:
    """Detailed mathematical quality evaluation of a single image or video frame."""

    sharpness_laplacian: float          # Top-3 4x4 tile Laplacian variance minus Immerkaer noise
    sharpness_laplacian_global: float   # Whole-frame resolution-normalized Laplacian variance
    sharpness_tenengrad: float          # Central 60% Sobel energy sqrt(mean(Gx^2 + Gy^2))
    sharpness_score: float              # Normalized S_norm in [0, 1]
    noise_sigma: float                  # Immerkaer flat-region sensor noise estimate
    exposure_mean_luma: float           # Mean luminance mu_I in [0, 255]
    shadow_clipping_frac: float         # Fraction of pixels < 5
    highlight_clipping_frac: float      # Fraction of pixels > 250
    exposure_clipping_frac: float       # Total clipping fraction c_black + c_white in [0, 1]
    exposure_score: float               # Normalized E_norm in [0, 1]
    entropy_shannon: float              # Shannon entropy H(I) in [0, 8] bits
    entropy_score: float                # Normalized H_norm in [0, 1]
    contrast_rms: float                 # RMS contrast sigma_I
    contrast_score: float               # Normalized C_norm in [0, 1]
    border_black_frac: float            # Fraction of frame covered by dark letterbox/pillarbox borders
    quality_score: float                # Composite score Q in [0, 1]
    passed_gates: bool                  # True if no hard quality gates triggered
    reject_reasons: list[str] = field(default_factory=list)
    primary_reject_reason: str | None = None
    curation_state: CurationState = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sharpness_laplacian": round(self.sharpness_laplacian, 3),
            "sharpness_laplacian_global": round(self.sharpness_laplacian_global, 3),
            "sharpness_tenengrad": round(self.sharpness_tenengrad, 3),
            "sharpness_score": round(self.sharpness_score, 4),
            "noise_sigma": round(self.noise_sigma, 4),
            "exposure_mean_luma": round(self.exposure_mean_luma, 2),
            "shadow_clipping_frac": round(self.shadow_clipping_frac, 4),
            "highlight_clipping_frac": round(self.highlight_clipping_frac, 4),
            "exposure_clipping_frac": round(self.exposure_clipping_frac, 4),
            "exposure_score": round(self.exposure_score, 4),
            "entropy_shannon": round(self.entropy_shannon, 4),
            "entropy_score": round(self.entropy_score, 4),
            "contrast_rms": round(self.contrast_rms, 3),
            "contrast_score": round(self.contrast_score, 4),
            "border_black_frac": round(self.border_black_frac, 4),
            "quality_score": round(self.quality_score, 4),
            "passed_gates": self.passed_gates,
            "reject_reasons": list(self.reject_reasons),
            "primary_reject_reason": self.primary_reject_reason,
            "curation_state": self.curation_state,
        }


@dataclass(slots=True)
class MediaFingerprint:
    """Perceptual hashes and color histogram fingerprint for fast deduplication."""

    phash_hex: str          # 16-char hex 64-bit DCT pHash
    phash_u64: int          # Unsigned 64-bit integer representation
    dhash_hex: str          # 16-char hex 64-bit gradient dHash
    dhash_u64: int          # Unsigned 64-bit integer representation
    hsv_hist: bytes         # 24 float32 L2-normalized HSV histogram (96 bytes)


@dataclass(slots=True)
class ClusterAssignment:
    """Per-item cluster membership and representative status."""

    item_id: int
    cluster_id: int
    is_cluster_representative: bool
    curation_state: CurationState
    distance_to_representative: int = 0


@dataclass(slots=True)
class ClusterRecord:
    """Summary of a perceptual hash similarity cluster."""

    cluster_id: int
    representative_item_id: int
    centroid_phash: str
    centroid_dhash: str
    member_ids: list[int]
    member_count: int
    max_quality_score: float
    scene_index: int = 0


class ExtractionConfig(BaseModel):
    """Configuration for video frame extraction and deduplication."""

    mode: ExtractionMode = "scene"
    scene_threshold: float = Field(default=0.15, ge=0.01, le=0.95)
    interval_s: float = Field(default=0.5, ge=0.05, le=30.0)
    window_peak: int = Field(default=3, ge=1, le=10)
    motion_weight: float = Field(default=0.5, ge=0.0, le=2.0)
    target_candidates: int = Field(default=600, ge=1, le=10000)
    max_extract_dim: int = Field(default=0, ge=0, le=4096)  # 0 = native resolution
    thumbnail_dim: int = Field(default=384, ge=64, le=1024)
    dedup_hamming_threshold: int = Field(default=6, ge=0, le=32)
    dedup_dhash_threshold: int = Field(default=8, ge=0, le=32)
    dedup_hsv_min_cosine: float = Field(default=0.85, ge=0.0, le=1.0)
    prefer_pyav: bool = False


@dataclass(slots=True)
class VideoMetadata:
    """Probed video stream metadata."""

    path: str
    duration_s: float
    fps: float
    width: int
    height: int
    codec: str
    pix_fmt: str
    frame_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "duration_s": round(self.duration_s, 3),
            "fps": round(self.fps, 3),
            "width": self.width,
            "height": self.height,
            "codec": self.codec,
            "pix_fmt": self.pix_fmt,
            "frame_count": self.frame_count,
        }


@dataclass(slots=True)
class MediaItemRecord:
    """In-memory representation of an ingested image or extracted video frame."""

    item_id: int
    source_filename: str
    source_path: str
    extracted_path: str | None
    thumbnail_path: str | None
    timestamp_s: float | None
    scene_index: int
    frame_extract_type: FrameExtractType
    width: int
    height: int
    aspect_ratio: float
    phash: str
    dhash: str
    hsv_hist: bytes
    quality: QualityScoreResult
    cluster_id: int | None = None
    is_cluster_representative: bool = True
    curation_state: CurationState = "candidate"
    existing_caption: str | None = None
    tags: list[Tag] = field(default_factory=list)


class StratificationResult(BaseModel):
    """Output of DatasetStratifier selection balancing."""

    recommended_keep_ids: list[int]
    recommended_demote_ids: list[int]
    outlier_ids: list[int]
    distribution: dict[str, Any] = Field(default_factory=dict)
