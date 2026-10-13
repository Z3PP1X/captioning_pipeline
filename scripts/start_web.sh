#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NODE_OPTIONS="--require $SCRIPT_DIR/patch_os.js --max-old-space-size=2048"
export NG_CLI_ANALYTICS=false
export NG_DISABLE_VERSION_CHECK=1
mkdir -p "$HOME"
echo '{"version": 1, "cli": {"analytics": false}}' > "$HOME/.angular-config.json"
echo "Starting Angular dev server on port 4200..."
exec ./node_modules/.bin/ng serve --proxy-config proxy.conf.json --host 0.0.0.0 --port 4200
