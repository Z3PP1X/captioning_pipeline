import httpx

base = "http://127.0.0.1:8000"

with httpx.Client(timeout=15.0) as client:
    # 1. Resume OpenRouter captioning for 09.01.23 Maitresse Madeline Marlowe (with max_tokens=300 and trigger='bdsm')
    r1 = client.post(
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
    print("Maitresse Madeline caption job:", r1.status_code, r1.json())

    # 2. Re-queue extract-750 for 07.08.10 Justine Joli and 07.08.31 Sandra Romain Bobbi Starr
    for vid in ["1baf81ae34a8cb7e", "4795b3650f204200"]:
        # lookup full id from prefix
        vids = client.get(f"{base}/api/videos").json()["videos"]
        match = next((v for v in vids if v["id"].startswith(vid[:8])), None)
        if match:
            r2 = client.post(f"{base}/api/videos/{match['id']}/extract-750")
            print(f"Extract-750 for {match['name'][:35]}:", r2.status_code, r2.json())
