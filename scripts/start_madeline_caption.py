import os
import httpx

base = "http://127.0.0.1:8000"
api_key = os.environ.get("OPENROUTER_API_KEY", "")

with httpx.Client(timeout=15.0) as client:
    r1 = client.post(
        f"{base}/api/videos/720923279026cb78/caption-openrouter",
        json={
            "api_key": api_key,
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
