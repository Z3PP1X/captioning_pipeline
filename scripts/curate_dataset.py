"""Postprocessing curation: Stratified sampling and near-duplicate elimination for 2500 dataset."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


def phash_hamming_distance(h1: str | None, h2: str | None) -> int:
    """Compute Hamming distance between two 16-char hex pHash strings."""
    if not h1 or not h2:
        return 999
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        return bin(v1 ^ v2).count("1")
    except Exception:
        return 999


def parse_embedding(blob: bytes | None) -> np.ndarray | None:
    """Parse BLOB embedding into normalized numpy array."""
    if not blob:
        return None
    try:
        arr = np.frombuffer(blob, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 1e-6:
            return arr / norm
        return arr
    except Exception:
        return None


def compute_composite_quality(metrics_raw: str | dict | None, zs_raw: str | dict | None) -> float:
    """Compute normalized composite quality score in [0.0, 1.0]."""
    metrics = {}
    if isinstance(metrics_raw, str):
        try:
            metrics = json.loads(metrics_raw)
        except Exception:
            pass
    elif isinstance(metrics_raw, dict):
        metrics = metrics_raw

    zs = {}
    if isinstance(zs_raw, str):
        try:
            zs = json.loads(zs_raw)
        except Exception:
            pass
    elif isinstance(zs_raw, dict):
        zs = zs_raw

    # If composite already computed in zs_scores
    if "composite" in zs and isinstance(zs["composite"], (int, float)):
        return float(zs["composite"])

    # Fallback to computing from Tier-1 metrics
    tile_sharp = float(metrics.get("tile_sharp", 0.0))
    sharp_norm = min(1.0, max(0.0, tile_sharp / 400.0))

    contrast = float(metrics.get("contrast", 0.0))
    contrast_norm = min(1.0, max(0.0, contrast / 60.0))

    clipping = float(metrics.get("clipping", 0.0))
    clipping_penalty = max(0.0, 1.0 - clipping * 8.0)

    entropy = float(metrics.get("entropy", 0.0))
    entropy_norm = min(1.0, max(0.0, entropy / 7.5))

    luma = float(metrics.get("mean_luma", 128.0))
    luma_score = max(0.0, 1.0 - abs(luma - 128.0) / 120.0)

    return round(
        0.40 * sharp_norm + 0.25 * contrast_norm + 0.15 * clipping_penalty + 0.10 * entropy_norm + 0.10 * luma_score,
        4,
    )


def load_dataset_candidates(db_path: Path, min_caption_words: int = 15) -> list[dict[str, Any]]:
    """Load all exported frames from SQLite with metrics, embeddings, and validated files."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = """
        SELECT f.id, f.video_id, f.chunk_id, f.t, f.metrics, f.phash, f.embedding, f.zs_scores,
               e.per_video_path, v.slug as video_slug, v.name as video_name
        FROM exports e
        JOIN frames f ON e.frame_id = f.id
        JOIN videos v ON f.video_id = v.id
        ORDER BY f.video_id, f.chunk_id, f.t ASC
    """
    c.execute(query)
    rows = c.fetchall()

    candidates: list[dict[str, Any]] = []
    missing_files = 0
    short_captions = 0

    for r in rows:
        img_path = Path(r["per_video_path"])
        txt_path = img_path.with_suffix(".txt")

        if not img_path.is_file() or not txt_path.is_file():
            missing_files += 1
            continue

        try:
            caption_text = txt_path.read_text(encoding="utf-8").strip()
            word_count = len(caption_text.split())
            if word_count < min_caption_words:
                short_captions += 1
                continue
        except Exception:
            short_captions += 1
            continue

        q_score = compute_composite_quality(r["metrics"], r["zs_scores"])
        emb = parse_embedding(r["embedding"])

        candidates.append({
            "frame_id": r["id"],
            "video_id": r["video_id"],
            "video_slug": r["video_slug"],
            "video_name": r["video_name"],
            "chunk_id": r["chunk_id"],
            "t": float(r["t"]),
            "phash": r["phash"],
            "embedding": emb,
            "quality": q_score,
            "img_path": img_path,
            "txt_path": txt_path,
            "caption": caption_text,
            "word_count": word_count,
            "metrics_raw": r["metrics"],
            "zs_raw": r["zs_scores"],
        })

    conn.close()
    print(f"Loaded {len(candidates)} valid candidates (Missing files: {missing_files}, Short captions: {short_captions})")
    return candidates


def run_stratified_dedup(
    candidates: list[dict[str, Any]],
    target_total: int = 2500,
    min_time_dist: float = 3.0,
    min_phash_dist: int = 6,
    max_clip_sim: float = 0.92,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Deduplicates candidates and selects stratified best 2500 frames."""
    # 1. Group by video
    vids: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        vids.setdefault(c["video_id"], []).append(c)

    num_videos = len(vids)
    if num_videos == 0:
        return [], {}

    # Sort each candidate pool by quality descending
    for vid, pool in vids.items():
        pool.sort(key=lambda x: x["quality"], reverse=True)

    # 2. Determine target quota per video
    # Proportional with min/max caps to ensure good diversity across all videos
    total_avail = len(candidates)
    video_targets: dict[str, int] = {}
    allocated = 0

    base_quota = target_total // num_videos
    for vid, pool in vids.items():
        # Target proportional to candidate count, smoothed with base_quota
        ratio = len(pool) / total_avail
        target = int(round(0.5 * base_quota + 0.5 * (target_total * ratio)))
        # Bounded between 30 and 150 (or pool size)
        target = max(20, min(len(pool), target))
        video_targets[vid] = target
        allocated += target

    # Balance any rounding differences
    diff = target_total - allocated
    sorted_vids_by_pool = sorted(vids.keys(), key=lambda v: len(vids[v]), reverse=True)
    idx = 0
    while diff != 0 and idx < len(sorted_vids_by_pool):
        vid = sorted_vids_by_pool[idx % len(sorted_vids_by_pool)]
        if diff > 0 and video_targets[vid] < len(vids[vid]):
            video_targets[vid] += 1
            diff -= 1
        elif diff < 0 and video_targets[vid] > 20:
            video_targets[vid] -= 1
            diff += 1
        idx += 1

    # 3. Greedy Deduplication selection per Video
    accepted_by_video: dict[str, list[dict[str, Any]]] = {vid: [] for vid in vids}
    rejected_stats = {
        "temporal_close": 0,
        "phash_near_dupe": 0,
        "clip_semantic_dupe": 0,
        "quota_reached": 0,
    }

    # Helper: Check if candidate is near duplicate of any already accepted frame in that video
    def is_duplicate(cand: dict[str, Any], accepted_list: list[dict[str, Any]]) -> tuple[bool, str | None]:
        if not accepted_list:
            return False, None

        # 1. Temporal distance in seconds (scalar check)
        cand_t = cand["t"]
        for acc in accepted_list:
            if abs(cand_t - acc["t"]) < min_time_dist:
                return True, "temporal_close"

        # 2. Perceptual Hash distance
        cand_ph = cand["phash"]
        for acc in accepted_list:
            if phash_hamming_distance(cand_ph, acc["phash"]) <= min_phash_dist:
                return True, "phash_near_dupe"

        # 3. CLIP embedding cosine similarity (vectorized BLAS batch dot product)
        if cand["embedding"] is not None:
            acc_embs = [acc["embedding"] for acc in accepted_list if acc["embedding"] is not None]
            if acc_embs:
                embs_mat = np.stack(acc_embs, axis=0)  # Shape (N, 512)
                sims = np.dot(embs_mat, cand["embedding"])
                if float(np.max(sims)) >= max_clip_sim:
                    return True, "clip_semantic_dupe"

        return False, None

    # First pass: Stratified by Chunk within each video to spread out across scenes
    for vid, pool in vids.items():
        quota = video_targets[vid]
        chunks: dict[int, list[dict[str, Any]]] = {}
        for c in pool:
            chunks.setdefault(c["chunk_id"], []).append(c)

        # Round-robin selection across chunks
        chunk_keys = list(chunks.keys())
        active_chunks = set(chunk_keys)
        chunk_ptrs = {cid: 0 for cid in chunk_keys}

        while len(accepted_by_video[vid]) < quota and active_chunks:
            added_in_round = False
            for cid in list(active_chunks):
                c_list = chunks[cid]
                ptr = chunk_ptrs[cid]
                picked = False

                while ptr < len(c_list):
                    cand = c_list[ptr]
                    ptr += 1
                    chunk_ptrs[cid] = ptr

                    dup, reason = is_duplicate(cand, accepted_by_video[vid])
                    if dup:
                        rejected_stats[reason] += 1
                    else:
                        accepted_by_video[vid].append(cand)
                        picked = True
                        added_in_round = True
                        break

                if ptr >= len(c_list):
                    active_chunks.remove(cid)

                if len(accepted_by_video[vid]) >= quota:
                    break

            if not added_in_round:
                break

    # 4. Fill remaining deficit globally from any video with diverse survivors
    all_accepted = [item for sub in accepted_by_video.values() for item in sub]
    current_count = len(all_accepted)
    deficit = target_total - current_count

    if deficit > 0:
        print(f"Initial stratified pass selected {current_count}/{target_total} frames. Filling remaining {deficit} slots...")
        # Gather all unaccepted candidates
        accepted_ids = {c["frame_id"] for c in all_accepted}
        remaining_pool = [c for c in candidates if c["frame_id"] not in accepted_ids]
        remaining_pool.sort(key=lambda x: x["quality"], reverse=True)

        for cand in remaining_pool:
            if len(all_accepted) >= target_total:
                break
            vid = cand["video_id"]
            dup, reason = is_duplicate(cand, accepted_by_video[vid])
            if not dup:
                accepted_by_video[vid].append(cand)
                all_accepted.append(cand)
            else:
                rejected_stats[reason] += 1

    # Final tally
    final_picks = [item for sub in accepted_by_video.values() for item in sub]
    final_picks.sort(key=lambda x: (x["video_slug"], x["t"]))

    stats = {
        "target_total": target_total,
        "selected_total": len(final_picks),
        "total_candidates": len(candidates),
        "rejected_stats": rejected_stats,
        "video_distribution": {
            vids[vid][0]["video_slug"]: len(picks) for vid, picks in accepted_by_video.items() if picks
        },
        "mean_quality": float(np.mean([p["quality"] for p in final_picks])) if final_picks else 0.0,
        "mean_words": float(np.mean([p["word_count"] for p in final_picks])) if final_picks else 0.0,
    }

    return final_picks, stats


