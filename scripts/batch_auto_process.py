#!/usr/bin/env python3
"""Batch autonomous processing runner for selected videos."""
import argparse
import sys
import time
from pathlib import Path

from pipeline.autoprocess import AutoProcessor
from pipeline.config import get_settings
from pipeline.db import DB
from pipeline.indexer import Indexer


def main():
    parser = argparse.ArgumentParser(description="Auto-process selected videos into 100-200 tagged HQ frames.")
    parser.add_argument("videos", nargs="*", help="Video IDs, slugs, or partial filenames to process")
    parser.add_argument("--file", "-f", type=str, help="Path to text file containing video titles or IDs (one per line)")
    parser.add_argument("--target", type=int, default=150, help="Target frame count per video (default: 150)")
    parser.add_argument("--rescan", action="store_true", help="Rescan video directory before running")
    parser.add_argument("--force", action="store_true", help="Force reprocess even if video already exported")
    args = parser.parse_args()

    settings = get_settings()
    db = DB(settings.db_path)
    indexer = Indexer(db, settings)

    if args.rescan:
        print("🔄 Rescanning video directory...")
        indexer.scan_and_sync()

    all_db_videos = db.all("SELECT id, name, slug, status, duration FROM videos")
    matched_videos = []

    queries = list(args.videos)
    if args.file:
        fpath = Path(args.file)
        if fpath.is_file():
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        queries.append(stripped)
        else:
            print(f"❌ Error: File not found: {args.file}")
            sys.exit(1)

    if not queries:
        print("No video titles provided. Please specify one or more video titles, IDs, or use --file.")
        sys.exit(1)

    for query in queries:
        q_lower = query.strip().lower()
        found = False
        for v in all_db_videos:
            if q_lower == v["id"].lower() or q_lower in v["name"].lower() or q_lower in v["slug"].lower():
                if v not in matched_videos:
                    matched_videos.append(v)
                found = True
                break
        if not found:
            print(f"⚠️ Warning: Could not match video query: '{query}'")

    if not matched_videos:
        print("❌ No matching videos found.")
        sys.exit(1)

    print(f"\n🚀 Starting batch auto-processing for {len(matched_videos)} video(s):")
    for idx, v in enumerate(matched_videos, 1):
        print(f"  {idx}. [{v['id']}] {v['name']} ({v['duration']:.1f}s)")

    processor = AutoProcessor(db, settings)
    total_exported = 0
    start_time = time.time()

    for idx, v in enumerate(matched_videos, 1):
        cur_v = db.one("SELECT status FROM videos WHERE id=?", (v["id"],))
        if cur_v and cur_v["status"] == "exported" and not args.force:
            print(f"\n" + "=" * 80)
            print(f"⏭️ [{idx}/{len(matched_videos)}] Skipping: {v['name']} (already exported). Use --force to reprocess.")
            print("=" * 80)
            continue

        print(f"\n" + "=" * 80)
        print(f"▶ [{idx}/{len(matched_videos)}] Processing: {v['name']}")
        print("=" * 80)

        def progress_cb(pct: float, step: str):
            bar_len = 25
            filled = int(bar_len * pct)
            bar = "█" * filled + "░" * (bar_len - filled)
            pct_str = f"{int(pct * 100):3d}%"
            if sys.stdout.isatty():
                print(f"\r  [{bar}] {pct_str} | {step:<60}", end="", flush=True)
            else:
                print(f"  [{bar}] {pct_str} | {step}", flush=True)

        try:
            res = processor.process_video(v["id"], target_frames=args.target, on_progress=progress_cb)
            print()
            print(f"  ✅ SUCCESS: {res['exported_frames']} frames exported across {res['chunks_count']} scenes in {res['elapsed_s']:.1f}s")
            total_exported += res["exported_frames"]
        except Exception as e:
            print()
            print(f"  ❌ ERROR processing {v['name']}: {e}")

    total_elapsed = round(time.time() - start_time, 1)
    print("\n" + "=" * 80)
    print(f"🎉 BATCH COMPLETE: {total_exported} HQ frames exported across {len(matched_videos)} video(s) in {total_elapsed}s")
    print(f"📁 Output directories:")
    print(f"   - Per-video: {settings.finished_dir}/per_video/")
    print(f"   - Merged:    {settings.finished_dir}/merged/ (with manifest.jsonl)")
    print("=" * 80)


if __name__ == "__main__":
    main()
