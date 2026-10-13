---
name: angular-migration
description: Current-state guide to the Angular 22 + Tailwind v4 + Spartan Studio frontend (src/web/app) that replaced the vanilla JS UI - folder layout, signal-service architecture, SSE, hotkeys, build/serve/proxy and FastAPI static serving, WSL Node tooling, conventions for adding features, deviations from the original plan, parity gaps and the remaining-work list. Use when adding or changing any frontend component/service, wiring the build or FastAPI serving, running the web dev workflow, or finishing the migration (legacy cleanup, tests, hotkeys).
---

# Angular frontend - current state

Read with `studio-ui-spec` (behaviour + parity status), `api-contract` (backend shapes), `styling` + `design-system` (rules; compliance gaps listed in `design-system/references/studio-adaptations.md`).

The migration landed in upstream commit `258a2d3` ("RAG-powered Krea Prompt Clicker"). It is a working first pass, **not** a finished migration: see "Gaps".

## Stack and files (verified)

- Angular 22 (`^22.1.8`), TypeScript ~6.0, Tailwind v4 via `@tailwindcss/postcss` (`.postcssrc.json`), `@spartan-ng/brain ^1.6.3` + vendored helm, `@ng-icons/lucide`, `class-variance-authority`, `tailwind-merge`. **`zone.js` is in use** (`polyfills: ["zone.js"]`, `provideZoneChangeDetection({eventCoalescing:true})`) - not zoneless.
- `angular.json`: one project `captioning-pipeline`, `sourceRoot: src/web`, builder `@angular/build:application`, `browser: src/web/main.ts`, `index: src/web/index.html`, styles `src/web/styles.css`, **`outputPath: src/web/dist`** (so the app ends up in `src/web/dist/browser`). Only `build` and `serve` targets exist - **there is no `test` target**, so `npm test` / `ng test` fails. Production budgets: initial 2 MB warn / 5 MB error, component styles 10/20 kB.
- `tsconfig.json`: strict + `strictTemplates`; path aliases `@spartan-ng/helm/*` -> `src/web/shared/ui/*/src/index.ts` and `@app/*` -> `src/web/app/*` (the app itself mostly uses relative imports). `tsconfig.app.json` excludes `*.spec.ts` and `src/web/shared/components/**`.
- `src/web/index.html` is 15 lines: `<html lang="en" class="dark">`, `<base href="/">`, Google Fonts links (Plus Jakarta Sans/JetBrains Mono - unused by the mim26 `font-sans` stack, remove), `<body class="bg-canvas text-foreground h-screen overflow-hidden"><app-root>`.
- `src/web/styles.css` (144 lines): Tailwind import + Spartan preset, `@theme` mapping and the **mim26 tokens** (`:root` light) plus a custom `.dark` block (dark surfaces, same primary `#D73816`), reduced-motion rule, thin scrollbars. `body` has `user-select: none`.
- **`src/web/app.js` is still in the repo** (1799 lines, legacy, even received the per-chunk caption-model feature upstream) but nothing loads it any more (new `index.html` only mounts `app-root`). `legacy/` was never created. It is dead code kept as a reference: delete it once parity is accepted.
- `src/web/shared/ui/**` = 255 vendored helm files (45 primitive folders, unchanged). **Only 4 helm imports are used by the app**: `badge`, `button`, `tooltip`, `utils` (`provideSpartanHlm()` in `app.config.ts`).
- `src/web/shared/components/**` (marketing leftovers from another product, with their own `.spec.ts`) **still exist**; they are excluded from the build. They are the only specs in the repo. Delete them.
- No `components.json` yet (needed by the Spartan CLI generator, see `styling`).

## Architecture as built

```
src/web/main.ts                 bootstrapApplication(AppComponent, appConfig)
src/web/app/
  app.config.ts                 provideZoneChangeDetection, provideHttpClient (unused by ApiService), provideSpartanHlm
  app.component.ts              shell + tab state + hotkey registration + SSE start (no Router)
  core/models/                  video, chunk, candidate, job, dataset, settings, prompt-rag
  core/services/                api, video, chunk, curate, dataset, settings, job-event, hotkey, prompt-rag
  core/utils/time.utils.ts      formatTime, formatShortTime, formatHms, parseTimestamp
  features/shell/               header (tabs, job pills, theme toggle), video-queue, toast-container
  features/studio/              studio-view, video-player (controls + watermark canvas), timeline, chunks-panel
  features/curate/              curate-view, curate-toolbar, distribution-bar, candidate-grid
  features/dataset/             dataset-view
  features/modals/              lightbox, settings, openrouter (caption)  - hand-rolled overlays, not hlm-dialog
  features/prompt-clicker/      Prompt Director / RAG view (new, not in legacy)
```

