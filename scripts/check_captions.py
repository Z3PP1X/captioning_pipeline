import glob
import json
from pathlib import Path

txt_files = sorted(Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished/per_video").glob("*/*.txt"))
print(f"Total per-video .txt caption files generated: {len(txt_files)}")

if txt_files:
    latest = txt_files[-3:]
    for p in latest:
        txt = p.read_text(encoding="utf-8")
        words = len(txt.split())
        print(f"=== {p.name} ({words} words) ===")
        print(txt)
        print()

manifest_path = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished/merged/manifest.jsonl")
if manifest_path.exists():
    with manifest_path.open() as f:
        records = [json.loads(line) for line in f if line.strip()]
    print(f"Total merged manifest records: {len(records)}")
    if records:
        last = records[-1]
        print(f"Latest record model: {last.get('model')}, layer: {last.get('layer')}")
        print(f"Cascade stats: {last.get('cascade_stats')}")
