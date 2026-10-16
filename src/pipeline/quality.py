"""Quality v2 (Phase F2, review §5.2): hard gates first, then per-video percentile-normalised components.

1. **Gates** (`quality_gates`, per chunk, before faces/CLIP): `too_dark` (mean luma < 16), `blown_out` (> 245),
   `clipping` (> 40 % of pixels clipped), `letterbox_black` (uniform dark borders cover more than 45 % of the
   frame, i.e. a pillar/letterboxed insert or a mostly black card left after the configured crop) and `blur`
   (tile sharpness below the absolute floor 15 or below 25 % of the chunk median, so a soft chunk is judged
   against itself). A frame failing any gate is dropped with its reasons (job stats, `frame_features.gates`).
2. **Raw components** (`raw_components`, per surviving sample, needs its faces): subject sharpness = Laplacian
   variance on the largest face (expanded to head and shoulders) or else the centre 60 %, minus the noise
   contribution `20 * sigma^2`; noise sigma (Immerkaer estimator on the flat half of the frame); subject
   exposure (band 40-210 on the subject, clipping in the subject and globally); face quality (largest face
   short side in native pixels saturating at 160, yaw proxy from the YuNet eye/nose landmarks, detector score;
   None without a face); motion energy (from sampling); optional IQA.
3. **Normalisation** (`normalise_components`): each component becomes `0.7 * pct_in_chunk + 0.3 * pct_in_video`
   (lower-is-better ones inverted; chunks with < 3 samples use the video percentile; a missing face is neutral
   0.5). `sharpness` = q_sharp, `quality` = 0.30 exposure + 0.20 noise + 0.20 motion + 0.30 face (IQA, when
   present, takes 25 %). These two feed the unchanged composite formula, so learned weights keep working.

Blockiness (8x8 grid discontinuity) is not measured: samples are analysed at <= 512 px, where the codec's block
grid is resampled away.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np

GATE_DARK = 16.0
GATE_BRIGHT = 245.0
GATE_CLIPPING = 0.40
GATE_BORDER_BLACK = 0.45
GATE_SHARP_FLOOR = 15.0
GATE_SHARP_REL = 0.25

# component -> True when higher is better
COMPONENT_DIRECTION = {"sharp": True, "noise": False, "exposure": True, "face": True, "motion": False, "iqa": True}


def border_black_fraction(gray: np.ndarray, thr: float = 16.0) -> float:
    """Share of the frame covered by uniform dark rows/columns at the borders (letterbox / pillarbox bars)."""
    h, w = gray.shape[:2]
    if h < 4 or w < 4:
        return 0.0
    rows = (gray.mean(axis=1) < thr) & (gray.std(axis=1) < 8.0)
    cols = (gray.mean(axis=0) < thr) & (gray.std(axis=0) < 8.0)

    def run(mask: np.ndarray) -> int:
        n = 0
        for v in mask:
            if not v:
                break
            n += 1
        return n

    top, bottom = run(rows), run(rows[::-1])
    left, right = run(cols), run(cols[::-1])
    if top + bottom >= h or left + right >= w:
        return 1.0
    inner = (h - top - bottom) * (w - left - right)
    return float(1.0 - inner / float(h * w))


def gate_metrics(img_bgr: np.ndarray) -> dict[str, float]:
    """Extra metrics the gates need beyond `compute_tier1_metrics` (cheap, on <= 512 px)."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    return {"border_black": round(border_black_fraction(gray), 4)}


def quality_gates(metrics_list: list[dict[str, Any]]) -> list[list[str]]:
    """Failed gate reasons per sample of one chunk ([] = passed). Blur is relative to the chunk median."""
    sharp = [float(m.get("tile_sharp", 0.0)) for m in metrics_list
             if GATE_DARK <= float(m.get("mean_luma", 128.0)) <= GATE_BRIGHT]
    rel_floor = GATE_SHARP_REL * float(np.median(sharp)) if sharp else 0.0
    floor = max(GATE_SHARP_FLOOR, rel_floor)
    out: list[list[str]] = []
    for m in metrics_list:
        reasons: list[str] = []
        luma = float(m.get("mean_luma", 128.0))
        if luma < GATE_DARK:
            reasons.append("too_dark")
        elif luma > GATE_BRIGHT:
            reasons.append("blown_out")
        if float(m.get("clipping", 0.0)) > GATE_CLIPPING:
            reasons.append("clipping")
        if float(m.get("border_black", 0.0)) > GATE_BORDER_BLACK:
            reasons.append("letterbox_black")
        if float(m.get("tile_sharp", 0.0)) < floor:
            reasons.append("blur")
        out.append(reasons)
    return out


def noise_sigma(gray: np.ndarray) -> float:
    """Immerkaer fast noise estimate, restricted to the flatter half of the frame (texture is not noise)."""
    g = gray.astype(np.float32)
    if g.shape[0] < 8 or g.shape[1] < 8:
        return 0.0
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    resp = np.abs(cv2.filter2D(g, cv2.CV_32F, kernel))[1:-1, 1:-1]
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)[1:-1, 1:-1]
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)[1:-1, 1:-1]
    mag = np.abs(gx) + np.abs(gy)
    mask = mag <= np.percentile(mag, 50)
    if not mask.any():
        return 0.0
    return float(math.sqrt(math.pi / 2.0) / 6.0 * resp[mask].mean())


