"""Per-video analysis cache (Phase F2, review §3.3/§4 Stage A): decode a video once, reuse it on re-extraction.

`<cache>/analysis/<video_id>.npz` holds every decoded candidate sample of the video (JPEG q92 at the sampling
size, before letterbox/watermark crop) with its signals - pts, Laplacian variance, motion energy, luma
mean/std, clipping, dHash - plus the sampling coverage (which time ranges were sampled at which interval and
mode) and per-kind embeddings (CLIP, DINOv2) computed on the cropped samples. The `frame_features` table holds
the same signals per sampled timestamp together with the face summary, the quality-v2 gates/components and
references to the npz embedding arrays, so the UI and selection can explain a frame without decoding.

Re-extraction (`AnalysisCache.sample`) only decodes the parts of a chunk that the cache does not cover at the
requested density; covered parts are served from the npz by re-running the window-peak choice over the cached
samples. Samples are always returned JPEG round-tripped, so a cache hit and a fresh decode give identical images
(and identical metrics). The cache is dropped (npz deleted, `frame_features` rows removed) when the video file's
size or mtime changes, and ignored entirely when the video file does not exist.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from pipeline.decode import pick_window_peak
from pipeline.metrics import compute_dhash

ANALYSIS_VERSION = 1
JPEG_QUALITY = 92
_T_TOL = 0.0015  # timestamps are rounded to 1 ms everywhere

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _video_lock(video_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(video_id, threading.Lock())


def video_signature(path: Path | str | None) -> dict[str, Any] | None:
    """Size + mtime of the video file (None when it does not exist: no caching then)."""
    if not path:
        return None
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    return {"size": int(st.st_size), "mtime": round(float(st.st_mtime), 3)}


def crop_signature(letterbox: Any, watermark_rect: Any) -> str:
    """Embeddings are computed on the cropped sample: a crop change invalidates them (not the samples)."""
    return json.dumps([letterbox or None, watermark_rect or None], sort_keys=True)


def sampling_interval(start_s: float, end_s: float, sample_fps: float, max_samples: int) -> float:
    """The window length `decode.sample_chunk_frames` uses for this request (both modes)."""
    dur = max(0.0, end_s - start_s)
    if max_samples > 0 and dur * sample_fps > max_samples:
        return dur / max_samples
    return 1.0 / max(0.2, sample_fps)


def _encode(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


def _decode(data: bytes) -> np.ndarray | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR) if arr.size else None


def sample_signals(img_bgr: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return {
        "lap": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
        "luma_mean": float(gray.mean()),
        "luma_std": float(gray.std()),
        "clipping": float(((gray <= 2) | (gray >= 253)).mean()),
    }


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1e-6:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _gaps(start: float, end: float, covered: list[tuple[float, float]], min_gap: float = 1e-3) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    cur = start
    for a, b in _merge_ranges(covered):
        if b <= cur or a >= end:
            continue
        if a > cur + min_gap:
            gaps.append((cur, min(a, end)))
        cur = max(cur, b)
        if cur >= end:
            break
    if end > cur + min_gap:
        gaps.append((cur, end))
    return gaps


class AnalysisCache:
    """The analysis artefact of one video. Load once per job, mutate, then `save()`."""

    def __init__(self, cache_dir: Path, video_id: str, video_path: Path | str | None):
        self.video_id = video_id
        self.dir = Path(cache_dir) / "analysis"
        self.path = self.dir / f"{video_id}.npz"
        self.signature = video_signature(video_path)
        self.enabled = self.signature is not None
        self.invalidated = False
        self.dirty = False
        self.stats = {"served": 0, "decoded": 0, "decoded_ranges": 0, "decoded_s": 0.0}
        self._clear()
        if self.enabled:
            self._load()

    # ------------------------------------------------------------------ storage
    def _clear(self) -> None:
        self.t = np.zeros(0, dtype=np.float64)
        self.sig = {k: np.zeros(0, dtype=np.float32) for k in ("lap", "motion", "luma_mean", "luma_std", "clipping")}
        self.dhash = np.zeros(0, dtype=np.uint64)
        self.jpegs: list[bytes] = []
        self.coverage: list[dict[str, Any]] = []
        # kind -> {"model": str, "crop": str, "t": array, "vec": array (n x d float32)}
        self.embeddings: dict[str, dict[str, Any]] = {}

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with np.load(self.path, allow_pickle=False) as z:
                meta = json.loads(bytes(z["meta"]).decode("utf-8"))
                if meta.get("version") != ANALYSIS_VERSION or meta.get("signature") != self.signature:
                    self.invalidated = True
                    return
                self.t = z["t"].astype(np.float64)
                for k in self.sig:
                    self.sig[k] = z[f"sig_{k}"].astype(np.float32)
                self.dhash = z["dhash"].astype(np.uint64)
                offsets = z["jpeg_offsets"]
                data = z["jpeg_data"].tobytes()
                self.jpegs = [data[int(offsets[i]):int(offsets[i + 1])] for i in range(len(offsets) - 1)]
                self.coverage = list(meta.get("coverage") or [])
                for kind, info in (meta.get("embeddings") or {}).items():
                    self.embeddings[kind] = {
                        "model": info.get("model"),
                        "crop": info.get("crop"),
                        "t": z[f"emb_{kind}_t"].astype(np.float64),
                        "vec": z[f"emb_{kind}"].astype(np.float32),
                    }
        except Exception:
            self._clear()
            self.invalidated = True

    def save(self) -> bool:
        """Atomic write (tmp + rename). No-op when disabled or nothing changed."""
        if not self.enabled or not self.dirty:
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        order = np.argsort(self.t, kind="stable")
        jpegs = [self.jpegs[i] for i in order]
        offsets = np.zeros(len(jpegs) + 1, dtype=np.int64)
        if jpegs:
            offsets[1:] = np.cumsum([len(b) for b in jpegs])
        meta = {
            "version": ANALYSIS_VERSION,
            "video_id": self.video_id,
            "signature": self.signature,
            "coverage": self.coverage,
            "embeddings": {k: {"model": v["model"], "crop": v["crop"]} for k, v in self.embeddings.items()},
            "saved_at": time.time(),
        }
        arrays: dict[str, np.ndarray] = {
            "meta": np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8),
            "t": self.t[order],
            "dhash": self.dhash[order],
            "jpeg_offsets": offsets,
            "jpeg_data": np.frombuffer(b"".join(jpegs), dtype=np.uint8),
        }
        for k, v in self.sig.items():
            arrays[f"sig_{k}"] = v[order]
        for kind, e in self.embeddings.items():
            arrays[f"emb_{kind}_t"] = e["t"]
            arrays[f"emb_{kind}"] = e["vec"]
        tmp = self.path.with_suffix(".tmp.npz")
        with open(tmp, "wb") as fh:
            np.savez(fh, **arrays)
        os.replace(tmp, self.path)
        self.dirty = False
        return True

    def invalidate_files(self, db: Any = None) -> None:
        """Delete the npz (and the video's frame_features rows when `db` is given)."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        if db is not None:
            with db.write() as conn:
                conn.execute("DELETE FROM frame_features WHERE video_id=?", (self.video_id,))

    # ------------------------------------------------------------------ samples
    def _sample_index(self, start: float, end: float) -> np.ndarray:
        return np.flatnonzero((self.t >= start - 1e-6) & (self.t <= end + 1e-6))

    def _covered(self, interval: float, mode: str, max_dim: int) -> list[tuple[float, float]]:
        return [
            (float(c["start"]), float(c["end"]))
            for c in self.coverage
            if c.get("mode") == mode and int(c.get("max_dim", 0)) == int(max_dim)
            and float(c.get("interval", 1e9)) <= interval * 1.05 + 1e-9
        ]

    def _replace_range(self, a: float, b: float, new: list[dict[str, Any]]) -> None:
        keep = ~((self.t >= a - 1e-6) & (self.t <= b + 1e-6))
        idx = np.flatnonzero(keep)
        self.t = np.concatenate([self.t[idx], np.array([n["t"] for n in new], dtype=np.float64)])
        for k in self.sig:
            vals = np.array([n.get(k, np.nan) if n.get(k) is not None else np.nan for n in new], dtype=np.float32)
            self.sig[k] = np.concatenate([self.sig[k][idx], vals])
        self.dhash = np.concatenate([self.dhash[idx], np.array([n["dhash"] for n in new], dtype=np.uint64)])
        self.jpegs = [self.jpegs[i] for i in idx] + [n["jpeg"] for n in new]
        self.dirty = True

    def _cut_coverage(self, a: float, b: float) -> None:
        """The samples in [a, b] were replaced: no older record (any mode/interval) may still claim them."""
        out: list[dict[str, Any]] = []
        for c in self.coverage:
            cs, ce = float(c["start"]), float(c["end"])
            if ce <= a or cs >= b:
                out.append(c)
                continue
            if cs < a - 1e-3:
                out.append({**c, "end": round(a, 3)})
            if ce > b + 1e-3:
                out.append({**c, "start": round(b, 3)})
        self.coverage = out

    def sample(
        self,
        sampler: Callable[..., list[tuple[float, np.ndarray]]],
        vpath: Path,
        start_s: float,
        end_s: float,
        sample_fps: float = 4.0,
        max_samples: int = 120,
        max_dim: int = 720,
        window_peak: int = 0,
        motion_weight: float = 0.5,
        stats_out: dict[str, Any] | None = None,
    ) -> list[tuple[float, np.ndarray]]:
        """Same contract as `decode.sample_chunk_frames`, decoding only what the cache does not cover."""
        interval = sampling_interval(start_s, end_s, sample_fps, max_samples)
        mode = "window_peak" if window_peak and window_peak >= 2 else "uniform"
        gaps = _gaps(start_s, end_s, self._covered(interval, mode, max_dim)) if end_s > start_s else []
        subsamples = 0
        for ga, gb in gaps:
            kwargs: dict[str, Any] = {
                "sample_fps": 1.0 / interval,
                "max_samples": max(1, int(np.ceil((gb - ga) / interval)) + 1),
                "max_dim": max_dim,
            }
            gap_stats: dict[str, Any] = {}
            if mode == "window_peak":
                kwargs.update(window_peak=int(window_peak), motion_weight=motion_weight, stats_out=gap_stats)
            raw = sampler(vpath, ga, gb, **kwargs)
            motion = gap_stats.get("motion") or {}
            subsamples += int(gap_stats.get("subsamples", len(raw)))
            new: list[dict[str, Any]] = []
            for t, img in raw:  # noqa: VEC001 gap raw sample iteration
                t = round(float(t), 3)
                if not (ga - 1e-6 <= t <= gb + 1e-6):
                    continue
                jpeg = _encode(img)
                rt = _decode(jpeg)
                entry = {"t": t, "jpeg": jpeg, "motion": motion.get(t), "dhash": int(compute_dhash(rt), 16)}
                entry.update(sample_signals(rt))
                new.append(entry)
            self._replace_range(ga, gb, new)
            self._cut_coverage(ga, gb)
            self.coverage.append({"start": round(ga, 3), "end": round(gb, 3), "interval": interval,
                                  "mode": mode, "max_dim": int(max_dim), "oversample": int(window_peak or 0)})
            self.stats["decoded"] += len(new)
            self.stats["decoded_ranges"] += 1
            self.stats["decoded_s"] = round(self.stats["decoded_s"] + (gb - ga), 3)

        # Serve [start, end] from the cache: best sample per window (window-peak) or the first (uniform)
        idx = self._sample_index(start_s, end_s)
        windows: dict[int, list[dict[str, Any]]] = {}
        for i in idx:
            w = min(int((self.t[i] - start_s) / interval) if interval > 0 else 0, max(0, max_samples - 1))
            mot = self.sig["motion"][i]
            windows.setdefault(w, []).append({
                "i": int(i), "t": round(float(self.t[i]), 3), "sharp": float(self.sig["lap"][i]),
                "motion": None if np.isnan(mot) else float(mot),
            })
        out: list[tuple[float, np.ndarray]] = []
        motion_by_t: dict[float, float] = {}
        for w in sorted(windows):
            members = sorted(windows[w], key=lambda m: m["t"])
            best = pick_window_peak(members, motion_weight) if mode == "window_peak" else members[0]
            img = _decode(self.jpegs[best["i"]])
            if img is None:
                continue
            out.append((best["t"], img))
            if best.get("motion") is not None:
                motion_by_t[best["t"]] = round(float(best["motion"]), 3)
        decoded_now = sum(1 for _ in gaps)
        self.stats["served"] += len(out)
        if stats_out is not None:
            stats_out.update({
                "mode": mode,
                "windows": len(out),
                "subsamples": subsamples if decoded_now else len(idx),
                "window_s": round(interval, 3),
                "motion": motion_by_t,
                "cache_decoded_ranges": decoded_now,
            })
        return out

    def signals_at(self, t: float) -> dict[str, Any] | None:
        if not self.t.size:
            return None
        i = int(np.argmin(np.abs(self.t - t)))
        if abs(self.t[i] - t) > _T_TOL:
            return None
        out = {k: (None if np.isnan(v[i]) else round(float(v[i]), 4)) for k, v in self.sig.items()}
        out["dhash"] = f"{int(self.dhash[i]):016x}"
        return out

    # ------------------------------------------------------------------ embeddings
    def get_embeddings(self, kind: str, model: str, crop: str, ts: list[float]) -> dict[int, np.ndarray]:
        """Cached vectors for the given timestamps: {index into ts: vector}."""
        e = self.embeddings.get(kind)
        if not e or e["model"] != model or e["crop"] != crop or not e["t"].size:
            return {}
        out: dict[int, np.ndarray] = {}
        for j, t in enumerate(ts):
            i = int(np.argmin(np.abs(e["t"] - t)))
            if abs(e["t"][i] - t) <= _T_TOL:
                out[j] = e["vec"][i]
        return out

    def put_embeddings(self, kind: str, model: str, crop: str, ts: list[float], vecs: np.ndarray) -> None:
        if not self.enabled or len(ts) == 0:
            return
        vecs = np.asarray(vecs, dtype=np.float32)
        e = self.embeddings.get(kind)
        if not e or e["model"] != model or e["crop"] != crop or (e["vec"].size and e["vec"].shape[1] != vecs.shape[1]):
            e = {"model": model, "crop": crop, "t": np.zeros(0), "vec": np.zeros((0, vecs.shape[1]), dtype=np.float32)}
        new_t = np.round(np.asarray(ts, dtype=np.float64), 3)
        keep = np.array([np.min(np.abs(new_t - t)) > _T_TOL for t in e["t"]], dtype=bool) if e["t"].size else \
            np.zeros(0, dtype=bool)
        e["t"] = np.concatenate([e["t"][keep], new_t])
        e["vec"] = np.concatenate([e["vec"][keep], vecs])
        self.embeddings[kind] = e
        self.dirty = True


def open_analysis_cache(db: Any, cache_dir: Path, video_id: str, video_path: Path | str | None) -> AnalysisCache:
    """Load the cache; a stale one (video changed) is deleted together with its frame_features rows."""
    with _video_lock(video_id):
        cache = AnalysisCache(cache_dir, video_id, video_path)
        if cache.invalidated:
            cache.invalidate_files(db)
            cache.invalidated = True  # keep the flag for the caller's stats
        return cache


# ---------------------------------------------------------------------- frame_features
FEATURE_COLUMNS = ("lap_var", "motion", "mean_luma", "luma_std", "clipping", "dhash", "face_count",
                   "face_summary", "emb_refs", "gates", "q_components")
_JSON_FEATURES = {"face_summary", "emb_refs", "gates", "q_components"}


def write_frame_features(db: Any, video_id: str, rows: list[dict[str, Any]]) -> int:
    """Upsert per-timestamp rows; a None value keeps what is stored (COALESCE), so partial updates merge."""
    if not rows:
        return 0
    now = time.time()
    cols = ", ".join(FEATURE_COLUMNS)
    ph = ", ".join("?" for _ in FEATURE_COLUMNS)
    updates = ", ".join(f"{c}=COALESCE(excluded.{c}, frame_features.{c})" for c in FEATURE_COLUMNS)
    sql = (f"INSERT INTO frame_features (video_id, t, {cols}, updated_at) VALUES (?, ?, {ph}, ?) "
           f"ON CONFLICT(video_id, t) DO UPDATE SET {updates}, updated_at=excluded.updated_at")
    params = []
    for r in rows:
        vals = []
        for c in FEATURE_COLUMNS:
            v = r.get(c)
            vals.append(json.dumps(v) if (c in _JSON_FEATURES and v is not None) else v)
        params.append((video_id, round(float(r["t"]), 3), *vals, now))
    with db.write() as conn:
        conn.executemany(sql, params)
    return len(params)


def face_summary(faces: list[dict[str, Any]] | None, img_shape: tuple[int, ...] | None,
                 native_scale: float = 1.0) -> dict[str, Any]:
    """Compact face info: count, largest face short side in native px, its area share, boxes (sample px)."""
    faces = faces or []
    out: dict[str, Any] = {"count": len(faces), "boxes": [f.get("box") for f in faces][:8]}
    if faces and img_shape:
        h, w = img_shape[:2]
        big = max(faces, key=lambda f: (f.get("box") or [0, 0, 0, 0])[2] * (f.get("box") or [0, 0, 0, 0])[3])
        bx = big.get("box") or [0, 0, 0, 0]
        out["max_short_side_px"] = round(min(bx[2], bx[3]) * native_scale, 1)
        out["max_area_frac"] = round(bx[2] * bx[3] / max(1.0, float(w * h)), 4)
    return out
