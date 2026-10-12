import httpx
res = httpx.post("http://127.0.0.1:8000/api/videos/5912ebb772d134fc/extract-750", timeout=10.0)
print("Extract-750 for 06.11.22 Sandra Romain:", res.status_code, res.json())
