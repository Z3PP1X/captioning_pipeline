# Gap report - Angular frontend migration (handover)

Date: 2026-10-13. Branch `main`, up to date with `origin/main` at `258a2d3` (pulled this session).
Scope: what is missing or wrong between the **target** (legacy behaviour in `src/web/app.js` + mim26 design system + Angular/Tailwind/Spartan rules) and the **Angular app that exists** (`src/web/app`). Every item comes from reading code; **nothing was built, run or tested in this session** (no `npm install`, `ng build`, `pytest`, browser run). Treat "verified" as "confirmed by reading source".

## 0. Read first

Skills in `.claude/skills/` are the detailed source of truth; this report is the prioritised index.

| Skill | Use for |
| --- | --- |
| `pipeline-overview` | domain, stages, statuses, WSL rules, run/test scripts |
| `api-contract` | endpoints, shapes, SSE, remaining backend quirks |
| `studio-ui-spec` | legacy behaviour spec + "Port status" table + parity checklist |
| `angular-migration` | current Angular architecture, build/serve, conventions, remaining work |
| `styling`, `design-system` (+ `references/studio-adaptations.md`) | rules and the verified "Compliance gaps" table |

Hard rules: all Python/Node/uv commands run **inside WSL 2** (`AGENTS.md`); tests must use fixture videos, never the user's library; never commit `.env`, `*.db`, frames, `data/finished`.

## 1. Repository state to resolve first

- Skills work is **uncommitted**: modified upstream skill files (`design-system/*`, `styling`), deleted `skills/**` (moved upstream to `.claude/skills`), untracked `angular-migration/`, `api-contract/`, `pipeline-overview/`, `studio-ui-spec/`, `design-system/references/studio-adaptations.md`, and this report. Nothing was committed or pushed.
- A git stash `skills-wip` still exists as a backup of the pre-pull skills work (its `git stash drop` was blocked by the permission classifier). Drop it only after reviewing the skills.
- `.agents/skills/{design-system,styling}` is an upstream duplicate of `.claude/skills/*` and now differs (styling). Decide which tree is canonical and mirror or delete.
- The `skills/` -> `.claude/skills/` move was already done upstream; the working tree shows the old `skills/` files as deleted.

## 2. Functional gaps (P1 = user-visible breakage)

| # | Pri | Gap | Where / evidence | Done when |
| --- | --- | --- | --- | --- |
| F1 | P1 | **Only 5 hotkey actions are registered** (`next_video`, `escape`, `keep`, `reject`, `star`). Space, J/L, arrows, `,` `.`, `[` `]` / I/O, Enter, C, G, PageUp/Down do nothing. No `J/L` or `Alt+Arrow` mapping either. Legacy never bound them (its init crashed), so users have never had them. | `core/services/hotkey.service.ts`, `app.component.ts`; key table in `studio-ui-spec` | All keys in the spec table work; `Enter` only on Studio tab; shortcuts suppressed in inputs and while a modal is open |
| F2 | P1 | **Extract & Score 750 no longer saves chunks first and no longer rescales budgets**; unsaved chunk edits are ignored by the job. | `studio/chunks-panel`/`chunk.service` (see port status row) | Button saves (and normalises to 100%) before `POST /extract-750`, or is disabled with a hint while edits are unsaved |
| F3 | P1 | **Video status `<select>` has only 6 options**; `extracting_750`, `curating_300`, `captioning`, `chunking`, `ignored` render blank. | `studio/studio-view.component.ts` | All `VideoStatus` values selectable and labelled |
| F4 | P2 | Snap-to-cut window fixed at 0.5 s; spec/settings use `snap_window_s` (default 1.5 s). | `ChunkService.snapTimeToCut` | Reads `settings.snap_window_s` |
| F5 | P2 | 3 remaining `alert()` calls. | `chunk.service.ts:53`, `studio/video-player.component.ts:373`, `modals/openrouter-modal.component.ts:223` | Replaced by toast / alert-dialog |
| F6 | P2 | Bulk **Reject all / Deselect all has no confirmation** (destructive). | `curate/*`, server `/curate/reject_all` | `alert-dialog` confirm |
| F7 | P2 | Caption dialog model list hard-coded; should use `GET /api/openrouter/models`. API key cached in `localStorage` (`cp_openrouter_api_key`). | `modals/openrouter-modal.component.ts` | Models from API; decide and document key persistence |
| F8 | P2 | Settings dialog: no validation (HTML min/max only), `|| default` hides `0`; `NaN` can reach `PUT /api/settings`. | `modals/settings-modal.component.ts` | Signal Forms with range validation |
| F9 | P2 | No Router: no URLs/deep links, no `?video=`. | `app.component.ts` (`currentTab` signal) | Decide hash routes vs keep tabs; document |
| F10 | P3 | Budget on add chunk: remainder to chunk 0 (legacy: first N chunks +1); max-15 uses `alert()`. | `chunk.service.ts` | Matches spec or deliberate change recorded |
| F11 | P3 | SSE reconnect only via EventSource defaults; no UI indication of a dropped stream. | `job-event.service.ts` | Optional: connection-state indicator |
| F12 | P3 | Playhead driven by `timeupdate` (~4 Hz), not `requestAnimationFrame`. | `video-player.component.ts` | Optional smoothness improvement |
| F13 | P3 | "Export this video" only as per-row "Re-Export" in the Dataset table; no Curate-toolbar action. | `dataset-view`, `curate-toolbar` | Product decision |

