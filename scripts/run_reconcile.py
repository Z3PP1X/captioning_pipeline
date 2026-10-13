from pipeline.db import DB
from pipeline.config import get_settings
from pipeline.reconcile import reconcile_captions_and_state, get_video_scenes_caption_stats
import json

settings = get_settings()
db = DB(settings.db_path)

stats = reconcile_captions_and_state(db, settings)
print("Reconcile Result:")
print(json.dumps(stats, indent=2))

print("\nSample Scene Breakdown for 07.08.31 4689 Sandra Romain (4795b3656251d297):")
scenes = get_video_scenes_caption_stats(db, "4795b3656251d297")
for s in scenes:
    flag = "✅ FULL" if s["is_fully_captioned"] else f"⏳ {s['captioned_count']}/{s['picks_count']}"
    print(f"  Chunk #{s['position']+1} ({s['start_s']:.1f}s - {s['end_s']:.1f}s): {flag} ({s['caption_progress_pct']}%)")
