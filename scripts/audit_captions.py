#!/usr/bin/env python3
"""Read-only audit of captions on disk: truncated captions, exporter stubs and word-count spread.

Earlier caption runs used reasoning "high" with max_tokens 300, which cut captions off (review C3), and the
exporter could overwrite VLM captions with trigger-only stubs (review C1). This finds both so the affected
frames can be re-captioned. Nothing is modified.

Run inside WSL:  source scripts/env.sh && uv run python scripts/audit_captions.py [--dir DIR] [--list] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pipeline.config import get_settings
from pipeline.export import existing_caption_kind
from pipeline.openrouter import MODE_WORD_RANGES, looks_truncated

BUCKET = 20


def audit(root: Path) -> dict:
    rows = []
    for txt in sorted(root.glob("*/*.txt")):
        kind = existing_caption_kind(txt)
        text = txt.read_text(encoding="utf-8", errors="replace").strip() if kind else ""
        words = len(text.split())
        rows.append({
            "path": str(txt),
            "video": txt.parent.name,
            "kind": kind or "empty",
            "words": words,
            "truncated": kind == "caption" and looks_truncated(text),
        })

    captions = [r for r in rows if r["kind"] == "caption"]
    lo = min(r for r, _ in MODE_WORD_RANGES.values())
    histogram = Counter((r["words"] // BUCKET) * BUCKET for r in captions)
    per_video: dict[str, Counter] = {}
    for r in rows:
        c = per_video.setdefault(r["video"], Counter())
        c["files"] += 1
        c[r["kind"]] += 1
        c["truncated"] += int(r["truncated"])
        c["short"] += int(r["kind"] == "caption" and r["words"] < lo)

    return {
        "root": str(root),
        "files": len(rows),
        "captions": len(captions),
        "stubs": sum(r["kind"] == "stub" for r in rows),
        "empty": sum(r["kind"] == "empty" for r in rows),
        "truncated": sum(r["truncated"] for r in rows),
        "short": sum(r["kind"] == "caption" and r["words"] < lo for r in rows),
        "word_histogram": {f"{b}-{b + BUCKET - 1}": histogram[b] for b in sorted(histogram)},
        "per_video": {v: dict(c) for v, c in sorted(per_video.items())},
        "needs_recaption": [r["path"] for r in rows if r["kind"] != "caption" or r["truncated"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit caption .txt files for truncation and stubs.")
    parser.add_argument("--dir", type=Path, default=None, help="Defaults to <finished_dir>/per_video.")
    parser.add_argument("--list", action="store_true", help="Print every file that needs re-captioning.")
    parser.add_argument("--json", type=Path, default=None, help="Write the full report as JSON.")
    args = parser.parse_args()

    root = args.dir or get_settings().finished_dir / "per_video"
    if not root.is_dir():
        print(f"No caption directory at {root}")
        return 1

    report = audit(root)
    print(f"Captions under {report['root']}")
    print(f"  files: {report['files']}  VLM captions: {report['captions']}  stubs: {report['stubs']}  "
          f"empty: {report['empty']}")
    print(f"  truncated (no closing sentence): {report['truncated']}  "
          f"short (< {min(r for r, _ in MODE_WORD_RANGES.values())} words): {report['short']}")
    print("  word counts:")
    for bucket, count in report["word_histogram"].items():
        print(f"    {bucket:>9}: {count}")
    print("  per video (files / stubs / truncated / short):")
    for video, c in report["per_video"].items():
        print(f"    {video}: {c.get('files', 0)} / {c.get('stub', 0)} / {c.get('truncated', 0)} / {c.get('short', 0)}")
    if args.list:
        print("Needs re-captioning:")
        for path in report["needs_recaption"]:
            print(f"  {path}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
