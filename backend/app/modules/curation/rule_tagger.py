"""Default Zero-ML Rule-Based Heuristic Tagger Plugin (`RuleBasedHeuristicPlugin`).

Extracts deterministic mathematical CV tags across 5 semantic categories without any local
neural network or ONNX dependency:
- `shot_scale`: Aspect ratio framing (`portrait_tall`, `square_framing`, `landscape_wide`) and
  center-vs-periphery Sobel energy ratio (`close_focus_bokeh`, `medium_subject_framing`, `full_scene_deep_dof`).
- `pose`: Central directional Sobel gradient dominance (`vertical_standing_posture`,
  `horizontal_reclining_posture`, `centered_balanced_posture`).
- `lighting`: Luminance and RMS contrast key (`chiaroscuro_high_contrast`, `low_key_dark`,
  `balanced_midtone`, `high_key_bright`).
- `style_element`: CIE LAB color cast (`warm_tungsten_cast`, `cool_daylight_cast`, `neutral_color_balance`)
  and HSV saturation (`monochrome_desaturated`, `vivid_saturated`, `muted_pastel_palette`).
- `environment`: Periphery texture & luminance backdrop analysis (`dark_void_backdrop`,
  `minimal_studio_backdrop`, `textured_complex_environment`).
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from backend.app.models.domain import Tag, TagCategory
from backend.app.modules.curation.interfaces import BaseTaggerPlugin


class RuleBasedHeuristicPlugin(BaseTaggerPlugin):
    """Deterministic OpenCV/NumPy heuristic tagger requiring zero ML weights."""

    def __init__(self, eval_dim: int = 256) -> None:
        self.eval_dim = max(64, int(eval_dim))

    @property
    def plugin_id(self) -> str:
        return "rule_cv_heuristics"

    @property
    def requires_ml_runtime(self) -> bool:
        return False

    def is_available(self) -> bool:
        return True

    def _make_tag(
        self,
        slug: str,
        name: str,
        category: TagCategory,
        confidence: float = 0.85,
    ) -> Tag:
        return Tag(
            name=name,
            slug=slug,
            source="rule",
            plugin_id=self.plugin_id,
            category=category,
            confidence=round(float(np.clip(confidence, 0.0, 1.0)), 3),
        )

    def analyze_item(
        self,
        img_bgr: np.ndarray,
        metadata: dict[str, Any] | None = None,
    ) -> list[Tag]:
        """Analyze a BGR image and optional metadata dictionary to produce structured Tags."""
        if img_bgr is None or img_bgr.size == 0:
            return []

        if img_bgr.ndim == 2:
            img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2BGR)
        elif img_bgr.shape[2] == 4:
            img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_BGRA2BGR)

        orig_h, orig_w = img_bgr.shape[:2]
        meta = metadata or {}
        w = int(meta.get("width") or orig_w)
        h = int(meta.get("height") or orig_h)
        aspect = float(meta.get("aspect_ratio") or (float(w) / float(max(1, h))))

        # Downscale for sub-millisecond color & gradient analysis
        longest = max(orig_h, orig_w)
        if longest > self.eval_dim:
            scale = self.eval_dim / float(longest)
            ew = max(16, int(round(orig_w * scale)))
            eh = max(16, int(round(orig_h * scale)))
            small_bgr = cv2.resize(img_bgr, (ew, eh), interpolation=cv2.INTER_AREA)
        else:
            small_bgr = img_bgr
            eh, ew = orig_h, orig_w

        gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)
        gray_f32 = gray.astype(np.float32)

        tags: list[Tag] = []

        # ------------------------------------------------------------------
        # 1. Aspect Ratio & Shot Scale / Depth-of-Field Proxy (`shot_scale`)
        # ------------------------------------------------------------------
        if aspect < 0.80:
            tags.append(self._make_tag("portrait_tall", "Portrait Tall", "shot_scale", 0.98))
        elif aspect > 1.20:
            tags.append(self._make_tag("landscape_wide", "Landscape Wide", "shot_scale", 0.98))
        else:
            tags.append(self._make_tag("square_framing", "Square Framing", "shot_scale", 0.98))

        gx = cv2.Sobel(gray_f32, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray_f32, cv2.CV_32F, 0, 1, ksize=3)
        grad_energy = gx * gx + gy * gy

        cy0, cy1 = int(eh * 0.25), max(int(eh * 0.25) + 1, int(eh * 0.75))
        cx0, cx1 = int(ew * 0.25), max(int(ew * 0.25) + 1, int(ew * 0.75))

        center_mask = np.zeros((eh, ew), dtype=bool)
        center_mask[cy0:cy1, cx0:cx1] = True
        periph_mask = ~center_mask

        center_energy = float(grad_energy[center_mask].mean()) if center_mask.any() else 0.0
        periph_energy = float(grad_energy[periph_mask].mean()) if periph_mask.any() else 0.0
        focus_ratio = center_energy / max(periph_energy, 1.0)

        if focus_ratio >= 2.2:
            conf = min(0.95, 0.70 + 0.08 * (focus_ratio - 2.2))
            tags.append(self._make_tag("close_focus_bokeh", "Close Focus / Bokeh", "shot_scale", conf))
        elif focus_ratio >= 1.25:
            tags.append(self._make_tag("medium_subject_framing", "Medium Subject Framing", "shot_scale", 0.80))
        else:
            tags.append(self._make_tag("full_scene_deep_dof", "Full Scene / Deep DoF", "shot_scale", 0.82))

        # ------------------------------------------------------------------
        # 2. Subject Orientation / Posture Proxy (`pose`)
        # ------------------------------------------------------------------
        cgx_abs = float(np.abs(gx[center_mask]).mean()) if center_mask.any() else 1.0
        cgy_abs = float(np.abs(gy[center_mask]).mean()) if center_mask.any() else 1.0
        if cgx_abs / max(cgy_abs, 1e-3) >= 1.25:
            tags.append(self._make_tag("vertical_standing_posture", "Vertical / Standing Posture", "pose", 0.76))
        elif cgy_abs / max(cgx_abs, 1e-3) >= 1.25:
            tags.append(self._make_tag("horizontal_reclining_posture", "Horizontal / Reclining Posture", "pose", 0.76))
        else:
            tags.append(self._make_tag("centered_balanced_posture", "Centered Balanced Posture", "pose", 0.74))

        # ------------------------------------------------------------------
        # 3. Lighting & Luminance Key (`lighting`)
        # ------------------------------------------------------------------
        mean_luma = float(gray_f32.mean())
        std_luma = float(gray_f32.std())

        if mean_luma < 115.0 and std_luma >= 45.0:
            conf = min(0.96, 0.75 + (std_luma - 45.0) / 100.0)
            tags.append(
                self._make_tag("chiaroscuro_high_contrast", "Chiaroscuro High Contrast", "lighting", conf)
            )
        elif mean_luma < 75.0:
            tags.append(self._make_tag("low_key_dark", "Low-Key Dark", "lighting", 0.88))
        elif mean_luma > 180.0:
            tags.append(self._make_tag("high_key_bright", "High-Key Bright", "lighting", 0.88))
        else:
            tags.append(self._make_tag("balanced_midtone", "Balanced Midtone", "lighting", 0.85))

        # ------------------------------------------------------------------
        # 4. Color Temperature & Saturation (`style_element`)
        # ------------------------------------------------------------------
        hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)
        mean_sat = float(hsv[:, :, 1].mean())

        lab = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2LAB)
        # In OpenCV uint8 LAB, a* and b* are neutral at 128
        mean_b_shift = float(lab[:, :, 2].mean()) - 128.0

        if mean_sat < 18.0:
            tags.append(
                self._make_tag("monochrome_desaturated", "Monochrome / Desaturated", "style_element", 0.94)
            )
        else:
            if mean_sat > 110.0:
                tags.append(self._make_tag("vivid_saturated", "Vivid Saturated", "style_element", 0.88))
            elif mean_sat < 45.0:
                tags.append(self._make_tag("muted_pastel_palette", "Muted Palette", "style_element", 0.80))

            if mean_b_shift > 8.0:
                conf = min(0.95, 0.72 + (mean_b_shift - 8.0) / 40.0)
                tags.append(self._make_tag("warm_tungsten_cast", "Warm Tungsten Cast", "style_element", conf))
            elif mean_b_shift < -8.0:
                conf = min(0.95, 0.72 + (abs(mean_b_shift) - 8.0) / 40.0)
                tags.append(self._make_tag("cool_daylight_cast", "Cool Daylight Cast", "style_element", conf))
            else:
                tags.append(self._make_tag("neutral_color_balance", "Neutral Color Balance", "style_element", 0.82))

        # ------------------------------------------------------------------
        # 5. Periphery Environment / Backdrop (`environment`)
        # ------------------------------------------------------------------
        periph_pixels = gray_f32[periph_mask] if periph_mask.any() else gray_f32.ravel()
        periph_luma = float(periph_pixels.mean())
        periph_std = float(periph_pixels.std())

        if periph_luma < 32.0 and periph_std < 28.0:
            tags.append(self._make_tag("dark_void_backdrop", "Dark Void Backdrop", "environment", 0.87))
        elif periph_std < 24.0 and focus_ratio >= 1.5:
            tags.append(self._make_tag("minimal_studio_backdrop", "Minimal Studio Backdrop", "environment", 0.84))
        elif periph_std >= 42.0 and periph_energy >= 400.0:
            tags.append(
                self._make_tag("textured_complex_environment", "Textured Complex Environment", "environment", 0.82)
            )
        else:
            tags.append(self._make_tag("indoor_ambient_setting", "Indoor Ambient Setting", "environment", 0.75))

        return tags
