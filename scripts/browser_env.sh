#!/usr/bin/env bash
# Sourced by scripts/ux_gate.sh and `make ux-gate`: headless Chromium for Playwright and Lighthouse CI.
#
# - CHROME_PATH: Lighthouse CI (chrome-launcher) uses Playwright's bundled Chromium unless already set.
# - LD_LIBRARY_PATH: if Chromium's system libraries (libnss3, libnspr4, libasound2) are not installed and
#   there is no root to `npx playwright install-deps`, scripts/ux_gate_setup.sh unpacks them into the cache.
_cp_cache="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}"
_cp_deps="$_cp_cache/chrome-deps/root/usr/lib/$(uname -m)-linux-gnu"
if [ -d "$_cp_deps" ]; then
  case ":${LD_LIBRARY_PATH:-}:" in
    *":$_cp_deps:"*) ;;
    *) export LD_LIBRARY_PATH="$_cp_deps${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
  esac
fi
if [ -z "${CHROME_PATH:-}" ]; then
  _cp_root="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)"
  _cp_chrome="$(cd "$_cp_root" && node -e 'try{process.stdout.write(require("@playwright/test").chromium.executablePath())}catch{}' 2>/dev/null || true)"
  if [ -n "$_cp_chrome" ] && [ -x "$_cp_chrome" ]; then
    export CHROME_PATH="$_cp_chrome"
  fi
  unset _cp_root _cp_chrome
fi
unset _cp_cache _cp_deps
