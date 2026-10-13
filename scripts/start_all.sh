#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NODE_OPTIONS="--require $SCRIPT_DIR/patch_os.js --max-old-space-size=2048"
export NG_CLI_ANALYTICS=false
export NG_DISABLE_VERSION_CHECK=1
mkdir -p "$HOME"
echo '{"version": 1, "cli": {"analytics": false}}' > "$HOME/.angular-config.json"

echo "[$(date '+%H:%M:%S')] 1. Compiling Angular frontend into src/web/dist/browser..."
./node_modules/.bin/ng build --configuration development --progress=false

echo "[$(date '+%H:%M:%S')] 2. Launching Captioning Pipeline FastAPI server on http://0.0.0.0:8000..."
source "$SCRIPT_DIR/env.sh"
exec uv run uvicorn pipeline.api:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 1