def _subject_box(shape: tuple[int, ...], faces: list[dict[str, Any]] | None) -> tuple[int, int, int, int, bool]:
    h, w = shape[:2]
    if faces:
        big = max(faces, key=lambda f: (f.get("box") or [0, 0, 0, 0])[2] * (f.get("box") or [0, 0, 0, 0])[3])
        x, y, fw, fh = [int(v) for v in (big.get("box") or [0, 0, 0, 0])[:4]]
        if fw > 2 and fh > 2:
            cx, cy = x + fw / 2.0, y + fh / 2.0
            ew, eh = fw * 1.6, fh * 1.8  # head and shoulders
            x0, y0 = int(max(0, cx - ew / 2)), int(max(0, cy - eh / 2.4))
            x1, y1 = int(min(w, cx + ew / 2)), int(min(h, y0 + eh))
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                return x0, y0, x1, y1, True
    return int(w * 0.2), int(h * 0.2), int(w * 0.8), int(h * 0.8), False


def face_quality(faces: list[dict[str, Any]] | None, native_scale: float = 1.0) -> dict[str, Any] | None:
    """Size (native px short side, saturating at 160), yaw proxy from YuNet landmarks, detector score."""
    if not faces:
        return None
    big = max(faces, key=lambda f: (f.get("box") or [0, 0, 0, 0])[2] * (f.get("box") or [0, 0, 0, 0])[3])
    bx = big.get("box") or [0, 0, 0, 0]
    short = float(min(bx[2], bx[3])) * float(native_scale)
    size_score = min(1.0, short / 160.0)
    yaw = None
    pose_score = 0.75  # unknown pose: mildly below a confirmed frontal face
    lm = big.get("landmarks")
    if lm and len(lm) >= 3:
        (rx, _), (lx, _), (nx, _) = lm[0][:2], lm[1][:2], lm[2][:2]
        eye_dist = abs(lx - rx)
        if eye_dist > 1e-3:
            yaw = abs(nx - (rx + lx) / 2.0) / eye_dist  # 0 frontal, ~0.5+ profile
            pose_score = 1.0 - min(1.0, max(0.0, (yaw - 0.15) / 0.45))
        else:
            yaw, pose_score = 1.0, 0.0
    det = float(big.get("score") or 0.8)
    score = 0.55 * size_score + 0.35 * pose_score + 0.10 * min(1.0, det)
    return {"score": round(score, 4), "short_side_px": round(short, 1), "yaw": None if yaw is None else round(yaw, 3),
            "pose": round(pose_score, 3)}


def raw_components(
    img_bgr: np.ndarray,
    metrics: dict[str, Any] | None = None,
    faces: list[dict[str, Any]] | None = None,
    native_scale: float = 1.0,
) -> dict[str, Any]:
    """Unnormalised quality components of one (cropped, <= 512 px) sample."""
    metrics = metrics or {}
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    x0, y0, x1, y1, on_face = _subject_box(gray.shape, faces)
    sub = gray[y0:y1, x0:x1]
    sigma = noise_sigma(gray)
    lap = float(cv2.Laplacian(sub, cv2.CV_32F).var()) if sub.size else 0.0
    sharp = max(0.0, lap - 20.0 * sigma * sigma)

    sub_luma = float(sub.mean()) if sub.size else float(gray.mean())
    band = 1.0 - min(1.0, max(0.0, 40.0 - sub_luma, sub_luma - 210.0) / 60.0)
    sub_clip = float(((sub <= 3) | (sub >= 252)).mean()) if sub.size else 0.0
    glob_clip = float(metrics.get("clipping", ((gray <= 2) | (gray >= 253)).mean()))
    exposure = band - 1.5 * sub_clip - 0.5 * glob_clip

    fq = face_quality(faces, native_scale)
    out: dict[str, Any] = {
        "sharp": round(sharp, 3),
        "noise": round(sigma, 4),
        "exposure": round(exposure, 4),
        "face": None if fq is None else fq["score"],
        "motion": None if metrics.get("motion_energy") is None else float(metrics["motion_energy"]),
        "subject": "face" if on_face else "centre",
        "subject_luma": round(sub_luma, 2),
    }
    if fq is not None:
        out["face_short_px"] = fq["short_side_px"]
        out["face_yaw"] = fq["yaw"]
    return out


