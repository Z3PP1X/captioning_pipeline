import sqlite3

con = sqlite3.connect("/home/z3pp1x/.cache/captioning_pipeline/state.db")
vids = con.execute("SELECT id, name, status FROM videos WHERE status IN ('curating_300', 'chunked', 'captioning')").fetchall()

print("Videos in progress or needing captioning:")
for vid, name, status in vids:
    picks = con.execute("SELECT COUNT(*) FROM picks p JOIN frames f ON p.frame_id=f.id WHERE f.video_id=? AND p.role='pick'", (vid,)).fetchone()[0]
    exports = con.execute("SELECT COUNT(*) FROM exports e JOIN frames f ON e.frame_id=f.id WHERE f.video_id=?", (vid,)).fetchone()[0]
    print(f"  [{status:12s}] {name[:40]:40s} | {exports}/{picks} captioned")
