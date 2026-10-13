from fastapi.testclient import TestClient
from pipeline.api import app

client = TestClient(app)
videos = client.get("/api/videos").json()
print("Total videos in DB:", len(videos.get("videos", [])))
for v in videos.get("videos", []):
    vid = v["id"]
    status = v.get("status")
    exports = v.get("exports_count", 0)
    if status == "new" and exports == 0:
        continue
    print(f"Video {vid} ({v.get('slug')}): status={status}, exports={exports}")
    
    scenes_res = client.get(f"/api/videos/{vid}/scenes")
    if scenes_res.status_code == 200:
        chunks = scenes_res.json().get("scenes", [])
        fully_cnt = sum(1 for c in chunks if c.get("is_fully_captioned"))
        print(f"  -> Total scenes: {len(chunks)}, fully captioned scenes: {fully_cnt}")
        for ch in chunks[:3]:
            cap = ch.get("captioned_count", 0)
            picks = ch.get("picks_count", 0)
            pct = ch.get("caption_progress_pct", 0)
            print(f"     Scene #{ch.get('position', 0) + 1}: {cap}/{picks} ({pct}%) fully={ch.get('is_fully_captioned')}")

    # Also test curation-300 endpoint
    curation_res = client.get(f"/api/videos/{vid}/curation-300")
    if curation_res.status_code == 200:
        cands = curation_res.json().get("candidates", [])
        picks = sum(1 for f in cands if f.get("is_pick") or f.get("role") == "pick")
        captioned = sum(1 for f in cands if f.get("is_captioned"))
        print(f"  -> Curation 300: total candidates={len(cands)}, picks={picks}, captioned={captioned}")
