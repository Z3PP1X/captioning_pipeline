#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Building Angular 22 + Spartan NG frontend..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/ng_env.sh"
# Production by default (what FastAPI serves); `bash scripts/build_web.sh --dev` for a fast unoptimised build
CONFIGURATION=production
if [ "${1:-}" = "--dev" ]; then
  CONFIGURATION=development
fi
./node_modules/.bin/ng build --configuration "$CONFIGURATION" --progress=false
