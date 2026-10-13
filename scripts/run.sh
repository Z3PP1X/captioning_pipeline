#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
echo "Starting Captioning Pipeline on http://127.0.0.1:8000..."
exec uv run uvicorn pipeline.api:app --host 127.0.0.1 --port 8000 --reload --reload-dir src --timeout-graceful-shutdown 1
