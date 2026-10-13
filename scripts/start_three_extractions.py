import httpx
import json

video_ids = [
    "4ddf10553f37a0a1",  # SEX AND SUBMISSION - Pretty Piece of Fuck Meat
    "d90955dd74e67334",  # TheTrainingOfO - Calico 3
    "83c87a1cce688974",  # WIRED PUSSY - Donna Dolore
]

with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30.0) as client:
    for vid in video_ids:
        resp = client.post(f"/api/videos/{vid}/extract-750")
        print(f"Video {vid[:8]}: status_code={resp.status_code}, response={resp.json()}")
