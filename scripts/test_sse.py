import httpx
try:
    with httpx.stream("GET", "http://127.0.0.1:8000/api/events", timeout=5.0) as r:
        print("Status code:", r.status_code)
        print("Content-Type:", r.headers.get("content-type"))
        for line in r.iter_lines():
            print("Received line:", line)
            break
except Exception as e:
    print("Error:", e)