## 3. Backend / contract gaps

| # | Pri | Gap | Where |
| --- | --- | --- | --- |
| B1 | P1 | **`POST /api/export` without `video_id` skips `curating_300`** (selects `curating, chunked, exported`) - "Export All Curated" omits videos finished by extract-750. | `api.py` `run_export` |
| B2 | P2 | Mixed row shapes: `swap-frame` rows and `PUT /chunks` result lack `is_pick`, `is_captioned`, caption stats. Replacing state wholesale after swap/save loses fields; merge or refetch. | `api.py`, `curate.service.ts` |
| B3 | P2 | Chunk field names differ per endpoint (`chunk_pos` vs `chunk_position`/`chunk_start`...). `candidate-grid` handles this inline instead of one mapper. | `curate/candidate-grid.component.ts` |
| B4 | P2 | `PUT /api/settings` accepts arbitrary keys unvalidated. | `api.py` |
| B5 | P3 | `PATCH /api/picks` does not rebalance (prefer `swap-frame`). | `api.py` |
| B6 | - | Reconcile rewrites statuses at startup/scan (`exported` if exports >= picks, `curating_300` if picks, `chunked` if chunks, else `new`; skips `skipped/ignored/broken`). Know this before debugging "my status changed". | `reconcile.py` |

Previously reported mismatches that upstream fixed: SSE `StreamingResponse` import, distribution key names (Angular reads backend names - **not re-verified at runtime**).

## 4. Design-system / styling compliance gaps (full table with file:line in `studio-adaptations.md`)

- **Theme - DECIDED (product owner): mim26 design system, light-first.** The app currently starts dark (`<html class="dark">` in `index.html`, header theme toggle in `shell/header.component.ts`, custom `.dark` block in `styles.css` that mim26 does not define). Task T1: default to light, remove the toggle and the `.dark` block (preferred), verify all screens in light (media surfaces stay ink per `studio-adaptations.md`), re-check contrast per `colors.md`.
- Emoji used as icons in nearly every view -> lucide via `NgIcon`.
- Raw palette / hex: `bg-black/*`, `text-white`, `bg-yellow-400/80`, `ring-white`, `amber-500`; `'#D73816'` canvas stroke (`video-player.component.ts:465`) -> tokens / `getComputedStyle`.
- Hand-rolled modals, toasts, inputs, selects, status pills instead of `hlm-dialog`/`alert-dialog`/`hlm-toaster`/`hlmBadge`/helm form controls; only `HlmButton`, `HlmBadge`, `HlmTooltip` imported. `FormsModule/ngModel` (~42 uses) instead of Signal Forms.
- Signature type misuse: video filename as uppercase `h1`; page H1 should be the view name.
- `font-mono` for timecodes (8 files), Google Fonts still linked in `index.html`, 82 arbitrary `text-[Npx]` sizes -> `tabular-nums`, type scale.
- Layout/a11y: fixed pixel grids, `h-screen overflow-hidden`, no queue drawer below `lg`, `user-select: none` on `body` (blocks copying filenames/captions), title-attribute tooltips, no skip link / `main#main-content`, `aria-current` unverified, icon-only buttons not audited.

