#!/usr/bin/env bash
# UI/UX quality gate helpers (see `make ux-gate`, frontend/package.json scripts and
# .agents/skills/ux-gate/SKILL.md). Works when called from the repo root or from the `frontend/` facade
# (frontend/scripts -> ../scripts): it always cd's into the real repo root.
#
#   scripts/ux_gate.sh lint-ts      ESLint (complexity + Angular template a11y rules, inline templates included)
#   scripts/ux_gate.sh lint-styles  Stylelint (design tokens, 4px spacing grid, no !important)
#   scripts/ux_gate.sh build        Production build of the Angular app into src/web/dist
#   scripts/ux_gate.sh serve        Build + start the deterministic mock API/static server (UX_GATE_PORT, default 4300)
#   scripts/ux_gate.sh serve-lhci   Build + mock server + headless Chromium with a CDP port for Lighthouse CI
#   scripts/ux_gate.sh e2e          Playwright UX suite (frontend/e2e/ux-gate.spec.ts)
#   scripts/ux_gate.sh lhci         Lighthouse CI (frontend/lighthouserc.json)
#   scripts/ux_gate.sh all          Everything above, fail-fast
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd -P)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/ng_env.sh" >/dev/null 2>&1 || true
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/browser_env.sh"

cmd="${1:-all}"
# Parallel runs (e.g. several agents) can isolate themselves with UX_GATE_PORT + UX_GATE_OUT (build output dir).
build_app() {
  if [ -n "${UX_GATE_OUT:-}" ]; then
    npx ng build --configuration production --output-path "$UX_GATE_OUT"
    export UX_GATE_DIST="$UX_GATE_OUT/browser"
  else
    npx ng build --configuration production
  fi
}
case "$cmd" in
  lint-ts)
    npx eslint .
    ;;
  lint-styles)
    npx stylelint "src/web/**/*.css"
    ;;
  build)
    build_app
    ;;
  serve)
    if [ "${UX_GATE_SKIP_BUILD:-0}" != "1" ]; then build_app; fi
    [ -n "${UX_GATE_OUT:-}" ] && export UX_GATE_DIST="$UX_GATE_OUT/browser"
    exec node frontend/e2e/mock-server.mjs
    ;;
  serve-lhci)
    if [ "${UX_GATE_SKIP_BUILD:-0}" != "1" ]; then build_app; fi
    [ -n "${UX_GATE_OUT:-}" ] && export UX_GATE_DIST="$UX_GATE_OUT/browser"
    exec node frontend/e2e/lhci-server.mjs
    ;;
  e2e)
    cd frontend && npx playwright test e2e/ux-gate.spec.ts
    ;;
  lhci)
    cd frontend && npx lhci autorun
    ;;
  all)
    "$SCRIPT_PATH" lint-ts
    "$SCRIPT_PATH" lint-styles
    "$SCRIPT_PATH" e2e
    UX_GATE_SKIP_BUILD=1 "$SCRIPT_PATH" lhci
    ;;
  *)
    echo "usage: $0 {lint-ts|lint-styles|build|serve|serve-lhci|e2e|lhci|all}" >&2
    exit 2
    ;;
esac
