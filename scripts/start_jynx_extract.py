import httpx
res = httpx.post("http://127.0.0.1:8000/api/videos/aff731b1afe8d5ec/extract-750", timeout=10.0)
print("Extract-750 for 10.12.03 Jynx Maze:", res.status_code, res.json())