def perform_train_val_split(
    picks: list[dict[str, Any]],
    val_ratio: float = 0.10,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stratified train/val split ensuring each video is represented in val."""
    rng = np.random.default_rng(seed)
    train: list[dict[str, Any]] = []
    val: list[dict[str, Any]] = []

    # Group by video
    vids: dict[str, list[dict[str, Any]]] = {}
    for p in picks:
        vids.setdefault(p["video_id"], []).append(p)

    for vid, pool in vids.items():
        n = len(pool)
        n_val = max(1, int(round(n * val_ratio))) if n >= 5 else 0
        indices = rng.permutation(n)
        val_indices = set(indices[:n_val])

        for idx, item in enumerate(pool):
            if idx in val_indices:
                val.append(item)
            else:
                train.append(item)

    return train, val


def export_dataset(
    train: list[dict[str, Any]],
    val: list[dict[str, Any]],
    out_dir: Path,
    copy_mode: str = "link",
) -> dict[str, int]:
    """Export PNG and TXT files to out_dir/train and out_dir/val with manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    train_dir = out_dir / "train"
    val_dir = out_dir / "val"
    train_dir.mkdir(parents=True, exist_ok=True)
    val_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "dataset_manifest.jsonl"
    exported_counts = {"train": 0, "val": 0, "errors": 0}

    with open(manifest_path, "w", encoding="utf-8") as manifest_f:
        for split_name, items, dest_dir in [("train", train, train_dir), ("val", val, val_dir)]:
            for item in items:
                src_img = item["img_path"]
                src_txt = item["txt_path"]

                dst_img = dest_dir / src_img.name
                dst_txt = dest_dir / src_txt.name

                try:
                    # Remove existing if any
                    if dst_img.exists():
                        dst_img.unlink()
                    if dst_txt.exists():
                        dst_txt.unlink()

                    if copy_mode == "link":
                        try:
                            os.link(src_img, dst_img)
                            os.link(src_txt, dst_txt)
                        except OSError:
                            shutil.copy2(src_img, dst_img)
                            shutil.copy2(src_txt, dst_txt)
                    else:
                        shutil.copy2(src_img, dst_img)
                        shutil.copy2(src_txt, dst_txt)

                    # Manifest record
                    record = {
                        "split": split_name,
                        "filename": src_img.name,
                        "caption_filename": src_txt.name,
                        "video_slug": item["video_slug"],
                        "timestamp_s": item["t"],
                        "quality_score": item["quality"],
                        "word_count": item["word_count"],
                        "caption": item["caption"],
                        "phash": item["phash"],
                    }
                    manifest_f.write(json.dumps(record) + "\n")
                    exported_counts[split_name] += 1

                except Exception as e:
                    print(f"Error exporting {src_img.name}: {e}", file=sys.stderr)
                    exported_counts["errors"] += 1

    return exported_counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Stratified deduplication & postprocessing for caption dataset.")
    parser.add_argument("--target-total", type=int, default=2500, help="Total target frame count (default: 2500)")
    parser.add_argument("--min-time-dist", type=float, default=3.0, help="Min time delta in seconds (default: 3.0s)")
    parser.add_argument("--min-phash-dist", type=int, default=6, help="Min pHash Hamming distance (default: 6)")
    parser.add_argument("--max-clip-sim", type=float, default=0.92, help="Max CLIP cosine similarity (default: 0.92)")
    parser.add_argument("--val-ratio", type=float, default=0.10, help="Validation split ratio (default: 0.10 -> 250/2250)")
    parser.add_argument("--min-caption-words", type=int, default=15, help="Min caption words filter (default: 15)")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path.home() / ".cache" / "captioning_pipeline" / "state.db",
        help="Path to SQLite state.db",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/curated_2500"),
        help="Target output directory",
    )
    parser.add_argument("--dry-run", action="store_true", help="Analyze and print statistics without writing files")
    parser.add_argument("--copy-mode", choices=["link", "copy"], default="link", help="Use hardlinks (link) or copy")

    args = parser.parse_args()

    print("=" * 70)
    print("DATASET CURATION & STRATIFIED DEDUPLICATION")
    print(f"Target size: {args.target_total} | Val Ratio: {args.val_ratio:.1%}")
    print(f"Constraints: Time >= {args.min_time_dist}s | pHash Dist > {args.min_phash_dist} | CLIP Sim < {args.max_clip_sim}")
    print(f"Dry Run: {args.dry_run}")
    print("=" * 70)

    t0 = time.time()
    candidates = load_dataset_candidates(args.db_path, min_caption_words=args.min_caption_words)
    picks, stats = run_stratified_dedup(
        candidates,
        target_total=args.target_total,
        min_time_dist=args.min_time_dist,
        min_phash_dist=args.min_phash_dist,
        max_clip_sim=args.max_clip_sim,
    )

    train, val = perform_train_val_split(picks, val_ratio=args.val_ratio)
    t_elapsed = time.time() - t0

    print("\n" + "=" * 70)
    print(f"SELECTION SUMMARY (Computed in {t_elapsed:.2f}s)")
    print("=" * 70)
    print(f"Total Candidates:   {stats['total_candidates']}")
    print(f"Total Selected:     {stats['selected_total']} (Target: {stats['target_total']})")
    print(f"  - Train Split:    {len(train)}")
    print(f"  - Val Split:      {len(val)}")
    print(f"Mean Quality Score: {stats['mean_quality']:.3f} / 1.000")
    print(f"Mean Caption Words: {stats['mean_words']:.1f} words")
    print("\nRejected Duplicates Breakdown:")
    for reason, count in stats["rejected_stats"].items():
        print(f"  - {reason:20s}: {count}")

    print("\nPer-Video Distribution:")
    for slug, count in sorted(stats["video_distribution"].items(), key=lambda x: x[1], reverse=True):
        print(f"  - {slug[:55]:55s}: {count:3d} frames")

    if args.dry_run:
        print("\n[DRY RUN COMPLETE] No files were copied or modified.")
        return

    print(f"\nExporting dataset to: {args.out_dir} (Mode: {args.copy_mode})...")
    res = export_dataset(train, val, args.out_dir, copy_mode=args.copy_mode)
    print(f"Done! Exported Train: {res['train']}, Val: {res['val']}, Errors: {res['errors']}")
    print(f"Manifest saved to: {args.out_dir / 'dataset_manifest.jsonl'}")


if __name__ == "__main__":
    main()
