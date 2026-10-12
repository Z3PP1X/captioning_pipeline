#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
uv sync
echo "setup complete: venv at $UV_PROJECT_ENVIRONMENT"
