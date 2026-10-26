---
name: ux-gate
description: Deterministic UI/UX quality gate for the Angular frontend (frontend/ facade over src/web) - ESLint template a11y + OnPush, Stylelint design tokens / 4px spacing grid / no !important, Playwright suite with the 5 UX laws (axe WCAG 2.1 AA, keyboard-only HITL triage without focus trap, CLS <= 0.01, Fitts 32x32 + overflow + text clipping, < 100 ms busy feedback) against a mock API, and Lighthouse CI thresholds. Use before marking ANY frontend component/feature as done, when `make ux-gate` fails, or when adding views, hotkeys, images, buttons or slow actions to the UI.
---

# UX gate

**Rule: no frontend component is done until `make ux-gate` (or `cd frontend && npm run ux-gate`) passes.**

## Layout

| Piece | Path |
| --- | --- |
| Make target | `Makefile` -> `ux-gate` (`source scripts/browser_env.sh && cd frontend && npm run lint:ts && npm run lint:styles && npx playwright test e2e/ux-gate.spec.ts && npx lhci autorun`) |
| npm scripts | `package.json` (= `frontend/package.json`, symlink): `lint:ts`, `lint:styles`, `e2e:ux`, `lhci`, `ux-gate`, `ux-gate:setup` -> `scripts/ux_gate.sh <cmd>` (always cd's into the real repo root) |
| ESLint | `eslint.config.mjs` - inline templates are linted via `extract-inline-html`; template rules `click-events-have-key-events`, `interactive-supports-focus`, `alt-text`, `no-autofocus`, `no-positive-tabindex`; `prefer-on-push-component-change-detection` + complexity limits |
| Stylelint | `.stylelintrc.json` (`frontend/.stylelintrc.json` symlink): `color-no-hex` except `src/web/styles/_tokens.css`, `declaration-no-important`, margin/padding/gap only `var(--space-1..8)`, `0`, `auto` or 0.25rem multiples |
| Tokens | `src/web/styles/_tokens.css` (imported by `src/web/styles.css`); semantic aliases `--surface-card`, `--status-danger`, `--accent-primary`, spacing `--space-1..8` |
| UX suite | `frontend/e2e/ux-gate.spec.ts`, config `frontend/playwright.config.ts` |
| Mock backend | `frontend/e2e/mock-server.mjs` + `fixtures.mjs` (legacy `/api/*` and studio `/api/v1/*`, in-memory, `POST /__reset`), serves the prod build |
| Lighthouse | `frontend/lighthouserc.json` (desktop preset, 3 runs x 3 deep links); `frontend/e2e/lhci-server.mjs` starts mock server + Playwright Chromium with CDP port 9222 (chrome-launcher cannot launch a Linux Chrome under WSL) |
| Browsers | `make ux-gate-setup` (= `scripts/ux_gate_setup.sh`): Playwright Chromium; system libs via `playwright install-deps` or, without root, `apt-get download` into `~/.cache/captioning_pipeline/chrome-deps` (`scripts/browser_env.sh` sets `LD_LIBRARY_PATH` / `CHROME_PATH`) |

Views are opened via deep links `#/studio/<ingest|triage|tagging|captioning|export_rag>` (`core/utils/studio-route.utils.ts`).

## The five laws (what the spec checks)

1. **Zero axe violations** (`wcag2a, wcag2aa, wcag21a, wcag21aa`) on all 5 studio steps and the Settings (OpenRouter) dialog.
2. **Mouse-free HITL** in Step 4 (`page.mouse` throws): `J`/`K` change selection < 50 ms, `C` toggles the smart-crop proposal (`smart-crop-toggle[aria-pressed]`, `smart-crop-overlay`), `E` focuses `caption-long`, typed letters stay text, `Escape` blurs back to navigation mode, `Ctrl+Enter` saves + blurs, `Enter` approves (`data-caption-status="approved"`) and jumps to the next unreviewed item.
3. **CLS <= 0.01** (strict: shifts right after key presses count) with thumbnails delayed 400 ms, and while switching images.
4. **Geometry** at 1920x1080 and 1280x800: every visible `button, a[href], [role=button], .chip` >= 32x32 px, no horizontal page overflow, no clipped text in buttons/labels/links/`.truncate` without a `title` on itself or an ancestor.
5. **Feedback < 100 ms**: re-caption, smart-crop and cascade triggers set `[aria-busy="true"]` + spinner and disable themselves before awaiting; a double click sends one request.

## Running

```bash
make ux-gate-setup                 # once (network)
make ux-gate                       # full gate
cd frontend && npx playwright test e2e/ux-gate.spec.ts -g "Law 4" --reporter=line   # one law
# parallel agents: isolate port + build dir
UX_GATE_PORT=4310 UX_GATE_OUT=/tmp/ux-me npx playwright test e2e/ux-gate.spec.ts --output=/tmp/ux-me-results
```

New view? Add it to `STUDIO_VIEWS` in the spec (axe + geometry), give its root `data-testid="view-<id>"`, add fixtures for its API calls to the mock server (unhandled GETs are logged as `[mock] unhandled GET`), and add its deep link to `lighthouserc.json` if it is a main view.

## Self-healing protocol

- **Axe contrast**: adjust the colour tokens in `_tokens.css` (light classic UI) or pick lighter/darker Tailwind shades (dark studio) until >= 4.5:1 for normal text and >= 3:1 for large text / UI components. Avoid sub-12px text.
- **Other axe rules**: labels for every input/select/range (`<label for>` or `aria-label`), names for icon buttons, no interactive elements nested in interactive elements, dialogs with `role="dialog"`, `aria-modal`, `aria-labelledby`.
- **CLS > 0.01**: fixed `aspect-ratio` / width+height boxes or skeletons for every image, `min-height` / fixed `rows` for the caption textareas (long, medium, short) and any per-item text block.
- **Focus trap in textareas**: Escape (and Ctrl+Enter) must `blur()` the field and move focus to a non-input element; the global `@HostListener('window:keydown')` must ignore letter hotkeys while `document.activeElement` is an INPUT, TEXTAREA, SELECT or contentEditable.
- **Fitts**: keep small visuals but give the control `min-h-8 min-w-8` (32 px) via padding; add `title` to anything that uses `truncate`.
- **Busy feedback**: set the busy signal synchronously in the click handler, bind `[disabled]` and `[attr.aria-busy]`, render an `animate-spin` indicator, reset in `finally`.
- **Lighthouse TBT/TTI**: keep the initial bundle lean (defer heavy views with `@defer`), no long synchronous work in constructors.
