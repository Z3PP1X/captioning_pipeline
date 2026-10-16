"""Learn-as-you-tag image tagging.

The user marks a region of a frame and names it ("red rope", "chair", "window light"). The crop of that region
is embedded with the CLIP vision model already used for scoring and stored as an exemplar of the tag. Tags are
global, so what was learned on one video is suggested on every other one, like persons for faces.

Suggestions are localised: every frame is embedded as a fixed set of multi-scale windows (cached per video), and
each tag scores every window. The best window is the suggested box. Accepting a suggestion adds a positive
exemplar, rejecting one adds a negative exemplar at that box, so each review step teaches the model.

Per-tag model (few-shot, no training run needed):
- exemplar term: mean of the top-k cosine similarities to the positive exemplars, turned into a probability around
  a threshold calibrated from the exemplars themselves (positive-positive vs positive-negative similarities);
- text prior: CLIP zero-shot similarity to the tag prompt, combined with the exemplar term as a noisy-OR. It
  carries a tag that has no exemplars yet and its weight fades as exemplars accumulate;
- negative suppression: a window closer to a negative exemplar than to any positive one is pushed down.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings
from pipeline.db import DB

WINDOW_CACHE_VERSION = 1
DEFAULT_THRESHOLD = 0.82
MIN_THRESHOLD, MAX_THRESHOLD = 0.70, 0.95
TEXT_CENTER, TEXT_SLOPE = 0.27, 45.0  # CLIP B/32 image-text cosine: ~0.18-0.24 unrelated, ~0.27-0.33 matching; conservative
VIS_SLOPE = 22.0
TOP_K = 3
DEFAULT_SUGGEST_MIN_SCORE = 0.5
CONTEXT_NEGATIVES = 4  # background windows stored as counter-examples per marked region
LOOKALIKE_SIM = 0.90  # a background window this close to the exemplar is another instance, not background
TAG_CATEGORIES = ("object", "clothing", "equipment", "setting", "pose", "lighting", "style", "other")
DEFAULT_COLORS = ("#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2", "#db2777", "#4d7c0f")


def make_windows() -> np.ndarray:
    """Normalised (x, y, w, h) windows: the full frame, a 3x3 grid of half-size windows and a 3x3 grid of thirds."""
    wins = [(0.0, 0.0, 1.0, 1.0)]
    for size, stride in ((0.5, 0.25), (1 / 3, 1 / 3)):
        for j in range(3):
            for i in range(3):
                wins.append((i * stride, j * stride, size, size))
    return np.array(wins, dtype=np.float32)


WINDOWS = make_windows()


def slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[-\s]+", "_", slug)[:64] or "tag"


def clamp_box(box: dict[str, float] | list[float] | tuple[float, ...]) -> tuple[float, float, float, float]:
    """Normalised box clamped into the unit square, with a minimum size of 2% per side."""
    if isinstance(box, dict):
        x, y, w, h = (float(box.get(k, 0.0)) for k in ("x", "y", "w", "h"))
    else:
        x, y, w, h = (float(v) for v in box[:4])
    x = min(max(x, 0.0), 0.98)
    y = min(max(y, 0.0), 0.98)
    w = min(max(w, 0.02), 1.0 - x)
    h = min(max(h, 0.02), 1.0 - y)
    return x, y, w, h


def square_crop(img_bgr: np.ndarray, box: tuple[float, float, float, float], context: float = 0.08) -> np.ndarray:
    """Square crop around a normalised box (plus a little context), padded with black where it leaves the frame.

    CLIP preprocessing centre-crops to a square; feeding it a square avoids cutting off the marked object.
    """
    h, w = img_bgr.shape[:2]
    bx, by, bw, bh = box
    cx, cy = (bx + bw / 2) * w, (by + bh / 2) * h
    side = max(bw * w, bh * h) * (1.0 + context)
    side = max(8.0, side)
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    x1, y1 = x0 + int(round(side)), y0 + int(round(side))
    pad_l, pad_t = max(0, -x0), max(0, -y0)
    pad_r, pad_b = max(0, x1 - w), max(0, y1 - h)
    crop = img_bgr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if crop.size == 0:
        return img_bgr
    if pad_l or pad_t or pad_r or pad_b:
        crop = cv2.copyMakeBorder(crop, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    return crop


def _sigmoid(x: np.ndarray | float) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=np.float32)))


def _topk_mean(sims: np.ndarray, k: int) -> np.ndarray:
    """Row-wise mean of the k largest values of an (M, N) similarity matrix."""
    if sims.shape[1] == 0:
        return np.zeros(sims.shape[0], dtype=np.float32)
    k = max(1, min(k, sims.shape[1]))
    part = np.partition(sims, sims.shape[1] - k, axis=1)[:, -k:]
    return part.mean(axis=1)


@dataclass
class TagModel:
    tag_id: int
    name: str
    color: str | None
    positives: np.ndarray  # (P, 512) unit vectors
    negatives: np.ndarray  # (N, 512)
    text: np.ndarray | None  # (512,)
    threshold: float = DEFAULT_THRESHOLD

    def score(self, emb: np.ndarray) -> np.ndarray:
        """Probability-like score per row of `emb` (M, 512)."""
        n_pos = len(self.positives)
        txt_prob = _sigmoid((emb @ self.text - TEXT_CENTER) * TEXT_SLOPE) if self.text is not None else None
        if n_pos:
            vis = _topk_mean(emb @ self.positives.T, TOP_K)
            vis_prob = _sigmoid((vis - self.threshold) * VIS_SLOPE)
            # Noisy-OR: the exemplars alone can carry a suggestion; the text prior adds evidence with a weight
            # that fades as exemplars accumulate (1 exemplar: at most +0.5, so text alone never suggests)
            text_w = 1.0 / (1.0 + n_pos)
            score = vis_prob if txt_prob is None else 1.0 - (1.0 - vis_prob) * (1.0 - text_w * txt_prob)
        else:
            vis = np.full(len(emb), self.threshold, dtype=np.float32)
            score = txt_prob if txt_prob is not None else np.zeros(len(emb), dtype=np.float32)
        if len(self.negatives):
            neg = _topk_mean(emb @ self.negatives.T, TOP_K)
            # 1 while the window is at least as close to the positives as to the negatives, then falls to 0
            score = score * np.minimum(1.0, 2.0 * _sigmoid((vis - neg) * 25.0))
        return score.astype(np.float32)


@dataclass
class TagStats:
    threshold: float
    accuracy: float | None
    pos_count: int
    neg_count: int
    extra: dict[str, Any] = field(default_factory=dict)


def calibrate(positives: np.ndarray, negatives: np.ndarray) -> TagStats:
    """Threshold between "looks like the other positives" and "looks like the negatives", from the exemplars.

    Leave-one-out: each positive is compared with the other positives, each negative with all positives. The
    accuracy of the resulting threshold on those same comparisons is reported to the user as how well the tag
    has been learned so far (None until there are enough exemplars to say).
    """
    p, n = len(positives), len(negatives)
    if p < 2:
        return TagStats(DEFAULT_THRESHOLD, None, p, n)
    pp = positives @ positives.T
    np.fill_diagonal(pp, -np.inf)
    pos_scores = _topk_mean(pp, min(TOP_K, p - 1))  # the -inf diagonal is never among the top p-1
    if n:
        neg_scores = _topk_mean(negatives @ positives.T, TOP_K)
        lo, hi = float(np.percentile(neg_scores, 80)), float(np.percentile(pos_scores, 20))
        thr = (lo + hi) / 2 if hi > lo else hi - 0.01
        correct = int((pos_scores >= thr).sum() + (neg_scores < thr).sum())
        accuracy = correct / float(p + n) if p + n >= 4 else None
    else:
        thr = float(np.percentile(pos_scores, 20)) - 0.03
        accuracy = None
    # Few exemplars give a noisy (often too tight) threshold: shrink it toward the default until more arrive
    weight = p / (p + 3.0)
    thr = weight * thr + (1.0 - weight) * DEFAULT_THRESHOLD
    thr = float(min(MAX_THRESHOLD, max(MIN_THRESHOLD, thr)))
    return TagStats(round(thr, 4), None if accuracy is None else round(accuracy, 3), p, n)


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return (v / n).astype(np.float32) if n > 1e-6 else v.astype(np.float32)


def refine_box(windows: np.ndarray, scores: np.ndarray) -> tuple[float, float, float, float]:
    """Score-weighted union of the windows close to the best one (better than snapping to a grid cell)."""
    best = int(np.argmax(scores))
    bx, by, bw, bh = windows[best]
    keep = scores >= 0.9 * scores[best]
    sel = windows[keep]
    w = scores[keep]
    # only windows that overlap the best one take part
    ov = (sel[:, 0] < bx + bw) & (sel[:, 0] + sel[:, 2] > bx) & (sel[:, 1] < by + bh) & (sel[:, 1] + sel[:, 3] > by)
    sel, w = sel[ov], w[ov]
    if best == 0 or len(sel) <= 1:
        return tuple(float(v) for v in windows[best])  # type: ignore[return-value]
    # prefer the smaller windows: the full frame matching only means "somewhere in the image"
    small = sel[:, 2] < 0.99
    if small.any():
        sel, w = sel[small], w[small]
    x0 = float(np.average(sel[:, 0], weights=w))
    y0 = float(np.average(sel[:, 1], weights=w))
    x1 = float(np.average(sel[:, 0] + sel[:, 2], weights=w))
    y1 = float(np.average(sel[:, 1] + sel[:, 3], weights=w))
    return clamp_box((x0, y0, x1 - x0, y1 - y0))


class ImageTagEngine:
    """Tag store, exemplar learning and localised suggestions."""

    def __init__(self, db: DB, settings: Settings, classifier: ClipClassifier):
        self.db = db
        self.settings = settings
        self.classifier = classifier
        self.windows = WINDOWS
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._text_cache: dict[str, np.ndarray] = {}
        # All tag models, rebuilt after any change to tags or exemplars (`_invalidate`)
        self._models: list[TagModel] | None = None
        self._models_lock = threading.Lock()
        self._mem_cache: dict[str, tuple[int, dict[int, np.ndarray]]] = {}

    def _invalidate(self) -> None:
        with self._models_lock:
            self._models = None

    # ------------------------------------------------------------------ paths
    @property
    def window_dir(self) -> Path:
        return self.settings.cache_dir / "tag_windows"

    @property
    def crop_dir(self) -> Path:
        return self.settings.cache_dir / "tag_regions"

    def _video_lock(self, video_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(video_id, threading.Lock())

    # ------------------------------------------------------------------ tags
    def list_tags(self) -> list[dict[str, Any]]:
        rows = self.db.all(
            """
            SELECT t.*,
                   (SELECT COUNT(*) FROM tag_regions r WHERE r.tag_id=t.id AND r.polarity='positive') AS positive_count,
                   (SELECT COUNT(*) FROM tag_regions r WHERE r.tag_id=t.id AND r.polarity='negative'
                     AND r.source<>'context') AS negative_count,
                   (SELECT COUNT(*) FROM tag_regions r WHERE r.tag_id=t.id AND r.source='context') AS context_count,
                   (SELECT COUNT(*) FROM frame_tags f WHERE f.tag_id=t.id AND f.status='confirmed') AS confirmed_count,
                   (SELECT COUNT(*) FROM frame_tags f WHERE f.tag_id=t.id AND f.status='suggested') AS suggested_count,
                   (SELECT r.id FROM tag_regions r WHERE r.tag_id=t.id AND r.polarity='positive'
                     ORDER BY r.created_at DESC LIMIT 1) AS cover_region_id
            FROM image_tags t
            ORDER BY t.category ASC, t.name ASC
            """
        )
        return [self._public_tag(r) for r in rows]

    def get_tag(self, tag_id: int) -> dict[str, Any] | None:
        for t in self.list_tags():
            if t["id"] == tag_id:
                return t
        return None

    @staticmethod
    def _public_tag(r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        r["prompt"] = r.get("prompt") or f"a photo of {r['name']}"
        r["threshold"] = r.get("threshold") if r.get("threshold") is not None else DEFAULT_THRESHOLD
        r["cover_url"] = f"/api/tag-regions/{r['cover_region_id']}/crop" if r.get("cover_region_id") else None
        return r

    def create_tag(self, name: str, category: str = "object", prompt: str | None = None, color: str | None = None) -> dict:
        clean = name.strip()
        if not clean:
            raise ValueError("Tag name cannot be empty")
        slug = slugify(clean)
        existing = self.db.one("SELECT id FROM image_tags WHERE slug=? OR name=?", (slug, clean))
        if existing:
            return self.get_tag(existing["id"])  # type: ignore[return-value]
        category = category if category in TAG_CATEGORIES else "other"
        now = time.time()
        with self.db.write() as c:
            count = c.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0]
            cur = c.execute(
                "INSERT INTO image_tags (name, slug, category, prompt, color, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (clean, slug, category, (prompt or "").strip() or None, color or DEFAULT_COLORS[count % len(DEFAULT_COLORS)], now, now),
            )
            tag_id = cur.lastrowid
        self._invalidate()
        return self.get_tag(tag_id)  # type: ignore[return-value]

    def update_tag(self, tag_id: int, **fields: Any) -> dict | None:
        if not self.db.one("SELECT id FROM image_tags WHERE id=?", (tag_id,)):
            return None
        sets, args = [], []
        if fields.get("name") is not None and fields["name"].strip():
            name = fields["name"].strip()
            clash = self.db.one("SELECT id FROM image_tags WHERE (name=? OR slug=?) AND id<>?", (name, slugify(name), tag_id))
            if clash:
                raise ValueError(f"A tag named '{name}' already exists")
            sets += ["name=?", "slug=?"]
            args += [name, slugify(name)]
        if fields.get("category") is not None:
            sets.append("category=?")
            args.append(fields["category"] if fields["category"] in TAG_CATEGORIES else "other")
        if fields.get("prompt") is not None:
            sets.append("prompt=?")
            args.append(fields["prompt"].strip() or None)
        if fields.get("color") is not None:
            sets.append("color=?")
            args.append(fields["color"])
        if sets:
            sets.append("updated_at=?")
            args.append(time.time())
            with self.db.write() as c:
                c.execute(f"UPDATE image_tags SET {', '.join(sets)} WHERE id=?", (*args, tag_id))
            self._invalidate()
        return self.get_tag(tag_id)

    def delete_tag(self, tag_id: int) -> bool:
        regions = self.db.all("SELECT crop_path FROM tag_regions WHERE tag_id=?", (tag_id,))
        with self.db.write() as c:
            cur = c.execute("DELETE FROM image_tags WHERE id=?", (tag_id,))
        self._invalidate()
        for r in regions:
            if r.get("crop_path"):
                Path(r["crop_path"]).unlink(missing_ok=True)
        return cur.rowcount > 0

    def merge_tags(self, source_id: int, target_id: int) -> dict | None:
        """Move all exemplars and frame tags of `source` into `target`, then delete `source`."""
        if source_id == target_id or not self.db.one("SELECT id FROM image_tags WHERE id=?", (target_id,)):
            return None
        with self.db.write() as c:
            c.execute("UPDATE tag_regions SET tag_id=? WHERE tag_id=?", (target_id, source_id))
            c.execute(
                """
                INSERT INTO frame_tags (frame_id, tag_id, status, score, box, updated_at)
                SELECT frame_id, ?, status, score, box, updated_at FROM frame_tags WHERE tag_id=?
                ON CONFLICT(frame_id, tag_id) DO UPDATE SET
                    status = CASE WHEN frame_tags.status='confirmed' OR excluded.status='confirmed' THEN 'confirmed'
                                  ELSE excluded.status END
                """,
                (target_id, source_id),
            )
            c.execute("DELETE FROM image_tags WHERE id=?", (source_id,))
        self.recalibrate(target_id)
        return self.get_tag(target_id)

    # ------------------------------------------------------------------ models
    def _text_embedding(self, prompt: str) -> np.ndarray | None:
        if prompt not in self._text_cache:
            try:
                self._text_cache[prompt] = _unit(self.classifier.embed_texts([prompt])[0])
            except Exception:
                return None  # text model unavailable (offline): exemplars still work
        return self._text_cache[prompt]

    def load_models(self, tag_ids: list[int] | None = None) -> list[TagModel]:
        with self._models_lock:
            models = self._models
        if models is None:
            models = self._build_models()
            with self._models_lock:
                self._models = models
        if tag_ids is not None:
            wanted = set(tag_ids)
            return [m for m in models if m.tag_id in wanted]
        return list(models)

    def _build_models(self) -> list[TagModel]:
        tags = self.db.all("SELECT id, name, prompt, color, threshold FROM image_tags")
        regions = self.db.all("SELECT tag_id, polarity, embedding FROM tag_regions")
        by_tag: dict[int, dict[str, list[np.ndarray]]] = {}
        for r in regions:
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            by_tag.setdefault(r["tag_id"], {"positive": [], "negative": []})[
                "negative" if r["polarity"] == "negative" else "positive"
            ].append(vec)
        models = []
        for t in tags:
            ex = by_tag.get(t["id"], {"positive": [], "negative": []})
            pos = np.stack(ex["positive"]) if ex["positive"] else np.zeros((0, 512), dtype=np.float32)
            neg = np.stack(ex["negative"]) if ex["negative"] else np.zeros((0, 512), dtype=np.float32)
            models.append(TagModel(
                tag_id=t["id"],
                name=t["name"],
                color=t.get("color"),
                positives=pos,
                negatives=neg,
                text=self._text_embedding(t.get("prompt") or f"a photo of {t['name']}"),
                threshold=float(t["threshold"]) if t.get("threshold") is not None else DEFAULT_THRESHOLD,
            ))
        return models

    def recalibrate(self, tag_id: int) -> TagStats:
        rows = self.db.all("SELECT polarity, embedding FROM tag_regions WHERE tag_id=?", (tag_id,))
        pos = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows if r["polarity"] != "negative"]
        neg = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows if r["polarity"] == "negative"]
        stats = calibrate(
            np.stack(pos) if pos else np.zeros((0, 512), dtype=np.float32),
            np.stack(neg) if neg else np.zeros((0, 512), dtype=np.float32),
        )
        with self.db.write() as c:
            c.execute(
                "UPDATE image_tags SET threshold=?, accuracy=?, updated_at=? WHERE id=?",
                (stats.threshold, stats.accuracy, time.time(), tag_id),
            )
        self._invalidate()
        return stats

    # ------------------------------------------------------------------ images & embeddings
    def _frame(self, frame_id: int) -> dict[str, Any]:
        f = self.db.one("SELECT id, video_id, preview_path, full_path FROM frames WHERE id=?", (frame_id,))
        if not f:
            raise LookupError(f"Frame {frame_id} not found")
        return f

    @staticmethod
    def _read_image(frame: dict[str, Any], prefer_full: bool = False) -> np.ndarray:
        order = ("full_path", "preview_path") if prefer_full else ("preview_path", "full_path")
        for key in order:
            p = frame.get(key)
            if p and Path(p).is_file():
                img = cv2.imread(str(p))
                if img is not None:
                    return img
        raise FileNotFoundError(f"No image on disk for frame {frame['id']}")

    def embed_region(self, img_bgr: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
        """Exemplar embedding: the crop and its mirror image, averaged (a cheap augmentation)."""
        crop = square_crop(img_bgr, box)
        embs = self.classifier.embed_images([crop, cv2.flip(crop, 1)])
        return _unit(embs.mean(axis=0))

    def embed_windows(self, img_bgr: np.ndarray) -> np.ndarray:
        crops = [square_crop(img_bgr, tuple(w), context=0.0) for w in self.windows]
        return self.classifier.embed_images(crops).astype(np.float32)

    def _cache_path(self, video_id: str) -> Path:
        safe = re.sub(r"[^\w.-]", "_", video_id)
        return self.window_dir / f"{safe}.npz"

    def _load_cache(self, video_id: str) -> dict[int, np.ndarray]:
        """Window embeddings of a video (frame id -> (W, 512)); kept in memory while the file is unchanged."""
        p = self._cache_path(video_id)
        if not p.is_file():
            return {}
        mtime = p.stat().st_mtime_ns
        hit = self._mem_cache.get(video_id)
        if hit and hit[0] == mtime:
            return dict(hit[1])
        try:
            data = np.load(p)
            if int(data["version"]) != WINDOW_CACHE_VERSION or data["embs"].shape[1] != len(self.windows):
                return {}
            cache = {int(fid): data["embs"][i].astype(np.float32) for i, fid in enumerate(data["frame_ids"])}
        except Exception:
            return {}
        self._mem_cache = {video_id: (mtime, cache)}  # one video at a time: the one being curated
        return dict(cache)

    def _save_cache(self, video_id: str, cache: dict[int, np.ndarray]) -> None:
        self.window_dir.mkdir(parents=True, exist_ok=True)
        p = self._cache_path(video_id)
        tmp = p.with_name(p.stem + ".tmp.npz")
        ids = np.array(sorted(cache), dtype=np.int64)
        embs = np.stack([cache[i] for i in ids]).astype(np.float16) if len(ids) else np.zeros((0, len(self.windows), 512), np.float16)
        np.savez(tmp, version=WINDOW_CACHE_VERSION, frame_ids=ids, embs=embs)
        tmp.replace(p)

    def _merge_save(self, video_id: str, fresh: dict[int, np.ndarray]) -> dict[int, np.ndarray]:
        """Add new window embeddings to the on-disk cache (re-read under the lock, so concurrent writers merge)."""
        with self._video_lock(video_id):
            cache = self._load_cache(video_id)
            cache.update(fresh)
            self._save_cache(video_id, cache)
            return cache

    def frame_windows(self, frame_id: int) -> np.ndarray:
        """Window embeddings of one frame, from the video cache or computed (and cached) now."""
        frame = self._frame(frame_id)
        with self._video_lock(frame["video_id"]):
            cached = self._load_cache(frame["video_id"]).get(frame_id)
        if cached is not None:
            return cached
        embs = self.embed_windows(self._read_image(frame))
        self._merge_save(frame["video_id"], {frame_id: embs})
        return embs

    # ------------------------------------------------------------------ scoring
    def _score_frame(self, models: list[TagModel], embs: np.ndarray) -> list[dict[str, Any]]:
        out = []
        for m in models:
            scores = m.score(embs)
            best = float(scores.max()) if len(scores) else 0.0
            box = refine_box(self.windows, scores)
            out.append({"tag_id": m.tag_id, "name": m.name, "color": m.color, "score": round(best, 4),
                        "box": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}})
        out.sort(key=lambda s: s["score"], reverse=True)
        return out

    def min_score(self) -> float:
        stored = self.db.get_setting("user_settings", {}) or {}
        try:
            return float(stored.get("tag_suggest_min_score", DEFAULT_SUGGEST_MIN_SCORE))
        except (TypeError, ValueError):
            return DEFAULT_SUGGEST_MIN_SCORE

    def frame_tags(self, frame_id: int, compute: bool = True) -> dict[str, Any]:
        """Confirmed and rejected tags of a frame plus live suggestions for every other tag."""
        frame = self._frame(frame_id)
        rows = self.db.all(
            """
            SELECT ft.tag_id, ft.status, ft.score, ft.box, t.name, t.color, t.category
            FROM frame_tags ft JOIN image_tags t ON t.id=ft.tag_id WHERE ft.frame_id=?
            """,
            (frame_id,),
        )
        regions = self.db.all(
            """
            SELECT r.id, r.tag_id, r.polarity, r.source, r.x, r.y, r.w, r.h, t.name, t.color
            FROM tag_regions r JOIN image_tags t ON t.id=r.tag_id WHERE r.frame_id=? AND r.source<>'context'
            ORDER BY r.created_at ASC
            """,
            (frame_id,),
        )
        decided = {r["tag_id"]: r for r in rows if r["status"] in ("confirmed", "rejected")}
        suggestions: list[dict[str, Any]] = []
        if compute:
            models = [m for m in self.load_models() if m.tag_id not in decided]
            if models:
                try:
                    embs = self.frame_windows(frame_id)
                    floor = self.min_score()
                    suggestions = [s for s in self._score_frame(models, embs) if s["score"] >= floor]
                except FileNotFoundError:
                    suggestions = []
        else:
            suggestions = [
                {"tag_id": r["tag_id"], "name": r["name"], "color": r["color"], "score": r["score"], "box": r["box"]}
                for r in rows if r["status"] == "suggested"
            ]
        return {
            "frame_id": frame_id,
            "video_id": frame["video_id"],
            "confirmed": [self._tag_row(r) for r in rows if r["status"] == "confirmed"],
            "rejected": [self._tag_row(r) for r in rows if r["status"] == "rejected"],
            "suggestions": suggestions,
            "regions": [
                {**r, "box": {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]},
                 "crop_url": f"/api/tag-regions/{r['id']}/crop"}
                for r in regions
            ],
        }

    @staticmethod
    def _tag_row(r: dict[str, Any]) -> dict[str, Any]:
        return {"tag_id": r["tag_id"], "name": r["name"], "color": r["color"], "category": r.get("category"),
                "score": r["score"], "box": r["box"]}

    # ------------------------------------------------------------------ learning from the user
    def add_region(
        self,
        frame_id: int,
        box: dict[str, float] | list[float],
        tag_id: int | None = None,
        tag_name: str | None = None,
        polarity: str = "positive",
        source: str = "manual",
        category: str = "object",
    ) -> dict[str, Any]:
        """Store a marked region as an exemplar, set the frame's tag status and recalibrate the tag."""
        if polarity not in ("positive", "negative"):
            raise ValueError("polarity must be 'positive' or 'negative'")
        frame = self._frame(frame_id)
        if tag_id is None:
            if not tag_name or not tag_name.strip():
                raise ValueError("Provide tag_id or tag_name")
            tag_id = self.create_tag(tag_name, category=category)["id"]
        elif not self.db.one("SELECT id FROM image_tags WHERE id=?", (tag_id,)):
            raise LookupError(f"Tag {tag_id} not found")
        nbox = clamp_box(box)
        img = self._read_image(frame, prefer_full=True)
        emb = self.embed_region(img, nbox)
        now = time.time()
        with self.db.write() as c:
            cur = c.execute(
                """
                INSERT INTO tag_regions (tag_id, frame_id, video_id, x, y, w, h, polarity, source, embedding, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (tag_id, frame_id, frame["video_id"], *nbox, polarity, source, emb.tobytes(), now),
            )
            region_id = cur.lastrowid
            status = "confirmed" if polarity == "positive" else "rejected"
            box_json = '{"x": %f, "y": %f, "w": %f, "h": %f}' % nbox
            if polarity == "positive":
                c.execute(
                    """
                    INSERT INTO frame_tags (frame_id, tag_id, status, score, box, updated_at) VALUES (?,?,?,1.0,?,?)
                    ON CONFLICT(frame_id, tag_id) DO UPDATE SET status='confirmed', score=1.0, box=excluded.box,
                        updated_at=excluded.updated_at
                    """,
                    (frame_id, tag_id, status, box_json, now),
                )
            else:
                # A negative exemplar on a frame that has the tag elsewhere (a confirmed region) keeps it confirmed
                c.execute(
                    """
                    INSERT INTO frame_tags (frame_id, tag_id, status, score, box, updated_at) VALUES (?,?,?,0.0,?,?)
                    ON CONFLICT(frame_id, tag_id) DO UPDATE SET
                        status = CASE WHEN frame_tags.status='confirmed' THEN 'confirmed' ELSE 'rejected' END,
                        updated_at=excluded.updated_at
                    """,
                    (frame_id, tag_id, status, box_json, now),
                )
        self._save_crop(region_id, img, nbox)
        if polarity == "positive":
            self._add_context_negatives(region_id, tag_id, frame, nbox, emb)
        stats = self.recalibrate(tag_id)
        return {"region_id": region_id, "tag_id": tag_id, "frame_id": frame_id, "video_id": frame["video_id"],
                "threshold": stats.threshold, "accuracy": stats.accuracy}

    def _add_context_negatives(
        self,
        region_id: int,
        tag_id: int,
        frame: dict[str, Any],
        box: tuple[float, float, float, float],
        emb: np.ndarray,
        limit: int = CONTEXT_NEGATIVES,
    ) -> int:
        """The rest of the frame around a marked region: implicit counter-examples ("this is not the tag").

        Without them a single exemplar also matches its own background, because CLIP crops of one scene look
        alike. Windows that barely overlap the box and do not look like the exemplar (a second instance of the
        same object) are stored as negatives linked to the region, so they disappear with it.
        """
        try:
            wins = self.frame_windows(frame["id"])
        except (FileNotFoundError, LookupError):
            return 0
        bx, by, bw, bh = box
        box_area = max(1e-6, bw * bh)
        candidates = []
        for i, (wx, wy, ww, wh) in enumerate(self.windows):
            ix = max(0.0, min(bx + bw, wx + ww) - max(bx, wx))
            iy = max(0.0, min(by + bh, wy + wh) - max(by, wy))
            inter = ix * iy
            if inter / box_area > 0.25 or inter / (ww * wh) > 0.10:
                continue
            sim = float(wins[i] @ emb)
            if sim >= LOOKALIKE_SIM:
                continue
            candidates.append((sim, i))
        # the most confusable background windows teach the most
        candidates.sort(reverse=True)
        now = time.time()
        rows = []
        for _sim, i in candidates[:limit]:
            wx, wy, ww, wh = (float(v) for v in self.windows[i])
            rows.append((tag_id, frame["id"], frame["video_id"], wx, wy, ww, wh, region_id,
                         _unit(wins[i]).tobytes(), now))
        if rows:
            with self.db.write() as c:
                c.executemany(
                    """
                    INSERT INTO tag_regions (tag_id, frame_id, video_id, x, y, w, h, polarity, source, parent_id,
                                             embedding, created_at)
                    VALUES (?,?,?,?,?,?,?,'negative','context',?,?,?)
                    """,
                    rows,
                )
        return len(rows)

    def _save_crop(self, region_id: int, img: np.ndarray, box: tuple[float, float, float, float]) -> None:
        self.crop_dir.mkdir(parents=True, exist_ok=True)
        crop = square_crop(img, box, context=0.15)
        side = max(crop.shape[:2])
        if side > 256:
            crop = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA)
        path = self.crop_dir / f"region_{region_id}.jpg"
        cv2.imwrite(str(path), crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        with self.db.write() as c:
            c.execute("UPDATE tag_regions SET crop_path=? WHERE id=?", (str(path), region_id))

    def accept_suggestion(self, frame_id: int, tag_id: int, box: dict[str, float] | None = None) -> dict[str, Any]:
        """Confirm a suggested tag. The (possibly adjusted) suggestion box becomes a positive exemplar."""
        box = box or self._suggested_box(frame_id, tag_id)
        return self.add_region(frame_id, box, tag_id=tag_id, polarity="positive", source="accepted")

    def reject_suggestion(self, frame_id: int, tag_id: int, box: dict[str, float] | None = None) -> dict[str, Any]:
        """A wrong suggestion: its box becomes a negative exemplar and the frame is marked as not having the tag."""
        box = box or self._suggested_box(frame_id, tag_id)
        return self.add_region(frame_id, box, tag_id=tag_id, polarity="negative", source="rejected")

    def _suggested_box(self, frame_id: int, tag_id: int) -> dict[str, float]:
        row = self.db.one("SELECT box FROM frame_tags WHERE frame_id=? AND tag_id=?", (frame_id, tag_id))
        if row and isinstance(row.get("box"), dict):
            return row["box"]
        models = self.load_models([tag_id])
        if not models:
            raise LookupError(f"Tag {tag_id} not found")
        return self._score_frame(models, self.frame_windows(frame_id))[0]["box"]

    def remove_frame_tag(self, frame_id: int, tag_id: int) -> bool:
        """Un-tag a frame: its decision and the exemplars marked on this frame for the tag are dropped."""
        regions = self.db.all("SELECT crop_path FROM tag_regions WHERE frame_id=? AND tag_id=?", (frame_id, tag_id))
        with self.db.write() as c:
            c.execute("DELETE FROM tag_regions WHERE frame_id=? AND tag_id=?", (frame_id, tag_id))
            cur = c.execute("DELETE FROM frame_tags WHERE frame_id=? AND tag_id=?", (frame_id, tag_id))
        for r in regions:
            if r.get("crop_path"):
                Path(r["crop_path"]).unlink(missing_ok=True)
        self.recalibrate(tag_id)
        return cur.rowcount > 0 or bool(regions)

    def list_regions(self, tag_id: int) -> list[dict[str, Any]]:
        rows = self.db.all(
            """
            SELECT r.id, r.tag_id, r.frame_id, r.video_id, r.x, r.y, r.w, r.h, r.polarity, r.source, r.created_at,
                   v.name AS video_name
            FROM tag_regions r LEFT JOIN videos v ON v.id=r.video_id
            WHERE r.tag_id=? AND r.source<>'context' ORDER BY r.created_at DESC
            """,
            (tag_id,),
        )
        for r in rows:
            r["crop_url"] = f"/api/tag-regions/{r['id']}/crop"
            r["box"] = {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}
        return rows

    def delete_region(self, region_id: int) -> bool:
        r = self.db.one("SELECT id, tag_id, frame_id, polarity, crop_path FROM tag_regions WHERE id=?", (region_id,))
        if not r:
            return False
        with self.db.write() as c:
            c.execute("DELETE FROM tag_regions WHERE id=?", (region_id,))
            if r["frame_id"] is not None:
                left = c.execute(
                    "SELECT COUNT(*) FROM tag_regions WHERE frame_id=? AND tag_id=? AND polarity=? AND source<>'context'",
                    (r["frame_id"], r["tag_id"], r["polarity"]),
                ).fetchone()[0]
                if not left:
                    c.execute("DELETE FROM frame_tags WHERE frame_id=? AND tag_id=?", (r["frame_id"], r["tag_id"]))
        if r.get("crop_path"):
            Path(r["crop_path"]).unlink(missing_ok=True)
        self.recalibrate(r["tag_id"])
        return True

    # ------------------------------------------------------------------ video-wide suggestions
    def suggest_for_video(
        self,
        video_id: str,
        on_progress: Callable[[float, str], None] | None = None,
        check_cancel: Callable[[], None] | None = None,
        compute_missing: bool = True,
        frame_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Score every frame of a video against every tag and store the suggestions.

        `compute_missing=False` only re-scores frames whose window embeddings are cached: it is the cheap pass that
        runs after every accept/reject so the whole video reflects what the model just learned.
        """
        models = self.load_models()
        frames = self.db.all("SELECT id, video_id, preview_path, full_path FROM frames WHERE video_id=? ORDER BY t", (video_id,))
        if frame_ids is not None:
            wanted = set(frame_ids)
            frames = [f for f in frames if f["id"] in wanted]
        if not models or not frames:
            return {"video_id": video_id, "frames": len(frames), "scored": 0, "suggested": 0, "tags": len(models)}

        # The lock is held only to read and to merge-write the cache: embedding a whole video takes minutes and
        # must not block the lightbox, which embeds single frames of the same video on demand
        with self._video_lock(video_id):
            cache = self._load_cache(video_id)
        fresh: dict[int, np.ndarray] = {}
        total = len(frames)
        for i, f in enumerate(frames):
            if check_cancel:
                check_cancel()
            if f["id"] in cache or not compute_missing:
                continue
            try:
                fresh[f["id"]] = self.embed_windows(self._read_image(f))
            except FileNotFoundError:
                continue
            if on_progress and (i % 10 == 0 or i == total - 1):
                on_progress(0.9 * (i + 1) / total, f"Embedding regions {i + 1}/{total}")
            if len(fresh) >= 100:
                cache = self._merge_save(video_id, fresh)
                fresh = {}
        if fresh:
            cache = self._merge_save(video_id, fresh)

        if on_progress:
            on_progress(0.92, "Scoring tags")
        scored_ids = [f["id"] for f in frames if f["id"] in cache]
        decided = {
            (r["frame_id"], r["tag_id"])
            for r in self.db.all(
                "SELECT ft.frame_id, ft.tag_id FROM frame_tags ft JOIN frames f ON f.id=ft.frame_id "
                "WHERE f.video_id=? AND ft.status IN ('confirmed','rejected')",
                (video_id,),
            )
        }
        floor = self.min_score()
        now = time.time()
        rows: list[tuple] = []
        if scored_ids:
            stack = np.stack([cache[fid] for fid in scored_ids])  # (F, W, 512)
            flat = stack.reshape(-1, stack.shape[-1])
            n_win = stack.shape[1]
            for m in models:
                scores = m.score(flat).reshape(len(scored_ids), n_win)
                best = scores.max(axis=1)
                for idx in np.nonzero(best >= floor)[0]:
                    fid = scored_ids[int(idx)]
                    if (fid, m.tag_id) in decided:
                        continue
                    bx = refine_box(self.windows, scores[idx])
                    rows.append((fid, m.tag_id, round(float(best[idx]), 4),
                                 '{"x": %f, "y": %f, "w": %f, "h": %f}' % bx, now))
        with self.db.write() as c:
            if scored_ids:
                c.execute(
                    f"DELETE FROM frame_tags WHERE status='suggested' AND frame_id IN ({','.join('?' * len(scored_ids))})",
                    scored_ids,
                )
            c.executemany(
                "INSERT OR IGNORE INTO frame_tags (frame_id, tag_id, status, score, box, updated_at) "
                "VALUES (?, ?, 'suggested', ?, ?, ?)",
                rows,
            )
        if on_progress:
            on_progress(1.0, f"{len(rows)} tag suggestions")
        return {"video_id": video_id, "frames": len(frames), "scored": len(scored_ids), "suggested": len(rows),
                "tags": len(models)}

    def uncached_frame_count(self, video_id: str) -> int:
        """Frames of the video whose region windows are not embedded yet (the cheap re-score skips them)."""
        ids = {r["id"] for r in self.db.all("SELECT id FROM frames WHERE video_id=?", (video_id,))}
        if not ids:
            return 0
        with self._video_lock(video_id):
            cached = set(self._load_cache(video_id))
        return len(ids - cached)

    def video_tag_map(self, video_id: str) -> dict[int, list[dict[str, Any]]]:
        """frame id -> tags (confirmed and suggested), for the curation grid."""
        rows = self.db.all(
            """
            SELECT ft.frame_id, ft.tag_id, ft.status, ft.score, t.name, t.color
            FROM frame_tags ft JOIN frames f ON f.id=ft.frame_id JOIN image_tags t ON t.id=ft.tag_id
            WHERE f.video_id=? AND ft.status IN ('confirmed','suggested')
            ORDER BY ft.status ASC, ft.score DESC
            """,
            (video_id,),
        )
        out: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["frame_id"], []).append(
                {"tag_id": r["tag_id"], "name": r["name"], "color": r["color"], "status": r["status"],
                 "score": r["score"]}
            )
        return out

    def video_summary(self, video_id: str) -> list[dict[str, Any]]:
        return self.db.all(
            """
            SELECT t.id AS tag_id, t.name, t.color, t.category,
                   SUM(CASE WHEN ft.status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
                   SUM(CASE WHEN ft.status='suggested' THEN 1 ELSE 0 END) AS suggested,
                   SUM(CASE WHEN ft.status='rejected' THEN 1 ELSE 0 END) AS rejected
            FROM frame_tags ft JOIN frames f ON f.id=ft.frame_id JOIN image_tags t ON t.id=ft.tag_id
            WHERE f.video_id=? GROUP BY t.id ORDER BY confirmed DESC, suggested DESC, t.name ASC
            """,
            (video_id,),
        )

    def confirmed_tag_names(self, frame_id: int) -> list[str]:
        rows = self.db.all(
            "SELECT t.name FROM frame_tags ft JOIN image_tags t ON t.id=ft.tag_id "
            "WHERE ft.frame_id=? AND ft.status='confirmed' ORDER BY t.name",
            (frame_id,),
        )
        return [r["name"] for r in rows]
