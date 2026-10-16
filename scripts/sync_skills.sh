#!/usr/bin/env bash
# .claude/skills is canonical; .agents/skills is a mirror for other agent tools. `--check` only reports drift.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "${1:-}" = "--check" ]; then
  diff -rq .claude/skills .agents/skills && echo "skills in sync"
  exit $?
fi
mkdir -p .agents/skills
rsync -a --delete .claude/skills/ .agents/skills/
echo "mirrored .claude/skills -> .agents/skills"
