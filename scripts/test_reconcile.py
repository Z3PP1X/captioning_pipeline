import re
import sqlite3
from pathlib import Path

db_path = Path.home() / ".cache" / "captioning_pipeline" / "state.db"
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row

finished_dir = Path("/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished")
per_video_dir = finished_dir / "per_video"

# Regex for caption files: {slug}_c{chunk_num:02d}_{pts_ms:07d}.txt
pattern = re.compile(r"^(.*)_c(\d+)_(\d+)\.txt$")

txt_files = list(per_video_dir.glob("*/*.txt"))
print(f"Total .txt files found: {len(txt_files)}")

matched_frames = 0
unmatched_frames = 0
video_counts = {}

for txt_path in txt_files:
    m = pattern.match(txt_path.name)
    if not m:
        print("Non-matching filename:", txt_path.name)
        continue
    vslug, chunk_num_str, pts_ms_str = m.groups()
    chunk_pos = int(chunk_num_str) - 1
    t_sec = int(pts_ms_str) / 1000.0

    # Look up video by slug
    v = con.execute("SELECT id FROM videos WHERE slug=?", (vslug,)).fetchone()
    if not v:
        # Try matching folder name
        v = con.execute("SELECT id FROM videos WHERE slug=?", (txt_path.parent.name,)).fetchone()
    if not v:
        print(f"Video not found for slug: {vslug} (folder: {txt_path.parent.name})")
        unmatched_frames += 1
        continue

    vid = v["id"]
    # Look up frame by video_id and approximate timestamp t (within 0.05s)
    f = con.execute(
        "SELECT id FROM frames WHERE video_id=? AND ABS(t - ?) < 0.05 ORDER BY ABS(t - ?) ASC LIMIT 1",
        (vid, t_sec, t_sec),
    ).fetchone()

    if f:
        matched_frames += 1
        video_counts[vid] = video_counts.get(vid, 0) + 1
    else:
        unmatched_frames += 1

print(f"Matched frames in DB: {matched_frames}")
print(f"Unmatched frames: {unmatched_frames}")
for vid, cnt in video_counts.items():
    vname = con.execute("SELECT name FROM videos WHERE id=?", (vid,)).fetchone()["name"]
    print(f"\nVideo {vid[:8]} ({vname[:30]}): {cnt} captions matched")
    chunks = con.execute("SELECT id, position, start_s, end_s FROM chunks WHERE video_id=? ORDER BY position ASC", (vid,)).fetchall()
    for ch in chunks:
        ch_picks = con.execute("SELECT count(*) FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.chunk_id=? AND p.role='pick'", (ch["id"],)).fetchone()[0]
        # count how many frames in this chunk have matching caption
        # For this test, we can check matching t_sec
        frames_in_ch = con.execute("SELECT id, t FROM frames WHERE chunk_id=?", (ch["id"],)).fetchall()
        # let's see how many have txt files
        captioned_in_ch = 0
        for f in frames_in_ch:
            # check if there's any txt file for this video near f['t']
            fname_stem_prefix = f"c{ch['position'] + 1:02d}_{int(f['t'] * 1000):07d}.txt"
            # we can check by pts
            if any(str(int(f['t'] * 1000)) in p.name for p in txt_files if vname[:10].lower() in p.parent.name.lower()):
                captioned_in_ch += 1
        is_full = captioned_in_ch >= ch_picks and ch_picks > 0
        status_mark = "✅ FULL" if is_full else f"⏳ {captioned_in_ch}/{ch_picks}"
        print(f"  Chunk #{ch['position']+1}: {status_mark} (picks: {ch_picks}, captioned: {captioned_in_ch})")
