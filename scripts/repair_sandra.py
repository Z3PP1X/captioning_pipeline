from pipeline.db import DB
from pipeline.config import get_settings
from pipeline.autochunk import AutoChunker

db = DB(get_settings().db_path)
vid_id = "5912ebb772d134fc"
vid = db.one("SELECT * FROM videos WHERE id=?", (vid_id,))
print("Video:", vid["name"], "Duration:", vid["duration"])

autochunker = AutoChunker(db)
chunks = autochunker.generate_balanced_chunks(vid_id, target_frames=300)
print(f"Generated {len(chunks)} balanced chunks:")
total_b = 0
for idx, c in enumerate(chunks):
    total_b += c.get("budget_pct", 0)
    print(f"  Chunk #{idx+1}: {c['start_s']:.1f}s - {c['end_s']:.1f}s (budget: {c.get('budget_pct')}%)")

print(f"Saved {len(chunks)} chunks with {total_b:.1f}% total budget. Status -> 'chunking'.")
