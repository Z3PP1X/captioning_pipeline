"""Replace trigger word in curated dataset .txt files and dataset_manifest.jsonl."""
import re
from pathlib import Path

curated_dir = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/curated_2500")
old_trigger = "restrained_elegance"
new_trigger = "bdsm"

pattern = re.compile(rf"\b{re.escape(old_trigger)}\b")

txt_files = sorted(curated_dir.glob("*/*.txt"))
print(f"Scanning {len(txt_files)} text files in {curated_dir}...")

updated_files = 0
for txt_path in txt_files:
    content = txt_path.read_text(encoding="utf-8")
    if pattern.search(content):
        new_content = pattern.sub(new_trigger, content)
        txt_path.write_text(new_content, encoding="utf-8")
        updated_files += 1

print(f"Updated {updated_files} text files.")

# Also update dataset_manifest.jsonl if present
manifest_path = curated_dir / "dataset_manifest.jsonl"
updated_manifest_lines = 0
if manifest_path.exists():
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    new_lines = []
    for line in lines:
        if old_trigger in line:
            new_line = pattern.sub(new_trigger, line)
            updated_manifest_lines += 1
            new_lines.append(new_line)
        else:
            new_lines.append(line)
    manifest_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Updated {updated_manifest_lines} lines in dataset_manifest.jsonl.")

print("Trigger replacement complete!")