- **State**: `providedIn: 'root'` services holding `signal()`/`computed()` (`VideoService`, `ChunkService`, `CurateService`, `DatasetService`, `SettingsService`, `JobEventService`, `PromptRagService`). Components are standalone, `OnPush`, inline `template:` strings, `@if/@for/@switch`, `input()/output()` mix with decorators (`@ViewChild`, `@HostListener`).
- **Navigation**: `AppComponent.currentTab` signal (`studio | curate | dataset | prompt-clicker`); **no Angular Router, no URLs/deep links** (deviation from the plan, which proposed hash routes). Queue sidebar hidden on the prompt-clicker tab. An `effect()` reloads chunks + filmstrip when `VideoService.currentVideo` changes and candidates if the curate tab is open.
- **HTTP**: `ApiService` is a thin `fetch` wrapper (`get/post/put/patch/delete`, throws `Error(detail)`); `provideHttpClient()` is registered but unused. No interceptor; errors are mostly `console.error`/`alert`.
- **SSE**: `JobEventService.initSSE(onFinished)` opens `EventSource('/api/events')`, keeps `activeJobs: Map<job_id, ActiveJob>`, raises toasts (own `toasts` signal + `ToastContainerComponent`, 9 s), `cancelJob()` -> `DELETE /api/jobs/{id}`. Distinguishes caption vs extraction by `job_type`/`type`/`exported`. `onerror` only logs (EventSource auto-reconnect). The backend `StreamingResponse` import bug is fixed upstream (`api.py` now imports it).
- **Hotkeys**: `HotkeyService.handleKeyDown` (bound via `@HostListener('window:keydown')` in `AppComponent`) implements the key map and the INPUT/SELECT/TEXTAREA guard (Esc blurs). **Only 5 actions are ever registered**: `next_video`, `escape`, `keep`, `reject`, `star` (the last three act on the open lightbox). `play_pause`, `seek_*`, `frame_*`, `mark_in/out`, `add_chunk`, `toggle_snap_cuts`, `jump_focus` have no handler -> those shortcuts do nothing today. The service has no `J/L` or `Alt+Arrow` cases.
- **Player/timeline**: `VideoPlayerComponent` owns the `<video>`, `currentTime` signal (fed by `timeupdate`), seek/frame-step (`1/fps`), speed, In/Out (snapped through `ChunkService.snapTimeToCut`, fixed 0.5 s window - not `settings.snap_window_s`), jump input (`parseTimestamp`), and the **watermark rectangle canvas** (normalised 0-1 rect, saved by `VideoService.saveWatermark` -> `PATCH /videos/{id}`). `TimelineComponent` renders filmstrip, cut markers, chunk bands, hover preview with template bindings.
- **Forms**: `FormsModule` + `ngModel` (about 42 usages) - violates `styling` rule 6 (Signal Forms). Native `<select>`/`<input>`/`<kbd>` are hand-styled, not helm.

## Build, run, serve

