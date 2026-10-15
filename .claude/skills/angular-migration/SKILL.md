---
name: angular-migration
description: Current-state guide to the Angular 22 + Tailwind v4 + Spartan Studio frontend (src/web/app) that replaced the vanilla JS UI - folder layout, signal-service architecture, SSE, hotkeys, build/serve/proxy and FastAPI static serving, WSL Node tooling, conventions for adding features, deviations from the original plan, parity gaps and the remaining-work list. Use when adding or changing any frontend component/service, wiring the build or FastAPI serving, running the web dev workflow, or finishing the migration (legacy cleanup, tests, hotkeys).
---

# Angular frontend - current state

Read with `studio-ui-spec` (behaviour + parity status), `api-contract` (backend shapes), `styling` + `design-system` (rules; compliance gaps listed in `design-system/references/studio-adaptations.md`).

The migration landed in upstream commit `258a2d3` ("RAG-powered Krea Prompt Clicker"). It is a working first pass, **not** a finished migration: see "Gaps".

## Stack and files (verified)

- Angular 22 (`^22.1.8`), TypeScript ~6.0, Tailwind v4 via `@tailwindcss/postcss` (`.postcssrc.json`), `@spartan-ng/brain ^1.6.3` + vendored helm, `@ng-icons/lucide`, `class-variance-authority`, `tailwind-merge`. **`zone.js` is in use** (`polyfills: ["zone.js"]`, `provideZoneChangeDetection({eventCoalescing:true})`) - not zoneless.
- `angular.json`: one project `captioning-pipeline`, `sourceRoot: src/web`, builder `@angular/build:application`, `browser: src/web/main.ts`, `index: src/web/index.html`, styles `src/web/styles.css`, **`outputPath: src/web/dist`** (so the app ends up in `src/web/dist`). Unit tests configured with `@angular/build:unit-test` runner `vitest` (`npm test` executes the 42 unit tests). Production budgets: initial 2 MB warn / 5 MB error, component styles 10/20 kB.
- `tsconfig.json`: strict + `strictTemplates`; path aliases `@spartan-ng/helm/*` -> `src/web/shared/ui/*/src/index.ts` and `@app/*` -> `src/web/app/*` (the app itself mostly uses relative imports).
- `src/web/index.html`: light theme default, `<base href="/">`, `<body class="bg-canvas text-foreground h-screen overflow-hidden"><app-root>`.
- `src/web/styles.css`: Tailwind import + Spartan preset, `@theme` mapping and the **mim26 tokens** (`:root` light), reduced-motion rule, thin scrollbars.
- `src/web/app.js` and `src/web/shared/components/**` have been completely removed.
- `components.json` configured for Spartan UI with Vega style and `@spartan-ng/helm` path aliases.
- Zero `ngModel` / zero `FormsModule` across the entire app (all two-way bindings use direct signals).

## Architecture as built

```
src/web/main.ts                 bootstrapApplication(AppComponent, appConfig)
src/web/app/
  app.config.ts                 provideZoneChangeDetection, provideHttpClient, provideSpartanHlm
  app.component.ts              shell + tab state + hotkey registration + SSE start + Board / Workflow navigation
  core/models/                  video, chunk, candidate, job, dataset, settings, prompt-rag
  core/services/                api, video, chunk, curate, dataset, settings, job-event, hotkey, prompt-rag, workflow
  core/utils/time.utils.ts      formatTime, formatShortTime, formatHms, parseTimestamp
  core/ui/status.ts             unified videoStatusMeta mapping
  features/shell/               header, video-queue, toast-container, workflow-bar
  features/pipeline-board/      pipeline-board (all videos by stage, batch rescan/export)
  features/studio/              studio-view, video-player (controls + watermark canvas), timeline, chunks-panel
  features/curate/              curate-view, curate-toolbar, distribution-bar, candidate-grid
  features/dataset/             dataset-view
  features/modals/              lightbox, settings, openrouter (caption)
  features/prompt-clicker/      Prompt Director / RAG view
```

- **State**: `providedIn: 'root'` services holding `signal()`/`computed()` (`VideoService`, `ChunkService`, `CurateService`, `DatasetService`, `SettingsService`, `JobEventService`, `PromptRagService`, `WorkflowService`). Components are standalone, `OnPush`, inline `template:` strings, `@if/@for/@switch`, `input()/output()` mix with decorators (`@ViewChild`, `@HostListener`).
- **Workflow State Machine**: `WorkflowService` derives step progression (Chunk -> Extract -> Curate -> Caption -> Done) and computes the single primary `nextAction` with `whyDisabled` context. Displayed in `WorkflowBarComponent` and `PipelineBoardComponent`.
- **Navigation**: `AppComponent.currentTab` signal (`board | studio | curate | dataset | prompt-clicker`), defaulting to `board`.
- **Testing**: Vitest suite configured in `angular.json` testing `time.utils`, `chunk.service`, `curate.service`, `hotkey.service`, and `hlm-input`.
