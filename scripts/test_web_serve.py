import re
from fastapi.testclient import TestClient
from pipeline.api import app

client = TestClient(app)

# 1. Root route
res = client.get("/")
print("Root status:", res.status_code)
assert res.status_code == 200, f"Expected 200, got {res.status_code}"
assert "<app-root>" in res.text, "Index does not contain <app-root>"

# 2. Main JS bundle
match = re.search(r'src="([^"]+\.js)"', res.text)
if match:
    js_file = match.group(1)
    res_js = client.get(f"/{js_file}")
    print(f"Bundle {js_file} status:", res_js.status_code, "Size:", len(res_js.content))
    assert res_js.status_code == 200, f"Failed to fetch {js_file}"

# 3. API endpoint
res_api = client.get("/api/videos")
print("API /api/videos status:", res_api.status_code)
assert res_api.status_code == 200, "API returned error"

print("All FastAPI SPA integration checks passed successfully!")
