"""Phase 0 benchmark spike.

Measures, on this machine (WSL aarch64, CPU only):
  1. PyAV probe of every source video (+ broken-file detection)
  2. Decode throughput from /mnt/c for a chunk: all frames, NONREF-skip, and keyframe-only
  3. Tier-1 metric cost per frame at 512px
  4. CLIP ONNX image-embedding latency (fp32 vs quantized) single + batched
  5. NTFS hardlink support on /mnt/c (for the merged dataset folder)

Usage (inside WSL):
  source scripts/env.sh && uv run python scripts/bench.py [--video PATH] [--chunk-sec 60]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import av
import cv2
import numpy as np

VIDEO_DIR = Path(os.environ.get("CP_VIDEO_DIR", "/mnt/c/Users/Patri/Videos/_girls"))
CACHE_DIR = Path(os.environ.get("CP_CACHE_DIR", Path.home() / ".cache/captioning_pipeline"))
FINISHED_DIR = Path(os.environ.get("CP_FINISHED_DIR", "/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished"))
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

results: dict = {}


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


# --------------------------------------------------------------------------- 1. probe
def probe_all() -> list[dict]:
    section("1. Probe all videos")
    out = []
    t0 = time.perf_counter()
    for p in sorted(VIDEO_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXT:
            continue
        info = {"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
        try:
            with av.open(str(p)) as c:
                s = c.streams.video[0]
                dur = float(c.duration / av.time_base) if c.duration else (
                    float(s.duration * s.time_base) if s.duration else 0.0)
                info.update(codec=s.codec_context.name, w=s.codec_context.width, h=s.codec_context.height,
                            fps=round(float(s.average_rate or 0), 3), duration_s=round(dur, 1))
                # try to decode one frame to catch truncated files
                next(c.decode(video=0))
                info["ok"] = dur > 1.0
        except Exception as e:  # noqa: BLE001
            info["ok"] = False
            info["error"] = f"{type(e).__name__}: {e}"[:160]
        out.append(info)
        flag = "OK " if info.get("ok") else "BAD"
        print(f"  [{flag}] {info.get('codec','?'):>6} {info.get('w','?')}x{info.get('h','?')} "
              f"{info.get('fps','?')}fps {info.get('duration_s','?')}s  {p.name[:70]}"
              + (f"  <- {info['error']}" if 'error' in info else ""))
    results["probe_total_s"] = round(time.perf_counter() - t0, 2)
    results["videos"] = out
    print(f"  probed {len(out)} files in {results['probe_total_s']}s")
    return out


# --------------------------------------------------------------------------- 2. decode
def decode_bench(path: Path, start_s: float, chunk_s: float, sample_fps: float) -> list[np.ndarray]:
    section(f"2. Decode chunk {start_s:.0f}s..{start_s+chunk_s:.0f}s of {path.name[:60]}")
    sampled: list[np.ndarray] = []
    modes = [("all_frames", "DEFAULT"), ("skip_nonref", "NONREF"), ("keyframes_only", "NONKEY")]
    dec = {}
    for label, skip in modes:
        with av.open(str(path)) as c:
            s = c.streams.video[0]
            s.thread_type = "AUTO"
            s.codec_context.skip_frame = skip
            tb = s.time_base
            c.seek(int(start_s / tb), stream=s, backward=True, any_frame=False)
            end_s = start_s + chunk_s
            next_sample = start_s
            n_dec = n_conv = 0
            t0 = time.perf_counter()
            for f in c.decode(s):
                t = float(f.pts * tb) if f.pts is not None else 0.0
                if t < start_s:
                    n_dec += 1
                    continue
                if t > end_s:
                    break
                n_dec += 1
                if label == "all_frames" and t >= next_sample:
                    sampled.append(f.to_ndarray(format="bgr24"))
                    n_conv += 1
                    next_sample += 1.0 / sample_fps
            dt = time.perf_counter() - t0
        dec[label] = {"frames_decoded": n_dec, "seconds": round(dt, 2),
                      "decode_fps": round(n_dec / dt, 1) if dt else None,
                      "realtime_factor": round(chunk_s / dt, 2) if dt else None}
        print(f"  {label:15s}: {n_dec:5d} frames in {dt:6.2f}s -> {dec[label]['decode_fps']} fps, "
              f"{dec[label]['realtime_factor']}x realtime")
    dec["sampled_frames"] = len(sampled)
    results["decode"] = dec
    return sampled


# --------------------------------------------------------------------------- 3. tier-1 metrics
def tier1(img_bgr: np.ndarray) -> dict:
    h, w = img_bgr.shape[:2]
    scale = 512 / max(h, w)
    small = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    gh, gw = gray.shape
    tiles = [lap[y * gh // 4:(y + 1) * gh // 4, x * gw // 4:(x + 1) * gw // 4].var()
             for y in range(4) for x in range(4)]
    tile_sharp = float(np.mean(sorted(tiles)[-3:]))
    mean = float(gray.mean())
    clip = float(((gray <= 2) | (gray >= 253)).mean())
    contrast = float(gray.std())
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    entropy = float(-(p[p > 0] * np.log2(p[p > 0])).sum())
    # 64-bit DCT pHash
    d = cv2.dct(cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32))[:8, :8]
    phash = int("".join("1" if v else "0" for v in (d > np.median(d)).ravel()), 2)
    return {"tile_sharp": tile_sharp, "mean": mean, "clip": clip, "contrast": contrast,
            "entropy": entropy, "phash": phash, "small": small}


def tier1_bench(frames: list[np.ndarray]) -> None:
    section(f"3. Tier-1 metrics on {len(frames)} frames")
    if not frames:
        return
    times = []
    for f in frames:
        t0 = time.perf_counter()
        tier1(f)
        times.append((time.perf_counter() - t0) * 1000)
    results["tier1_ms_per_frame"] = round(statistics.median(times), 2)
    print(f"  median {results['tier1_ms_per_frame']} ms/frame (src {frames[0].shape[1]}x{frames[0].shape[0]})")


# --------------------------------------------------------------------------- 4. CLIP ONNX
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def clip_preprocess(img_bgr: np.ndarray, size: int = 224) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    s = size / min(h, w)
    img = cv2.resize(img_bgr, (round(w * s), round(h * s)), interpolation=cv2.INTER_CUBIC)
    h, w = img.shape[:2]
    y0, x0 = (h - size) // 2, (w - size) // 2
    img = img[y0:y0 + size, x0:x0 + size, ::-1].astype(np.float32) / 255.0
    img = (img - CLIP_MEAN) / CLIP_STD
    return img.transpose(2, 0, 1)


def clip_bench(frames: list[np.ndarray]) -> None:
    section("4. CLIP ONNX image embedding (CPU)")
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download

    print(f"  onnxruntime {ort.__version__}, providers={ort.get_available_providers()}")
    models_dir = CACHE_DIR / "models"
    variants = [
        ("Xenova/clip-vit-base-patch32", "onnx/vision_model.onnx"),
        ("Xenova/clip-vit-base-patch32", "onnx/vision_model_quantized.onnx"),
        ("Xenova/clip-vit-base-patch16", "onnx/vision_model_quantized.onnx"),
    ]
    clip_res = {}
    sample = frames[: min(32, len(frames))] or [np.zeros((720, 1280, 3), np.uint8)]
    batch = np.stack([clip_preprocess(f) for f in sample]).astype(np.float32)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = os.cpu_count() or 4
    for repo, fname in variants:
        key = f"{repo.split('/')[1]}:{Path(fname).stem}"
        try:
            t0 = time.perf_counter()
            path = hf_hub_download(repo, fname, cache_dir=str(models_dir))
            dl = time.perf_counter() - t0
            sess = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
            inp = sess.get_inputs()[0].name
            sess.run(None, {inp: batch[:1]})  # warmup
            singles = []
            for i in range(min(8, len(batch))):
                t0 = time.perf_counter()
                sess.run(None, {inp: batch[i:i + 1]})
                singles.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            sess.run(None, {inp: batch})
            batched = (time.perf_counter() - t0) * 1000 / len(batch)
            clip_res[key] = {"size_mb": round(Path(path).stat().st_size / 1e6, 1),
                             "single_ms": round(statistics.median(singles), 1),
                             "batched_ms_per_img": round(batched, 1),
                             "download_s": round(dl, 1)}
            print(f"  {key:45s} {clip_res[key]['size_mb']:6.1f}MB  single {clip_res[key]['single_ms']:7.1f} ms"
                  f"  batch{len(batch)} {clip_res[key]['batched_ms_per_img']:7.1f} ms/img")
        except Exception as e:  # noqa: BLE001
            clip_res[key] = {"error": f"{type(e).__name__}: {e}"[:200]}
            print(f"  {key}: ERROR {clip_res[key]['error']}")
    results["clip"] = clip_res


# --------------------------------------------------------------------------- 5. hardlinks
def hardlink_bench() -> None:
    section("5. Hardlink support on /mnt/c")
    FINISHED_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FINISHED_DIR) as d:
        a, b = Path(d) / "a.bin", Path(d) / "b.bin"
        a.write_bytes(b"x" * 1024)
        try:
            os.link(a, b)
            ok = b.read_bytes() == a.read_bytes() and os.stat(a).st_nlink >= 2
        except OSError as e:
            ok = False
            print(f"  os.link failed: {e}")
    results["hardlinks_on_mnt_c"] = ok
    print(f"  hardlinks supported: {ok}")


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument("--start-sec", type=float, default=None)
    ap.add_argument("--chunk-sec", type=float, default=60.0)
    ap.add_argument("--sample-fps", type=float, default=4.0)
    ap.add_argument("--skip-clip", action="store_true")
    args = ap.parse_args()

    print(f"python {sys.version.split()[0]}, cpu_count={os.cpu_count()}, av {av.__version__}, cv2 {cv2.__version__}")
    results["cpu_count"] = os.cpu_count()
    vids = probe_all()
    if args.video is None:
        good = [v for v in vids if v.get("ok")]
        good.sort(key=lambda v: v["size_mb"])
        pick = good[len(good) // 2]
        args.video = VIDEO_DIR / pick["name"]
        dur = pick["duration_s"]
    else:
        dur = next((v["duration_s"] for v in vids if v["name"] == args.video.name), 600)
    start = args.start_sec if args.start_sec is not None else max(0.0, dur * 0.4)
    frames = decode_bench(args.video, start, args.chunk_sec, args.sample_fps)
    tier1_bench(frames)
    if not args.skip_clip:
        clip_bench(frames)
    hardlink_bench()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / "bench_results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nresults written to {out}")


if __name__ == "__main__":
    main()
