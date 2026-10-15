#!/usr/bin/env bash
# Source this file: `source scripts/env.sh`
# Keeps the venv and all caches on WSL ext4 (fast), code + finished output on /mnt/c.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/captioning_pipeline}"
export UV_LINK_MODE=copy
export UV_PYTHON=3.12
export CP_CACHE_DIR="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}"
export CP_VIDEO_DIR="${CP_VIDEO_DIR:-/mnt/c/Users/Patri/Videos/_girls}"
export CP_FINISHED_DIR="${CP_FINISHED_DIR:-/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished}"
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$HOME/.local/bin:$PATH"
mkdir -p "$CP_CACHE_DIR" "$HOME/.venvs"

cd "$PROJECT_ROOT"

# Automatically source .env if present (e.g. OPENROUTER_API_KEY=sk-or-v1-...)
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi
