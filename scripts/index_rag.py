#!/usr/bin/env python3
"""CLI utility to index captions for the Krea 2 RAG Prompt Tool.

Source: the captions table in state.db (vlm + imported), else data/curated_2500, else finished/merged.

Run inside WSL:  source scripts/env.sh && uv run python scripts/index_rag.py
(The Prompt Director tab can also start the same build via POST /api/rag/index.)
"""
from __future__ import annotations

import argparse
import sys

from pipeline.config import get_settings
from pipeline.rag.indexer import RagCaptionIndexer, resolve_caption_sources


def main() -> int:
    parser = argparse.ArgumentParser(description="Index curated captions for RAG prompt tool.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for test indexing.")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size for embedding inference.")
    parser.add_argument("--incremental", action="store_true",
                        help="Embed only new/changed captions (full build when the index is missing or incompatible).")
    args = parser.parse_args()

    settings = get_settings()
    sources = resolve_caption_sources(settings)
    if not sources["source"]:
        print("No captions found. Searched:")
        for path in sources.get("searched", []):
            print(f"  - {path}")
        print("Caption + export frames first (optionally run scripts/curate_dataset.py), then re-run.")
        return 1

    print(f"Indexing {sources['caption_files']} captions from {sources['path']} ({sources['source']})...")
    indexer = RagCaptionIndexer(settings)

    def progress(frac: float, step: str) -> None:
        print(f"  [{frac * 100:5.1f}%] {step}", flush=True)

    res = indexer.build_index(limit=args.limit, batch_size=args.batch_size, progress_cb=progress,
                              incremental=args.incremental)

    print("\nIndexing complete.")
    print(f"   Mode:              {res.get('mode')}" + (f" ({res['full_reason']})" if res.get("full_reason") else ""))
    if res.get("mode") == "incremental":
        print(f"   Added/updated/removed: {res.get('added')}/{res.get('updated')}/{res.get('removed')}")
    print(f"   Indexed:           {res['indexed']} captions ({res.get('count')} in the index)")
    print(f"   Source:            {res.get('source')} ({res.get('source_path')})")
    print(f"   Embeddings matrix: {res.get('embeddings_shape')}")
    print(f"   Domain chunks:     {res.get('domain_segments')} (R1 per-domain vectors)")
    image = res.get("image_index") or {}
    if image.get("enabled"):
        print(f"   Image index:       {image.get('count')} images ({image.get('embedder')}), "
              f"{image.get('missing')} without image" + (f", error: {image['error']}" if image.get("error") else ""))
    else:
        print("   Image index:       off (setting rag_image_index)")
    print(f"   SQLite DB:         {res.get('db_path')}")
    print(f"   NPZ matrix:        {res.get('npz_path')}")
    print(f"   Duration:          {res['duration']}s")
    print("A running server picks up the new index automatically on the next query.")
    return 0 if res.get("count") else 1


if __name__ == "__main__":
    sys.exit(main())