## 5. Tooling / hygiene gaps

- **No test target and no app specs.** `npm test` / `ng test` fails (`angular.json` has only `build`/`serve`). Which unit-test builder Angular 22 scaffolds is unverified. Only specs in the repo belong to the foreign `shared/components/**`.
- Dead code still present: `src/web/app.js` (1799 lines, not loaded), `src/web/shared/components/**` (another product; German copy; excluded from build). Delete after parity sign-off.
- Missing: `components.json` (needed by the Spartan CLI), `legacy/` was never created, Node install not part of `scripts/setup.sh`/`env.sh`.
- `node_modules` and the Angular cache sit on `/mnt/c` (slow 9p); no ext4 relocation / `cli.cache.path`. `scripts/patch_os.js` (stubs `os.networkInterfaces`) is an unexplained WSL workaround - cause **unverified**.
- `scripts/build_web.sh` / `start_all.sh` build with `--configuration development` (unoptimised); `start_all.sh` runs uvicorn without `--reload`.
- FastAPI serves `src/web` (raw TS) if `src/web/dist` is not built -> broken UI at `:8000`. Always build first; consider failing loudly instead.
- `zone.js` still used (not zoneless); `provideHttpClient()` registered but `ApiService` uses `fetch`, so no interceptor/central error handling.
- `scripts/` gained ~20 ad-hoc scripts (inspect/check/test); several named `test_*.py` are not pytest tests - confirm `pytest` collection only targets `tests/`.
- Prompt Director (RAG) view: new, undocumented in legacy spec beyond a short section; backend returns 404 "RAG index not built yet" until `scripts/index_rag.py` is run. Top-level keys of the `/api/rag/generate` response were not verified.

## 6. Parity checklist (from `studio-ui-spec`, confirmed in code)

Ticked: startup + queue, timeline, SSE pills/toasts/cancel, curate grid/filters/actions/lightbox (prev/next added), caption + settings dialogs (with caveats), dataset + export.
Open: player shortcuts (F1), chunk editing/extract flow (F2), all statuses selectable (F3), no `alert()` (F5).

## 7. Suggested order for the next agent

1. Verify the baseline in WSL: `npm install`, `npm run build`, `bash scripts/test.sh`, `bash scripts/start_all.sh`, open `:8000`, click through the golden path (select video -> suggest -> save -> extract -> curate -> caption -> export). Record what actually fails; this report has no runtime evidence.
2. F1 hotkeys, F2 extract flow, F3 status select, B1 export-all (small, high impact; add backend test for B1).
3. Add a `test` target + specs for `time.utils`, chunk budget/snap, `CurateService` filters, `HotkeyService`.
4. Do T1 (light-first, remove dark toggle/block), then the compliance pass (icons, tokens, hlm primitives, Signal Forms) screen by screen, always against the light theme.
5. Tooling: ext4 Node/cache, Node in `setup.sh`, production build in the start script, drop Google Fonts.
6. Cleanup: delete `app.js` and `shared/components/**`, resolve `.agents/skills` duplication, drop the `skills-wip` stash, commit.
7. Keep the skills current: update `angular-migration` (remaining work) and `studio-ui-spec` (port status) as items close.

## 8. Unverified / needs a human

Per-video export placement; Router vs tabs; API key persistence; `patch_os.js` cause; Angular 22 test builder; accessibility rows (grep-checked only); `/api/rag/generate` response shape.
