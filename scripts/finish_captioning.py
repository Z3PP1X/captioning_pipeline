import httpx

base = "http://127.0.0.1:8000"
vids_to_finish = [
    "4795b3656251d297",  # Sandra Romain Bobbi Starr (233/300)
    "720923279026cb78",  # Maitresse Madeline Marlowe (242/300)
]

for vid in vids_to_finish:
    res = httpx.post(
        f"{base}/api/videos/{vid}/caption-openrouter",
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
        timeout=15.0,
    )
    print(f"Queued captioning for {vid[:8]}:", res.status_code, res.json())
