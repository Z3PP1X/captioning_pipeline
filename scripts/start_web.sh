#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ng_env.sh"
echo "Starting Angular dev server on port 4200..."
exec ./node_modules/.bin/ng serve --proxy-config proxy.conf.json --host 127.0.0.1 --port 4200
