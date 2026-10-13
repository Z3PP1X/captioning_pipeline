import sqlite3
from pathlib import Path

db = sqlite3.connect(Path.home() / ".cache/captioning_pipeline/state.db")
db.row_factory = sqlite3.Row

for jid in (69, 70):
    r = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if r:
        print(f"Job #{jid}: status={r['status']}, error={repr(r['error'])}")
