#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Building Angular 22 + Spartan NG frontend..."
export NG_CLI_ANALYTICS=false
export NG_DISABLE_VERSION_CHECK=1
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NODE_OPTIONS="--require $SCRIPT_DIR/patch_os.js --max-old-space-size=2048"
mkdir -p "$HOME"
echo '{"version": 1, "cli": {"analytics": false}}' > "$HOME/.angular-config.json"
./node_modules/.bin/ng build --configuration development --progress=false
