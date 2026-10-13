import sqlite3
import time
from pathlib import Path

db = sqlite3.connect(Path.home() / ".cache/captioning_pipeline/state.db")
db.row_factory = sqlite3.Row

print("=== CURRENT ACTIVE JOBS ===")
for r in db.execute("SELECT * FROM jobs WHERE status IN ('running', 'queued') ORDER BY id ASC").fetchall():
    v = db.execute("SELECT name, slug FROM videos WHERE id=?", (r["video_id"],)).fetchone()
    vname = v["name"] if v else "Unknown"
    created = time.strftime('%H:%M:%S', time.localtime(r['created_at'])) if r['created_at'] else 'N/A'
    started = time.strftime('%H:%M:%S', time.localtime(r['started_at'])) if r['started_at'] else 'N/A'
    print(f"Job #{r['id']}: type={r['type']}, status={r['status']}, prog={r['progress']*100:.1f}%, created={created}, started={started}")
    print(f"  Video: {r['video_id'][:8]} ({vname})")
    if r['error']:
        print(f"  Error: {r['error']}")

print("\n=== ALL RECENT JOBS (>= 70) ===")
for r in db.execute("SELECT * FROM jobs WHERE id >= 70 ORDER BY id ASC").fetchall():
    v = db.execute("SELECT name, slug FROM videos WHERE id=?", (r["video_id"],)).fetchone()
    vname = v["name"] if v else "Unknown"
    fin = time.strftime('%H:%M:%S', time.localtime(r['finished_at'])) if r['finished_at'] else 'running'
    start = time.strftime('%H:%M:%S', time.localtime(r['started_at'])) if r['started_at'] else 'N/A'
    dur = f"{(r['finished_at'] - r['started_at']):.0f}s" if (r['finished_at'] and r['started_at']) else 'running'
    print(f"Job #{r['id']}: type={r['type']}, status={r['status']}, start={start}, fin={fin} ({dur}), prog={r['progress']*100:.1f}%")
    if r['error']:
        print(f"  Error: {r['error']}")
    print(f"  Video: {r['video_id'][:8]} ({vname})")

print("\n=== LAST CAPTIONING JOBS ===")
for r in db.execute("SELECT * FROM jobs WHERE type='openrouter_caption' ORDER BY id DESC LIMIT 10").fetchall():
    v = db.execute("SELECT name, slug FROM videos WHERE id=?", (r["video_id"],)).fetchone()
    vname = v["name"] if v else "Unknown"
    fin = time.strftime('%H:%M:%S', time.localtime(r['finished_at'])) if r['finished_at'] else 'N/A'
    start = time.strftime('%H:%M:%S', time.localtime(r['started_at'])) if r['started_at'] else 'N/A'
    dur = f"{(r['finished_at'] - r['started_at']):.0f}s" if (r['finished_at'] and r['started_at']) else 'running'
    exp = db.execute("SELECT count(*) FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (r["video_id"],)).fetchone()[0]
    picks = db.execute("SELECT count(*) FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=? AND p.state IN ('keep', 'star')", (r["video_id"],)).fetchone()[0]
    print(f"Job #{r['id']}: status={r['status']}, start={start}, fin={fin} ({dur}), prog={r['progress']*100:.1f}%, exported={exp}/{picks}")
    if r['error']:
        print(f"  Error: {r['error']}")
    print(f"  Video: {r['video_id'][:8]} ({vname})")

print("\n=== VIDEOS BY STATUS ===")
for r in db.execute("SELECT status, count(*) as cnt FROM videos GROUP BY status").fetchall():
    print(f"  {r['status']}: {r['cnt']}")
