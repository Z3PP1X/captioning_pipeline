#!/usr/bin/env bash
# Sourced by the web scripts: Angular CLI environment for WSL.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NODE_OPTIONS="--require $SCRIPT_DIR/patch_os.js --max-old-space-size=2048"
export NG_CLI_ANALYTICS=false
export NG_DISABLE_VERSION_CHECK=1
mkdir -p "$HOME" 2>/dev/null || true
([ -f "$HOME/.angular-config.json" ] || echo '{"version": 1, "cli": {"analytics": false, "completion": {"prompted": true}}}' > "$HOME/.angular-config.json") 2>/dev/null || true

# Keep the Angular build cache on ext4: on a /mnt/<drive> checkout (9p) it is slow and can corrupt.
# `.angular` becomes a symlink into the cache dir (gitignored either way).
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
case "$REPO_ROOT" in
  /mnt/*)
    NG_CACHE_DIR="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}/angular"
    mkdir -p "$NG_CACHE_DIR"
    if [ -d "$REPO_ROOT/.angular" ] && [ ! -L "$REPO_ROOT/.angular" ]; then
      rm -rf "$REPO_ROOT/.angular"
    fi
    [ -L "$REPO_ROOT/.angular" ] || ln -s "$NG_CACHE_DIR" "$REPO_ROOT/.angular"
    ;;
esac
