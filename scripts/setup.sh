#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv python install 3.12
uv sync

# Node 22 via nvm in $HOME (WSL ext4, not /mnt/c), only when no suitable Node is on PATH
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  source "$NVM_DIR/nvm.sh"
fi
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
if [ "$NODE_MAJOR" -lt 20 ]; then
  echo "Installing Node.js 22 with nvm into $NVM_DIR..."
  if [ ! -s "$NVM_DIR/nvm.sh" ]; then
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | PROFILE=/dev/null bash
  fi
  # shellcheck disable=SC1091
  source "$NVM_DIR/nvm.sh"
  nvm install 22
  nvm use 22
fi

if [ -f "package.json" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi

echo "setup complete: venv at $UV_PROJECT_ENVIRONMENT"
