import sqlite3
from pathlib import Path

db_path = Path.home() / ".cache" / "captioning_pipeline" / "state.db"
db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

print("Total frames:", db.execute("SELECT COUNT(*) FROM frames").fetchone()[0])
print("Total chunks:", db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
print("Total exports:", db.execute("SELECT COUNT(*) FROM exports").fetchone()[0])
print("Total picks:", db.execute("SELECT COUNT(*) FROM picks").fetchone()[0])
for r in db.execute("SELECT DISTINCT state, COUNT(*) as cnt FROM picks GROUP BY state").fetchall():
    print("State:", r["state"], "Count:", r["cnt"])

mismatched = db.execute("SELECT COUNT(*) FROM frames f LEFT JOIN chunks c ON f.chunk_id = c.id WHERE c.id IS NULL").fetchone()[0]
print("Frames with invalid chunk_id:", mismatched)
mismatched_picks = db.execute("SELECT COUNT(*) FROM picks p LEFT JOIN frames f ON p.frame_id = f.id WHERE f.id IS NULL").fetchone()[0]
print("Picks with invalid frame_id:", mismatched_picks)

print("\n--- Picks per video ---")
for r in db.execute("""
    SELECT c.video_id, p.role, COUNT(*) as cnt
    FROM picks p
    JOIN chunks c ON p.chunk_id = c.id
    GROUP BY c.video_id, p.role
""").fetchall():
    print(f"video_id: {r['video_id']}, role: {r['role']} -> {r['cnt']}")

print("\n--- Frames count per video_id in DB ---")
for r in db.execute("SELECT video_id, COUNT(*) as cnt FROM frames GROUP BY video_id").fetchall():
    print(f"video_id: {r['video_id']} (type {type(r['video_id']).__name__}) -> {r['cnt']} frames")

print("\n--- Chunks count per video_id in DB ---")
for r in db.execute("SELECT video_id, COUNT(*) as cnt FROM chunks GROUP BY video_id").fetchall():
    print(f"video_id: {r['video_id']} (type {type(r['video_id']).__name__}) -> {r['cnt']} chunks")

print("\n--- Exports count per video_id in DB ---")
for r in db.execute("""
    SELECT v.id, v.slug, COUNT(e.frame_id) as cnt
    FROM videos v
    JOIN frames f ON f.video_id = v.id
    JOIN exports e ON e.frame_id = f.id
    GROUP BY v.id
""").fetchall():
    print(f"video: {r['id']} ({r['slug']}) -> {r['cnt']} exports")

print("\n--- Columns in frames ---")
for col in db.execute("PRAGMA table_info(frames)").fetchall():
    print(col[1], col[2])

print("\n--- Columns in chunks ---")
for col in db.execute("PRAGMA table_info(chunks)").fetchall():
    print(col[1], col[2])
