#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
uv sync

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js (v20+) is required for frontend building and testing but not installed." >&2
  exit 1
fi

if [ -f "package.json" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "setup complete: venv at $UV_PROJECT_ENVIRONMENT"
