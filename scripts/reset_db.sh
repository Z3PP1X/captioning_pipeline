#!/usr/bin/env bash
# Reset Captioning Pipeline SQLite database with automatic backup.
# Usage:
#   scripts/reset_db.sh              # Reset state.db (creates a timestamped backup)
#   scripts/reset_db.sh --purge-all  # Also remove cached candidates, previews, and filmstrips
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source env if present
if [ -f "$SCRIPT_DIR/env.sh" ]; then
    source "$SCRIPT_DIR/env.sh"
fi

CACHE_DIR="${CP_CACHE_DIR:-$HOME/.cache/captioning_pipeline}"
DB_PATH="$CACHE_DIR/state.db"

echo "==> Captioning Pipeline Database Reset"
echo "Target cache directory: $CACHE_DIR"
echo "Target database path:    $DB_PATH"

if [ -f "$DB_PATH" ]; then
    TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
    BACKUP_PATH="$CACHE_DIR/state.db.bak_$TIMESTAMP"
    echo "Backing up existing database to: $BACKUP_PATH"
    cp "$DB_PATH" "$BACKUP_PATH"
    rm -f "$DB_PATH" "$CACHE_DIR/state.db-wal" "$CACHE_DIR/state.db-shm"
fi

if [[ "${1:-}" == "--purge-all" ]]; then
    echo "Purging candidates, previews, and filmstrips..."
    rm -rf "$CACHE_DIR/candidates" "$CACHE_DIR/previews" "$CACHE_DIR/filmstrips"
    mkdir -p "$CACHE_DIR/candidates" "$CACHE_DIR/previews" "$CACHE_DIR/filmstrips"
fi

echo "Initializing clean database schema..."
python3 -c "import sys; sys.path.insert(0, '$PROJECT_ROOT/src'); from pipeline.db import DB; from pipeline.config import get_settings; db = DB(get_settings().db_path); print('Successfully initialized schema at:', db.path)"

echo "Database reset complete."
