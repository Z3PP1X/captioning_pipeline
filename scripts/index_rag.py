#!/usr/bin/env python3
"""CLI utility to index curated dataset captions for Krea 2 RAG Prompt Tool."""
from __future__ import annotations

import argparse
import sys
import time

from pipeline.config import get_settings
from pipeline.rag.indexer import RagCaptionIndexer


def main() -> int:
    parser = argparse.ArgumentParser(description="Index curated captions for RAG prompt tool.")
    parser.add_argument("--limit", type=int, default=None, help="Optional limit for test indexing.")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size for embedding inference.")
    args = parser.parse_args()

    settings = get_settings()
    print("🚀 Initializing RAG Caption Indexer...")
    indexer = RagCaptionIndexer(settings)

    print(f"📦 Scanning captions from {settings.curated_dir}...")
    t0 = time.time()
    res = indexer.build_index(limit=args.limit, batch_size=args.batch_size)

    print("\n✅ Indexing complete!")
    print(f"   Indexed:           {res['indexed']} captions")
    print(f"   Embeddings matrix: {res['embeddings_shape']}")
    print(f"   SQLite DB:         {res['db_path']}")
    print(f"   NPZ matrix:        {res['npz_path']}")
    print(f"   Duration:          {res['duration']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
