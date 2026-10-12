import httpx
import sqlite3

# 1. Ensure DB status is clean
con = sqlite3.connect("/home/z3pp1x/.cache/captioning_pipeline/state.db")
with con:
    con.execute("UPDATE videos SET status='chunked' WHERE id LIKE '5912ebb7%'")
    con.execute("UPDATE videos SET status='chunked' WHERE id LIKE '1baf81ae%'")
    con.execute("UPDATE videos SET status='chunked' WHERE id LIKE '4795b365%'")
    con.execute("UPDATE videos SET status='captioning' WHERE id LIKE '72092327%'")

base = "http://127.0.0.1:8000"

with httpx.Client(timeout=30.0) as client:
    # 2. Requeue Madeline Marlowe OpenRouter captioning (300 max tokens)
    r_cap = client.post(
        f"{base}/api/videos/720923279026cb78/caption-openrouter",
        json={
            "model": "google/gemma-4-31b-it",
            "fallback_layer2": "deepseek/deepseek-v4.1-flash",
            "fallback_layer3": "x-ai/grok-4.7",
            "reasoning_effort": "high",
            "max_tokens": 300,
            "temperature": 0.80,
            "mode": "concept",
            "trigger": "bdsm",
            "class_noun": "woman, ropes",
        },
    )
    print("Madeline caption job queued:", r_cap.status_code, r_cap.json())

    # 3. Queue extract-750 for all 3 chunked videos
    vids_to_extract = [
        "1baf81ae79650fc7",  # Justine Joli
        "4795b3656251d297",  # Sandra Romain Bobbi Starr
        "5912ebb772d134fc",  # Sandra Romain Sara Faye
    ]

    for vid in vids_to_extract:
        r_ext = client.post(f"{base}/api/videos/{vid}/extract-750")
        print(f"Extract-750 job for {vid[:8]}:", r_ext.status_code, r_ext.json())

print("All jobs successfully dispatched!")
