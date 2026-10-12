import sqlite3
con = sqlite3.connect("/home/z3pp1x/.cache/captioning_pipeline/state.db")
for r in con.execute("SELECT id, name, status FROM videos"):
    print(r[0], "|", r[1][:40], "|", r[2])
