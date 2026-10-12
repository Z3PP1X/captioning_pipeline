import asyncio
import os
import cv2
import httpx
from pathlib import Path
from pipeline.openrouter import OpenRouterCaptioner

async def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    captioner = OpenRouterCaptioner()
    
    # Pick first png in per_video for Madeline
    pngs = sorted(Path("data/finished/per_video").glob("090123*/*.png"))
    if not pngs:
        print("No PNG found")
        return
    img = cv2.imread(str(pngs[0]))
    b64 = captioner.prepare_512px_payload(img, max_dim=512)
    sys_prompt = captioner.load_system_prompt("concept", "bdsm", "woman")
    
    for effort in ["high", "low", None]:
        payload = captioner.build_request_payload(
            b64, sys_prompt, model="google/gemma-4-31b-it", max_tokens=300, reasoning_effort=effort
        )
        headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=60.0)
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            print(f"Effort={effort} | status={resp.status_code} | finish_reason={choice.get('finish_reason')} | usage={data.get('usage')}")
            print(f"  content len={len(msg.get('content') or '')}: {(msg.get('content') or '')[:150]}")

asyncio.run(main())
