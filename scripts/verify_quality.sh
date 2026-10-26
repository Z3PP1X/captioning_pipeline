#!/usr/bin/env bash
# Automated Complexity, Big-O & Code Quality Gatekeeper
# Usage:
#   scripts/verify_quality.sh --lint [files...]   (Run static lint & complexity checks)
#   scripts/verify_quality.sh --bigo              (Run Big-O regression, memory & benchmark gate)
#   scripts/verify_quality.sh --unit              (Run Python & Angular unit tests)
#   scripts/verify_quality.sh --gate              (Run full Quality Gate)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Source Python virtual environment if available
if [ -f "$SCRIPT_DIR/env.sh" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/env.sh" >/dev/null 2>&1 || true
fi

# Source Angular WSL environment
if [ -f "$SCRIPT_DIR/ng_env.sh" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/ng_env.sh" >/dev/null 2>&1 || true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

FAILED_CHECKS=()

run_check() {
    local name="$1"
    shift
    echo -e "\n${BLUE}================================================================${NC}"
    echo -e "${BOLD}▶ Running: ${name}${NC}"
    echo -e "${BLUE}================================================================${NC}"
    if "$@"; then
        echo -e "${GREEN}✔ [PASS] ${name}${NC}"
    else
        echo -e "${RED}✘ [FAIL] ${name}${NC}"
        FAILED_CHECKS+=("${name}")
    fi
}

DEFAULT_PY_TARGETS=(
    "$REPO_ROOT/src/pipeline/deduplication.py"
    "$REPO_ROOT/scripts/ast_vector_lint.py"
    "$REPO_ROOT/tests/complexity"
)

CUSTOM_TARGETS=("${@:2}")
if [ ${#CUSTOM_TARGETS[@]} -gt 0 ]; then
    TARGET_FILES=("${CUSTOM_TARGETS[@]}")
else
    TARGET_FILES=("${DEFAULT_PY_TARGETS[@]}")
fi

check_ast_vector_lint() {
    python3 "$REPO_ROOT/scripts/ast_vector_lint.py" "$REPO_ROOT/src/pipeline"
}

check_ruff_complexity() {
    ruff check "${TARGET_FILES[@]}"
}

check_complexipy() {
    complexipy --max-complexity-allowed 15 "${TARGET_FILES[@]}"
}

check_xenon_maintainability() {
    xenon --max-absolute B --max-modules A --max-average A "${TARGET_FILES[@]}"
}

check_mypy_strict() {
    mypy --strict "${TARGET_FILES[@]}"
}

check_knip_dead_code() {
    npx knip
}

check_bigo_and_memory() {
    pytest "$REPO_ROOT/backend/tests/complexity/test_algorithmic_bounds.py" --memray -v
}

check_python_unit_tests() {
    pytest "$REPO_ROOT/tests" -q --ignore="$REPO_ROOT/tests/complexity"
}

check_angular_unit_tests() {
    HOME=/tmp ./node_modules/.bin/ng test --watch=false
}

do_lint() {
    echo -e "${BOLD}${BLUE}Executing Static Complexity & Quality Linters...${NC}"
    run_check "AST Vector Loop Linter (VEC001)" check_ast_vector_lint
    run_check "Ruff Complexity & Performance (C901, PERF, PLR)" check_ruff_complexity
    run_check "Complexipy Cognitive Complexity (Ccog <= 15)" check_complexipy
    run_check "Xenon / Radon Maintainability Gate" check_xenon_maintainability
    run_check "Mypy Strict Static Typing" check_mypy_strict
    run_check "Knip Dead Code & Unused Exports Gate" check_knip_dead_code
}

do_bigo() {
    echo -e "${BOLD}${BLUE}Executing Algorithmic Big-O & Memory Bounds Gate...${NC}"
    run_check "Empirical Big-O Regression & Memory Ceiling (<= O(N log N), < 300MB)" check_bigo_and_memory
}

do_unit() {
    echo -e "${BOLD}${BLUE}Executing Unit Test Suites...${NC}"
    run_check "Python Pipeline Unit Tests" check_python_unit_tests
    run_check "Angular Vitest Frontend Unit Tests" check_angular_unit_tests
}

MODE="${1:---gate}"

case "$MODE" in
    --lint|lint-complexity)
        do_lint
        ;;
    --bigo|test-bigo)
        do_bigo
        ;;
    --unit|test)
        do_unit
        ;;
    --gate|quality-gate|--all)
        do_lint
        do_bigo
        do_unit
        ;;
    *)
        echo "Usage: $0 [--lint|--bigo|--unit|--gate] [optional files...]"
        exit 1
        ;;
esac

echo -e "\n${BLUE}================================================================${NC}"
echo -e "${BOLD}QUALITY GATE SUMMARY${NC}"
echo -e "${BLUE}================================================================${NC}"
if [ ${#FAILED_CHECKS[@]} -eq 0 ]; then
    echo -e "${GREEN}${BOLD}ALL CHECKS PASSED SUCCESSFULLY. Quality Gate APPROVED! 🎉${NC}\n"
    exit 0
else
    echo -e "${RED}${BOLD}QUALITY GATE FAILED! The following check(s) did not pass:${NC}"
    for failed in "${FAILED_CHECKS[@]}"; do
        echo -e "  ${RED}✘ ${failed}${NC}"
    done
    echo ""
    exit 1
fi
