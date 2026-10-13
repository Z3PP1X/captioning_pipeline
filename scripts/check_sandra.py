import sqlite3
from pathlib import Path

db = sqlite3.connect(Path.home() / ".cache/captioning_pipeline/state.db")
db.row_factory = sqlite3.Row

v = db.execute("SELECT * FROM videos WHERE name LIKE '%Sandra Romain Sara Faye%'").fetchone()
if not v:
    print("Video not found!")
    exit(0)

print(f"Video ID: {v['id']}")
print(f"Video Name: {v['name']}")
print(f"Video Slug: {v['slug']}")
print(f"Video Status: {v['status']}")

print("\n--- ALL JOBS FOR THIS VIDEO ---")
jobs = db.execute("SELECT * FROM jobs WHERE video_id=? ORDER BY id ASC", (v['id'],)).fetchall()
for j in jobs:
    print(f"Job #{j['id']}: type={j['type']}, status={j['status']}, prog={j['progress']*100:.1f}%, err={repr(j['error'])}")

exp_cnt = db.execute("SELECT count(*) FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (v['id'],)).fetchone()[0]
picks_cnt = db.execute("SELECT count(*) FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=? AND p.state IN ('keep', 'star')", (v['id'],)).fetchone()[0]
print(f"\nExports count in DB: {exp_cnt} / {picks_cnt} picks")

# Check exported files on disk
finished_dir = Path.home() / ".cache/captioning_pipeline/finished/per_video" / v['slug']
if finished_dir.exists():
    imgs = list(finished_dir.glob("*.png"))
    txts = list(finished_dir.glob("*.txt"))
    print(f"Files in finished per_video dir ({finished_dir}): {len(imgs)} images, {len(txts)} captions")
else:
    print(f"Finished dir does not exist: {finished_dir}")
