#!/usr/bin/env bash
# One-time setup for the UX gate browsers (network required):
#   1. Playwright's Chromium (+ headless shell) into ~/.cache/ms-playwright
#   2. Chromium's system libraries: `npx playwright install-deps chromium` when root/sudo is available,
#      otherwise the .deb files are fetched with `apt-get download` (no root) and unpacked into
#      ~/.cache/captioning_pipeline/chrome-deps (picked up by scripts/browser_env.sh via LD_LIBRARY_PATH).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)"
cd "$REPO_ROOT"

npx playwright install chromium

CHROME="$(node -e 'process.stdout.write(require("@playwright/test").chromium.executablePath())')"
missing="$(ldd "$CHROME" 2>/dev/null | awk '/not found/ {print $1}' || true)"
if [ -z "$missing" ]; then
  echo "Chromium system libraries OK"
  exit 0
fi
echo "Missing Chromium libraries: $missing"
if sudo -n true 2>/dev/null; then
  sudo npx playwright install-deps chromium
  exit 0
fi

DEPS_DIR="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}/chrome-deps"
mkdir -p "$DEPS_DIR/debs" "$DEPS_DIR/root"
cd "$DEPS_DIR/debs"
# libasound2 is libasound2t64 on Ubuntu >= 24.04
apt-get download libnss3 libnspr4 libasound2t64 2>/dev/null || apt-get download libnss3 libnspr4 libasound2
for deb in ./*.deb; do dpkg-deb -x "$deb" "$DEPS_DIR/root"; done
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/browser_env.sh"
still="$(ldd "$CHROME" | awk '/not found/ {print $1}' || true)"
if [ -n "$still" ]; then
  echo "Still missing: $still - install them with your package manager" >&2
  exit 1
fi
echo "Chromium system libraries unpacked into $DEPS_DIR"
