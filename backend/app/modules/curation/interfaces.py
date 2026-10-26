"""Strategy and Plugin interfaces for Module B (Tagging & Dataset Stratification)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from backend.app.models.domain import Tag


class BaseTaggerPlugin(ABC):
    """Abstract interface for both Non-ML Rule Taggers and optional ML Taggers (WD14, CLIP, SigLIP, JoyTag)."""

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique identifier, e.g. 'rule_cv_heuristics', 'wd14_swinv2_onnx', 'siglip_zero_shot'."""

    @property
    @abstractmethod
    def requires_ml_runtime(self) -> bool:
        """False for pure OpenCV/NumPy heuristics; True for ONNX/PyTorch plugins."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if runtime dependencies and weights are present without raising import errors."""

    @abstractmethod
    def analyze_item(self, img_bgr: np.ndarray, metadata: dict[str, Any] | None = None) -> list[Tag]:
        """Analyze a single BGR image + optional metadata dict and return structured Tag predictions."""

    def analyze_batch(
        self,
        items: list[tuple[np.ndarray, dict[str, Any] | None]],
    ) -> list[list[Tag]]:
        """Batch entrypoint; subclasses with GPU/ONNX batching can override."""
        return [self.analyze_item(img, meta) for img, meta in items]


class DatasetStratifier(ABC):
    """Analyzes dataset balance, detects visual/tag outliers, and recommends keep/demote adjustments."""

    @abstractmethod
    def compute_distribution(
        self,
        media_items: list[dict[str, Any]],
        tags_by_item: dict[int, list[Tag]] | None = None,
    ) -> dict[str, Any]:
        """Return histogram buckets across aspect ratio, lighting/brightness, color temperature,
        temporal spread (for video sources), and TagCategory coverage."""

    @abstractmethod
    def detect_outliers_and_gaps(
        self,
        distribution: dict[str, Any],
        target_size: int,
    ) -> dict[str, Any]:
        """Identify over-represented clusters/buckets, under-represented categories, and quality outliers."""

    @abstractmethod
    def stratify_selection(
        self,
        media_items: list[dict[str, Any]],
        tags_by_item: dict[int, list[Tag]] | None = None,
        target_count: int = 150,
        diversity_weight: float = 0.5,
    ) -> dict[str, list[int]]:
        """Return {'recommended_keep_ids': [...], 'recommended_demote_ids': [...], 'outlier_ids': [...]}."""