- **Dev**: `scripts/start_web.sh` (or `npm start`): `ng serve --proxy-config proxy.conf.json --host 0.0.0.0 --port 4200`; `proxy.conf.json` forwards `/api` and `/static` to `http://127.0.0.1:8000`. Run FastAPI separately with `scripts/run.sh`.
- **Build**: `scripts/build_web.sh` runs `ng build --configuration development` (note: development, unoptimised) -> `src/web/dist/browser`. `scripts/start_all.sh` = build, then `uvicorn pipeline.api:app` on :8000 (no `--reload`). `npm run build` is the production config. `src/web/dist/`, `dist/`, `node_modules/`, `.angular/` are git-ignored.
- **FastAPI serving** (`api.py` bottom): `DIST_DIR = src/web/dist/browser` (falls back to `src/web/dist`), `STATIC_DIR = DIST_DIR` if it has `index.html`, else `src/web` (legacy). Mounts `StaticFiles` at `/static`, and a catch-all `GET /{full_path:path}` returns an existing file under `STATIC_DIR` or `index.html` (SPA fallback; this is what makes `<base href="/">` + `/main-*.js` work). If `dist` is not built the server serves the raw `src/web` folder (a TS source tree - broken UI), so **always build before opening `:8000`**.
- **Node on WSL** (AGENTS.md: never run Node on Windows): scripts `cd` to the repo root and export `NODE_OPTIONS="--require scripts/patch_os.js --max-old-space-size=2048"`, `NG_CLI_ANALYTICS=false`, `NG_DISABLE_VERSION_CHECK=1`, and write `~/.angular-config.json`. `scripts/patch_os.js` overrides `os.networkInterfaces()` with a static dummy; it is evidently a workaround for the Angular CLI/Node failing to enumerate network interfaces in this WSL setup (the repo does not state the cause - unverified). Scripts call `./node_modules/.bin/ng`, so `npm install` must have been run in WSL first.
- **Not handled**: `node_modules` and the Angular cache live in the repo on `/mnt/c` (no ext4 relocation, no `cli.cache.path`); `scripts/web.sh`/Node install is not part of `setup.sh`/`env.sh`. Builds will be slow on 9p.

## Conventions when adding features

1. State in a root service with signals; components inject it, no component-level HTTP. Add the backend shape under `core/models/` and map backend inconsistencies in the service (see `api-contract`, e.g. `chunk_pos` vs `chunk_position`, which `candidate-grid` currently handles inline).
2. Standalone + `OnPush`, `@if/@for`, `inject()`. Use the helm primitives (`@spartan-ng/helm/*`) and the `hlm()` class merger; generate missing primitives into `src/web/shared/ui` (`switch`, `kbd`, `slider`, `toggle`, `radio-group` are not vendored).
3. Styling rules: tokens only, lucide icons (`NgIcon` + `provideIcons`), no emoji, no raw palette classes, no `alert()` - use `JobEventService.showToast` or an alert-dialog. Existing code violates this in places (list in `studio-adaptations.md`); do not copy those patterns.
4. Register new shortcuts through `HotkeyService.register` from the component that owns the behaviour, and clean up with the returned disposer.
5. New backend calls: add/extend a test in `tests/` (`tests/test_curation_api.py`, `tests/test_rag.py` exist); keep fixture-video usage.
6. Type-check with the production build (`npm run build`) - strict templates catch most errors; there is no test runner yet.

## Deviations from the original plan

zone.js kept (plan: zoneless-compatible); no Router (plan: hash routes); `fetch` service instead of `HttpClient` + interceptor; output in `src/web/dist` and the SPA catch-all in `api.py` (plan: `CP_WEB_DIR`); no `legacy/` move and `app.js` left in place; no `components.json`, no ext4 Node handling, no `web.sh`; `shared/components` not deleted; helm barely used (hand-rolled modals/inputs); no unit tests; Google Fonts still linked; dark theme toggle exists (`html.dark` default on, header button) although the adopted mim26 design system is light-first (**decided: default light**; remove `class="dark"` in `index.html`, the header toggle and the `.dark` block in `styles.css`).

## Remaining work (priority order)

1. **Hotkeys**: register the 14 missing actions (player/timeline/chunks), add `J/L` and `Alt+Arrow`, scope `Enter` to the Studio tab (the old guard) and avoid acting while a modal is open.
2. Replace the three `alert()` calls (`chunk.service.ts:53`, `video-player.component.ts:373`, `openrouter-modal.component.ts:223`) with toasts; add confirms for destructive bulk actions (`bulkRejectAll`).
3. Tests: add a `test` target (check which unit-test builder the Angular 22 CLI scaffolds; unverified here), cover `time.utils`, chunk budget/snap logic, `CurateService` filters, `HotkeyService`; remove the foreign `shared/components` specs.
4. Compliance gaps from `studio-adaptations.md` (emoji, raw palette, hex canvas colour, hlm adoption, Signal Forms, tooltips, status badge coverage).
5. Tooling: `ext4` node_modules/cache, Node install in `setup.sh`, `web.sh`, production build in `start_all.sh`, drop Google Fonts, consider `ng build --watch` + uvicorn `--reload` workflow.
6. Cleanup: delete `src/web/app.js`, `src/web/shared/components/**`; decide on Router/deep links; add `?video=` state.
7. Backend/contract follow-ups still open: `Export All` status filter, distribution key mapping (Angular reads the backend names correctly), settings validation.
