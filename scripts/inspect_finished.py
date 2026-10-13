from pathlib import Path
import sqlite3

finished_dir = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished")
per_video_dir = finished_dir / "per_video"

print("per_video folders:")
if per_video_dir.exists():
    for d in sorted(per_video_dir.iterdir()):
        if d.is_dir():
            txts = list(d.glob("*.txt"))
            pngs = list(d.glob("*.png"))
            webps = list(d.glob("*.webp"))
            jpgs = list(d.glob("*.jpg"))
            print(f"Folder: {d.name}")
            print(f"  txt: {len(txts)}, png: {len(pngs)}, webp: {len(webps)}, jpg: {len(jpgs)}")
            if txts:
                print(f"  Sample txt filename: {txts[0].name}")
