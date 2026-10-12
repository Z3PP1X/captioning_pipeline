#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
exec uv run python "$(dirname "$0")/batch_auto_process.py" "$@"
