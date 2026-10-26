# Captioning Pipeline - Quality, Complexity & Architecture Gatekeeper
SHELL := /bin/bash

.PHONY: help lint-complexity test-bigo test quality-gate ux-gate ux-gate-setup

help:
	@echo "========================================================================"
	@echo " Automated Complexity, Big-O & Code Quality Gatekeeper"
	@echo "========================================================================"
	@echo "  make lint-complexity : Static AST, ruff, complexipy, xenon, mypy, eslint, knip"
	@echo "  make test-bigo       : Big-O regression (<= O(N log N)), memory & benchmark gate"
	@echo "  make test            : Run full Python pytest & Angular Vitest unit suites"
	@echo "  make quality-gate    : Execute complete gatekeeper verification"
	@echo "  make ux-gate         : UI/UX gate - ESLint a11y, Stylelint tokens, Playwright UX laws, Lighthouse CI"
	@echo "  make ux-gate-setup   : One-time: Playwright Chromium + its system libraries"
	@echo "========================================================================"

lint-complexity:
	@bash scripts/verify_quality.sh --lint

test-bigo:
	@bash scripts/verify_quality.sh --bigo

test:
	@bash scripts/verify_quality.sh --unit

quality-gate:
	@bash scripts/verify_quality.sh --gate

# UI/UX quality gate (frontend/ facade over src/web). No frontend component counts as done until this passes.
# scripts/browser_env.sh only exports CHROME_PATH / LD_LIBRARY_PATH for the headless Chromium.
ux-gate:
	source scripts/browser_env.sh && cd frontend && npm run lint:ts && npm run lint:styles && npx playwright test e2e/ux-gate.spec.ts && npx lhci autorun

ux-gate-setup:
	@bash scripts/ux_gate_setup.sh
