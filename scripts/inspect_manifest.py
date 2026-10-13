import json
from pathlib import Path

p = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished/merged/manifest.jsonl")
if p.exists():
    print(f"File size: {p.stat().st_size} bytes")
    with open(p, "r", encoding="utf-8") as f:
        valid = 0
        invalid = 0
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
                valid += 1
            except Exception as e:
                invalid += 1
                print(f"Line {i} error: {e}")
                print(f"Content: {line[:80]!r}")
    print(f"Total valid: {valid}, invalid: {invalid}")
else:
    print("manifest.jsonl does not exist")
