"""Deterministic geometry and cropping engine for MOSS-Image pipeline."""
from __future__ import annotations

from pipeline.geometry.cropper import apply_moss_crop, compute_optimal_moss_crop
from pipeline.geometry.letterbox import crop_letterbox_numpy

__all__ = ["apply_moss_crop", "compute_optimal_moss_crop", "crop_letterbox_numpy"]
