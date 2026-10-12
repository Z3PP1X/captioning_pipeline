import sqlite3
con = sqlite3.connect("/home/z3pp1x/.cache/captioning_pipeline/state.db")
con.row_factory = sqlite3.Row

target_names = [
    "07.08.10 4505 Justine Joli",
    "07.08.31 4689 Sandra Romain",
    "06.11.22 4032 Sandra Romain",
    "09.01.23 5997 Maitresse Madeline",
    "10.12.03 11167 Jynx Maze",
    "12.09.14 25850 Aiden Starr",
    "SEX AND SUBMISSION - Dani Daniels",
    "SEX AND SUBMISSION - Nineteen and Bound Latina"
]

for pat in target_names:
    row = con.execute("SELECT id, name, status, duration FROM videos WHERE name LIKE ?", (f"%{pat}%",)).fetchone()
    if not row:
        print("NOT FOUND:", pat)
        continue
    vid = row["id"]
    n_chunks = con.execute("SELECT COUNT(*) FROM chunks WHERE video_id=?", (vid,)).fetchone()[0]
    n_frames = con.execute("SELECT COUNT(*) FROM frames WHERE video_id=?", (vid,)).fetchone()[0]
    n_picks = con.execute("SELECT COUNT(*) FROM picks JOIN frames ON picks.frame_id=frames.id WHERE frames.video_id=? AND picks.role='pick'", (vid,)).fetchone()[0]
    jobs = con.execute("SELECT id, type, status, progress, error FROM jobs WHERE video_id=? ORDER BY id ASC", (vid,)).fetchall()
    
    status_str = row["status"]
    name_str = row["name"][:45]
    print(f"\n[{status_str:14s}] {name_str:45s} (ID: {vid[:8]}) | Chunks: {n_chunks:2d} | Frames: {n_frames:4d} | Picks: {n_picks:3d}")
    for j in jobs:
        print(f"   -> Job #{j['id']}: type={j['type']} status={j['status']} prog={j['progress']} err={j['error']}")
