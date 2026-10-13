import sqlite3
from pathlib import Path

db_path = Path.home() / ".cache" / "captioning_pipeline" / "state.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

print("=== EXPORTS COUNT ===")
print("Total exports:", con.execute("SELECT COUNT(*) FROM exports").fetchone()[0])

print("\n=== VIDEOS STATUSES ===")
for r in con.execute("SELECT status, count(*) as cnt FROM videos GROUP BY status").fetchall():
    print(f"Status: {r['status']} -> {r['cnt']}")

print("\n=== JOBS STATUSES ===")
for r in con.execute("SELECT status, count(*) as cnt FROM jobs GROUP BY status").fetchall():
    print(f"Job Status: {r['status']} -> {r['cnt']}")

print("\n=== NON-NEW VIDEOS WITH JOBS ===")
for v in con.execute("SELECT id, name, status, slug FROM videos WHERE status != 'new'").fetchall():
    vid = v["id"]
    jobs = con.execute("SELECT id, type, status, progress, error FROM jobs WHERE video_id=? ORDER BY id ASC", (vid,)).fetchall()
    print(f"\n{vid[:8]} ({v['status']}) - {v['name'][:40]} (slug: {v['slug']}):")
    for j in jobs:
        print(f"  Job #{j['id']}: type={j['type']} status={j['status']} prog={j['progress']} err={j['error']}")