def _pct(values: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Percentile of each value within `ref` (ties count half), in [0, 1]."""
    ref = np.sort(ref[np.isfinite(ref)])
    n = ref.size
    out = np.full(values.shape, 0.5)
    if n < 2:
        return out
    less = np.searchsorted(ref, values, side="left")
    leq = np.searchsorted(ref, values, side="right")
    # value's own entry is in ref: rank = less + (equal - 1) / 2 over n - 1 steps
    out = (less + np.maximum(0, leq - less - 1) / 2.0) / (n - 1)
    return np.clip(out, 0.0, 1.0)


def normalise_components(
    raws: list[dict[str, Any]],
    chunk_ids: list[Any],
    video_reference: dict[str, list[float]] | None = None,
) -> list[dict[str, float]]:
    """Per-component `0.7 * pct_in_chunk + 0.3 * pct_in_video`, oriented so that 1 = best."""
    n = len(raws)
    if n == 0:
        return []
    chunk_arr = np.array([str(c) for c in chunk_ids])
    out: list[dict[str, float]] = [{} for _ in range(n)]
    for comp, higher_better in COMPONENT_DIRECTION.items():
        vals = np.array([np.nan if r.get(comp) is None else float(r[comp]) for r in raws], dtype=np.float64)
        present = np.isfinite(vals)
        if not present.any():
            continue
        ref_v = vals[present]
        extra = np.array([v for v in (video_reference or {}).get(comp, []) if v is not None], dtype=np.float64)
        video_ref = np.concatenate([ref_v, extra]) if extra.size else ref_v
        pv = _pct(vals, video_ref)
        q = pv.copy()
        for c in np.unique(chunk_arr):
            m = (chunk_arr == c) & present
            if m.sum() >= 3:
                pc = _pct(vals[m], vals[m])
                q[m] = 0.7 * pc + 0.3 * pv[m]
        if not higher_better:
            q = 1.0 - q
        for i in range(n):
            out[i][comp] = round(float(q[i]), 4) if present[i] else 0.5
    return out


def combine(norm: dict[str, float]) -> tuple[float, float]:
    """(sharpness, quality) from normalised components (feed the existing composite formula)."""
    sharpness = float(norm.get("sharp", 0.5))
    quality = (0.30 * norm.get("exposure", 0.5) + 0.20 * norm.get("noise", 0.5)
               + 0.20 * norm.get("motion", 0.5) + 0.30 * norm.get("face", 0.5))
    if "iqa" in norm:
        quality = 0.75 * quality + 0.25 * float(norm["iqa"])
    return round(sharpness, 4), round(float(quality), 4)


# ---------------------------------------------------------------------- optional IQA
TOPIQ_REPO = "nocturna/topiq-nr-koniq-onnx"
TOPIQ_FILE = "model.onnx"
CLIP_IQA_PROMPTS = ("Good photo.", "Bad photo.")


class IqaScorer:
    """Optional no-reference IQA. 'topiq_nr': TOPIQ-NR (CFANet, KonIQ) ONNX from Hugging Face (fixed input
    1x3x384x384 RGB 0..1, ImageNet normalisation inside the graph, ~0.15 s/image on CPU). 'clip_iqa': zero-shot
    CLIP-IQA probe ("Good photo." vs "Bad photo.", softmax at temperature 100) on the existing CLIP embeddings,
    no download. A model that cannot be loaded (offline, missing file) disables the component (`error`)."""

    def __init__(self, settings: Any, classifier: Any = None):
        self.settings = settings
        self.classifier = classifier
        self._sess = None
        self._text: np.ndarray | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def _models_dir(self) -> Path:
        return Path(os.getenv("CP_MODELS_DIR", Path(self.settings.cache_dir) / "models"))

    def _load_topiq(self):
        with self._lock:
            if self._sess is not None or self.error:
                return self._sess
            try:
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download

                path = None
                for d in (self._models_dir(), Path("/app/models")):
                    if d.is_dir():
                        try:
                            path = hf_hub_download(TOPIQ_REPO, TOPIQ_FILE, cache_dir=str(d), local_files_only=True)
                            break
                        except Exception:
                            pass
                if path is None:
                    path = hf_hub_download(TOPIQ_REPO, TOPIQ_FILE, cache_dir=str(self._models_dir()))
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
                self._sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
            except Exception as exc:  # offline / no model: the component is skipped
                self.error = f"TOPIQ unavailable: {exc}"
                self._sess = None
            return self._sess

    def score(
        self,
        mode: str,
        images: list[np.ndarray],
        clip_embeddings: np.ndarray | None = None,
        classifier: Any = None,
    ) -> list[float] | None:
        classifier = classifier if classifier is not None else self.classifier
        if mode == "topiq_nr":
            sess = self._load_topiq()
            if sess is None:
                return None
            name = sess.get_inputs()[0].name
            out: list[float] = []
            for img in images:
                rgb = cv2.resize(img, (384, 384), interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.0
                out.append(float(sess.run(None, {name: rgb.transpose(2, 0, 1)[None]})[0].reshape(-1)[0]))
            return out
        if mode == "clip_iqa":
            if classifier is None or clip_embeddings is None or len(clip_embeddings) != len(images):
                return None
            try:
                if self._text is None:
                    self._text = np.asarray(classifier.embed_texts(list(CLIP_IQA_PROMPTS)), dtype=np.float32)
                logits = 100.0 * np.asarray(clip_embeddings, dtype=np.float32) @ self._text.T
                logits -= logits.max(axis=1, keepdims=True)
                p = np.exp(logits)
                return [float(v) for v in p[:, 0] / p.sum(axis=1)]
            except Exception as exc:
                self.error = f"CLIP-IQA unavailable: {exc}"
                return None
        return None
