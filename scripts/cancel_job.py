import httpx
res = httpx.delete("http://127.0.0.1:8000/api/jobs/37")
print("Cancel Job #37:", res.status_code, res.json())
