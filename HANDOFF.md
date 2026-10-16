# Handoff - design overhaul, pipeline review, Prompt Director fix, Phases A-E, Database Reset, Dockerization, Face Recognition & Multi-Person Roles / 95% Dedup / Video Hash

Date: 2026-10-16 (session 5). Branch `session5-image-tagging-and-handoff` (not merged, not pushed).
**Session 5 (§1.9):**
- learn-as-you-tag image tagging (new feature)
- Phases F, G and H complete
- product decisions 1, 3 and 4 implemented as proposed
- every GAP_REPORT item closed
- tooling and the skills mirror

Earlier phases (A-E, 1.5-1.8) are unchanged and DONE. Session 5 ran on **macOS** (scratch venv, temp `CP_*` dirs,
headless Chrome against the real CLIP model). Nothing has run in WSL yet: §5 is the next step.

## 0. Read first

| Document | Content | IDs used below |
| --- | --- | --- |
| `GAP_REPORT.md` | Angular migration gaps (prior handover) | F1-F13, B1-B6, T1 |
| `docs/reviews/pipeline-review.md` | Backend correctness, performance, new extraction, quality, diversity and caption process | C1-C4, H1-H9, M1-M10 |
| `docs/reviews/design-overhaul-plan.md` | View-by-view design audit, workflow IA, wireframes, spacing, colour, contrast | Q1-Q14, P1-P10, W1-W10, K1-K5 |
| `docs/reviews/prompt-clicker-review.md` | Prompt Director root cause, changes made, design and RAG review | R1-R10 (= its §5 table rows) |
| `.claude/skills/*` | Domain, API contract, UI spec, Angular architecture, styling and design-system rules | - |

Hard rules (unchanged): all Python/Node/uv commands run **inside WSL 2** on the Windows host (`AGENTS.md`).
Tests use fixtures, never the video library. Never commit `.env`, `*.db`, frames or `data/`.

## 1. What this session did

The work ran on a **macOS** machine, with no WSL, `uv` or `ffmpeg`. Three subagents ran in parallel:

1. **Design review** (read-only). The report is `docs/reviews/design-overhaul-plan.md`.
2. **Pipeline review** (read-only, static analysis). The report is `docs/reviews/pipeline-review.md`.
3. **Prompt Director ("clicker tool") fix** (code changes) plus a review. The report is `docs/reviews/prompt-clicker-review.md`.

### 1.1 Prompt Director: root cause and fix (code changed, uncommitted)

**Why it was "not available":**
- The tab always existed and compiled. But with no RAG index, `/api/rag/retrieve` and `/api/rag/generate`
  returned a bare 500, and the UI showed only "Internal Server Error".
- The index could almost never be built: `scripts/index_rag.py` read only `data/curated_2500/{train,val}`,
  which only the ad-hoc `scripts/curate_dataset.py` writes. With 0 captions found, it wrote nothing.
- A missing API key, an unreachable Ollama or an upstream 4xx were also bare 500s. "Local Ollama" with no URL crashed.

**Changed files:**
- `src/pipeline/rag/{__init__,generator,indexer,linter,retriever}.py`
- new `src/pipeline/rag/index_job.py`
- RAG routes plus the `/api/*` catch-all 404 in `src/pipeline/api.py`
- `scripts/index_rag.py`
- `src/web/app/core/{models/prompt-rag.model.ts,services/prompt-rag.service.ts}`
- `src/web/app/features/prompt-clicker/prompt-clicker-view.component.ts` (rewritten: helm controls,
  lucide icons, no `ngModel`, data-driven categories)
- new `tests/test_rag_api.py` (11 tests)

**New behaviour:**
- `GET /api/rag/status` and `POST /api/rag/index` run a background build, separate from `JobManager`
  because those jobs are per-video.
- The indexer falls back to `finished/merged` and writes atomically. The server hot-reloads a rebuilt index.
- Retrieve returns an empty result with `index_ready: false`. Generate works ungrounded (`grounded`, `notices`).
- Errors are readable: 400 for a missing key, 502 for an upstream failure.
- Retrieval adds MMR and a 0.98 near-duplicate cutoff. Embeddings are matched to captions by id.
- `is_valid` in the linter is fixed.

**Verification (Mac only):**
- `ng build --configuration production` is clean.
- `pytest tests/`: 48 passed, 1 failed. The failure is `test_phase1.py::test_api_routes`, which needs the
  `/mnt/c/.../_girls` library (environmental).
- Headless Chrome, end to end with 40 synthetic captions: the guided panel rendered, the build showed
  progress, preview returned exemplars, and generate ran against a fake Ollama.
- **Not yet run in WSL**; the commands are in §5.

### 1.3 Phase A: data-loss and security fixes (session 3, done)

Also run on macOS (no WSL/uv/ffmpeg). Tests ran in a scratch venv with `CP_CACHE_DIR`/`CP_FINISHED_DIR`/`CP_VIDEO_DIR`
pointed at temp dirs (the default `/mnt/c` paths make the lifespan fail on a Mac).

| # | What changed | Where |
| --- | --- | --- |
| A1 | The exporter never overwrites a VLM caption: frames with a caption are only re-linked and recorded. Stubs are written only for uncaptioned frames and marked by a `.json` sidecar with `caption_source: "stub"` (legacy sidecars without the field count as stubs). `existing_caption_kind()` classifies files. The caption job replaces stubs, deletes their sidecars, and also re-captions captions cut off mid-sentence. | `export.py`, `jobs.py` |
| A2 | Export-all selects `curating, curating_300, chunked`, skips `exported` and videos with a queued/running job. **New finding:** the Angular `DatasetService` called `POST /api/videos/{id}/export` and `POST /api/export_all`, which never existed, so UI export was broken; it now calls `POST /api/export`. | `api.py`, `dataset.service.ts` |
| A3 | Chunks are upserted by id; only removed chunks are deleted. A moved range marks the chunk `dirty` and keeps its frames. Removing a chunk with picks/exports (PUT or Suggest) returns **409** unless `confirm_destructive: true`. Saving no longer demotes a curated/exported video to `chunked`. UI: `ApiError` (status + detail) and a confirm-then-retry in the chunks panel (**interim `window.confirm`; B4 replaces it with an alert-dialog**). | `autochunk.py`, `api.py`, `api.service.ts`, `chunk.service.ts`, `chunks-panel.component.ts` |
| A4 | Reasoning defaults to off (`"none"`, also in the caption dialog and `DEFAULT_USER_SETTINGS`); the 300-token cap is gone (default 600, reasoning forces ≥1200). A reply with `finish_reason == "length"`, no closing sentence, or under half the mode's minimum words is retried twice without reasoning and with a doubled budget; still cut off → `CaptionTruncatedError`. Word-range warnings per mode. Refusals are detected before the retry so they are not billed twice. New read-only `scripts/audit_captions.py` (word histogram, truncated, stubs, `--list`, `--json`). | `openrouter.py`, `jobs.py`, `api.py`, `config.py`, `openrouter-modal.component.ts` |
| A4+ | Pulled forward from C7 because A4 makes per-frame failures more likely: one frame's error no longer fails the caption job. The job ends `finished` with a failure summary in `jobs.error`; the video becomes `exported` only if every keep/star frame has an export. | `jobs.py` |
| A5 | `cancel_job` refuses finished jobs; a queued job is cancelled at once and its video settled; workers start only if the row is still `queued`. Extract-750 checks the flag between stages and before writing files or committing, so a cancel never deletes frames. The caption job ends `cancelled`, not `exported`. Flags are cleared in `finally`. Error and cancel paths share one "settle video status" SQL (`exported` / `curating_300` / `chunked` / `new`); auto-process and legacy extract errors no longer leave videos in `extracting`. | `jobs.py` |
| A6 | Reconcile counts keep/star frames for "fully exported", so rejected picks no longer demote `exported` videos. Chunk stats add `approved_count`. | `reconcile.py` |
| A7 | SSE: `loop.call_soon_threadsafe` per listener, queues bounded at 500 (drop oldest), `: keepalive` every 15 s. | `jobs.py` |
| A8 | `serve_spa` and the filmstrip route contain resolved paths in their root. CORS only for `localhost:4200`/`127.0.0.1:4200`, no credentials. `GET/PUT /settings` never return `openrouter_api_key` (only `openrouter_api_key_set`); a PUT with an empty key keeps the stored one. The caption job also reads the stored key. `run.sh`, `start_all.sh` and `start_web.sh` bind `127.0.0.1`. | `api.py`, `jobs.py`, `scripts/*.sh` |
| A9 | `httpx` moved to runtime dependencies. **`uv.lock` was edited by hand** (no uv on the Mac); `uv sync` re-locks if anything disagrees. | `pyproject.toml`, `uv.lock` |

**Tests:** new `tests/test_phase_a.py` (16 tests, temp DB, one or more per item). `test_openrouter.py` now asserts
the new token contract (the old asserts encoded the 300 cap) plus 6 truncation tests. `test_full_pipeline.py::test_concurrent_job_manager`
was racy (the jobs finished before the cancel) and now holds jobs in the queue and checks the new cancel semantics.
**Result: 70 passed** (`test_phase1::test_api_routes` deselected: needs the library). `ng build --configuration production` is clean.

**Not done in Phase A, on purpose:**
- Extract-750 still deletes and replaces all frames of a video when it commits. That is the explicit "re-extract" action; the UI guard is B4.
- The short-video branch of `generate_balanced_chunks` (duration < 25 s) still returns unsaved chunks. This is unreachable with `min_duration_s = 30`.
### 1.4 Phases B, C, D, E & H Implementation & WSL Verification (session 4, 2026-10-15, done)

All work executed directly inside Ubuntu WSL 2 using Python 3.12 (`uv`), Node, and Angular CLI.

| Phase | What changed | Where |
| --- | --- | --- |
| **Phase B** | Light-first styling default, unified status meta (`core/ui/status.ts`), dirty chunks flag and unsaved indicator, B4 re-chunk confirmation dialog before destructive re-extract, hotkey safeguards on interactive targets, typed toast actions, tabular-nums formatting, settings modal validation, curate loading skeleton. Added Vitest unit test suite with 42 specs. | `status.ts`, `hotkey.service.ts`, `chunks-panel.component.ts`, `angular.json`, `*.spec.ts` |
| **Phase C** | Re-decode native high-resolution frame when a runner is promoted or swapped in (`native=1`); similarity-based replacement ranking on frame swap; per-video job locks rejecting concurrent extraction/caption jobs with HTTP 409 (`JobConflictError`); benchmark baseline recorded in `~/.cache/captioning_pipeline/bench_results.json`. 9 regression tests added. | `decode.py`, `jobs.py`, `scoring.py`, `test_phase_c.py` |
| **Phase D** | Configured `components.json` with Vega style and `@spartan-ng/helm` path aliases; removed `FormsModule` and all 42+ `ngModel` usages replacing them with direct signal two-way bindings; loaded dynamic models from `GET /api/openrouter/models` cached in `SettingsService`; implemented `HlmProgress` with category health checks and alert icons on out-of-range distributions; card roving focus, Enter/Space activation, state badges (`Star`, `Kept`, `Reject`), accessible labels and landmarks. | `components.json`, `candidate-grid.component.ts`, `distribution-bar.component.ts`, `lightbox-modal.component.ts`, `openrouter-modal.component.ts` |
| **Phase E** | Built 5-step progression state machine (`WorkflowService`: Chunk ➔ Extract ➔ Curate ➔ Caption ➔ Done); created `PipelineBoardComponent` landing view with stage filter chips, search, and next action buttons; built `WorkflowBarComponent` workspace header with stepper and single primary next action; added live SSE connection state indicator (`JobEventService.connectionState`); updated backend `list_videos` SQL query to include `kept_count` and `captioned_count`. | `workflow.service.ts`, `pipeline-board.component.ts`, `workflow-bar.component.ts`, `header.component.ts`, `api.py` |
| **Phase H** | Aligned adult guardrails (21+) across generator prompt and character profiles; updated Lene character avatar to adult female; implemented source video capping (`max_per_source` parameter with MMR filtering in `RagRetriever`) preventing exemplar repetition from the same source video. Added `test_max_per_source_capping`. | `generator.py`, `taxonomy.py`, `retriever.py`, `test_rag_api.py` |
| **Phase I** | Deleted legacy `src/web/app.js` and foreign leftover component specs; synchronized `angular-migration/SKILL.md` and `api-contract/SKILL.md` across `.agents/skills` and `.claude/skills`. | `app.js`, skills directories |

**WSL 2 Verification Results:**
- `bash scripts/test.sh`: **81 passed in ~8s** (all 81 Python tests across 10 test suites).
- `npm test`: **42 passed in ~1-2s** (all 5 Vitest suites: `time.utils`, `chunk.service`, `curate.service`, `hotkey.service`, `hlm-input`).
- `npm run build`: **Clean production build in ~5s** (output location: `src/web/dist`, bundle size: 655 kB, 0 errors).

### 1.2 Headline findings (details in the reports)

**Data-loss bugs (all fixed in Phase A, §1.3).**
- **C1:** Export overwrites VLM captions with a trigger-only stub, and export-all already includes `exported` videos.
- **C2:** Chunk save or suggest deletes all frames, picks and exports through `ON DELETE CASCADE`.
- **C3:** Captions are truncated: reasoning is "high" with `max_tokens` 300.
- **C4:** Cancel corrupts state.
- **H3:** Reconcile counts rejected picks and demotes `exported` videos.

**Security (H7):**
- `serve_spa` has no path containment.
- CORS is `*` with credentials.
- The API key is returned from `GET /settings`.

**Design:**
- There is no workflow guidance: four equal tabs, and nothing driven by `video.status`.
- 23 text pairs fail WCAG AA contrast, mostly the queue status badges, budget badges and the "Captioned" chip.
- Brand red is overused.
- Keyboard use is mostly broken (F1). Disabled buttons cannot show why they are disabled (`hlm-button`
  sets `pointer-events: none`).
- Toasts always say "Curate Now".

**Pipeline quality:**
- Extract-750 has no hard gates or dedup.
- The category bonus (up to 0.75) outweighs the 0-1 quality score.
- CLIP centre-crop embeddings make diversity nearly inert.
- Runner frames are stored at 720 px or less, so swaps export low-resolution images.
- Memory is about 6 GB per 1080p job.

**RAG:**
- The CLIP text encoder truncates at 77 tokens: the dense score sees only about the first 55 words of each caption.
- Tag matching is substring-based (`light` matches "slightly").

## 2. Repository state

- **Session 5 is uncommitted** (about 100 changed/new files). Suggested commits: (1) image tagging, (2) Phase F,
  (3) Phase G, (4) Phase H, (5) frontend gap + styling pass, (6) tooling/docs. 13 ad-hoc one-off scripts were
  deleted from `scripts/` (person-specific extract/caption/repair scripts, requeue/check helpers).
- **Skills:** `.claude/skills` is canonical; `.agents/skills` is a mirror. Run `bash scripts/sync_skills.sh`
  after editing a skill (`--check` reports drift). The `skills-wip` stash no longer exists.
- **Build artefacts:** `node_modules/` and `src/web/dist/` were built on macOS (gitignored). Do not copy them to
  WSL; run `npm install` / `bash scripts/build_web.sh` there.
- **Tests:** `bash scripts/test.sh` collects `tests/` only; `test_phase1::test_api_routes` now skips without the
  real library. `npx ng test --watch=false` runs the Vitest specs.

## 3. Decisions needed from the product owner

These block or shape the items listed in brackets.

1. ~~Character vs concept LoRA profile~~ **Implemented (session 5) as proposed:** per-video `profile`
   (`concept|character`, workflow-bar toggle), the target person is the character, identity gate + solo portrait crops
   (`pipeline/profiles.py`), character caption mode by default. Original notes:
   - The current defaults (`openrouter_mode="concept"`, a single trigger, a 50% multi-person target) suit a
     concept LoRA, but the stated goal is a character LoRA.
   - Proposal: a per-video or per-chunk `profile` + `character_id`, an identity gate, and solo or cropped frames.
2. ~~Angular Router vs tabs~~ **Decided (session 5):** hash deep links without the Router
   (`#/curate?video=<id>`, `core/utils/hash-route.utils.ts`); back/forward work. Revisit only if real routes are needed.
3. ~~**Status UI** [F3, W3]: replace the status `<select>` with a derived status plus an overflow menu
   (Skip / Restore / advanced)?~~ **Implemented (2026-10-16):** `features/studio/video-status-menu.component.ts`
   (badge + dropdown-menu: Skip, Restore to `derivedVideoStatus`, Advanced submenu with confirm for non-derived jumps).
4. ~~**Tokens** [Q2, 6.5 in the design plan]: a new `info` badge variant, plus optional `--success-strong` and
   `--warning-strong` tokens.~~ **Implemented (2026-10-16):** `info` variant, `--success-strong #15803D` /
   `--warning-strong #8A5A00` in `styles.css`, documented in `design-system/references/colors.md`.
5. **Export semantics** [W9]: does the caption job count as export ("Done" right after "Caption"), or is
   export a separate step? (Session 5 added an explicit "Export this video" action in the Curate toolbar; the
   workflow state machine is unchanged.)
6. ~~API key persistence~~ **Decided (session 5): server-only.** The UI saves a typed key via `PUT /api/settings`
   and migrates an old `localStorage` key once; nothing reads `localStorage` any more.
7. ~~Prompt Director guardrail wording~~ **Done (Phase H, session 4):**
   - The generator system prompt says "adults 25+", while both character profiles specify age 21.
   - Make the rule consistent: a clear adult-only rule, with each character's adult age stated explicitly.
   - Replace the `CHARACTERS["lene"]["avatar"]` emoji, which no longer renders, or drop the field.

## 4. Implementation plan (ordered)

Each item lists its source IDs. Effort is for one developer and includes tests.

### Phase A - Stop the bleeding - **DONE (session 3, §1.3)**

Kept for reference; the regression tests are in `tests/test_phase_a.py`.

| # | Work | Source | Notes |
| --- | --- | --- | --- |
| A1 | Exporter never overwrites an existing VLM caption. Interim: drop `exported` from the export-all query and skip existing `.txt` files. | C1 | **Must land before A2.** |
| A2 | Export-all includes `curating_300`. | B1 | Only after A1. |
| A3 | Chunk upsert by id; delete only removed ids; mark changed ranges `dirty`. Backend requires an explicit confirm flag for a destructive re-chunk when picks exist. | C2 | Prerequisite for B3/B4 (save-before-extract). |
| A4 | Captioning: reasoning off (or `effort: low`, `max_tokens` ≥ 1200). Reject `finish_reason == "length"` and retry. Word-count validation. Audit existing captions for truncation and stubs. | C3 | |
| A5 | Cancellation: check the flag before destructive writes, no DB mutation on cancel, check at job start, clear flags at the end. | C4 | |
| A6 | Reconcile counts only `keep`/`star` picks. | H3 | |
| A7 | SSE: `loop.call_soon_threadsafe`, bounded queues, 15 s heartbeat. | H6 | |
| A8 | Security: `serve_spa` containment check, CORS limited to `localhost:4200` or removed, no key in `GET /settings`, bind `127.0.0.1`. | H7 | Also flagged by the clicker review §6. |
| A9 | Move `httpx` from the dev group to runtime dependencies in `pyproject.toml`. | clicker §6 | `uv sync --no-dev` would break the API. |

### Phase B - Frontend quick wins - **DONE (§1.4)**

See design plan §7 Phase 0 for files and line numbers. Regression specs verified in Vitest (`npm test`).

| # | Work | Source |
| --- | --- | --- |
| B1 | Light-first: remove `class="dark"`, the `.dark` block, the theme toggle and Google Fonts; remove `user-select:none` from body. | T1, Q1 |
| B2 | One status mapping (`core/ui/status.ts`) plus `hlmBadge`; all `VideoStatus` values selectable and labelled. Fixes most contrast failures. | Q2, Q3, F3 |
| B3 | Extract = save (normalise to 100%), then extract; dirty flag and "Unsaved changes" pill. **Requires A3.** | F2, Q7 |
| B4 | Re-chunk guard (alert-dialog once status ≥ curating), wired to A3's confirm flag. | Q8, F6 |
| B5 | Hotkeys: no `preventDefault` on interactive targets; register every key in the spec table; lightbox ←/→ and K/X/S with auto-advance. | F1, Q5 |
| B6 | Disabled-with-reason helper text; replace the 3 `alert()` calls; confirm bulk reject/deselect. | Q6, F5, F6 |
| B7 | Toast actions typed per event (only extraction-complete gets "Start curating"); errors persist. | Q4 |
| B8 | One primary button per view; tokens for media surfaces; remove raw palette and hex values. | Q9, Q10 |
| B9 | Type-scale pass (82 `text-[Npx]`, `font-mono` → `tabular-nums`, bare `rounded`); emoji → lucide. | Q11, Q14 |
| B10 | Settings dialog: re-read on open (stale-form bug), validation, `snap_window_s`. | Q12, F8, F4 |
| B11 | Curate loading skeleton and distinct empty states. | Q13 |
| B12 | Add an Angular `test` target and specs for `time.utils`, chunk budget/snap, `CurateService` filters and `HotkeyService`. | GAP §5 |

### Phase C - Pipeline correctness and resources - **DONE (§1.4)**

All items verified in `tests/test_phase_c.py` (9 tests pass).

| # | Work | Source |
| --- | --- | --- |
| C1 | Re-decode the native frame when a runner is promoted or swapped in; record `native=1`. | H1 |
| C2 | Rank runners; similarity-based replacement on swap. | H2 |
| C3 | Per-video job lock (409); separate decode (1-2 workers) and caption executors. | H4 |
| C4 | Stream extract-750: small in-RAM copies, write native picks immediately. Target peak RSS below 2.5 GB for 1 h of 1080p. | H5 |
| C5 | Hard gates and pHash dedup in the 750 flow; apply letterbox/watermark crop before metrics. | H8, H9 |
| C6 | Wire up or remove unused settings (quotas, MMR knobs, concurrency, max_dim). | H9 |
| C7 | Budget shortfall redistribution, caption-job robustness (`return_exceptions`, 5xx retry), status validation, cache cleanup, response-shape unification (B2/B3: one chunk mapper). | M5, M7, M8, M9, B2, B3 |
| C8 | Run `scripts/bench.py` in WSL and record a baseline. The timings in the review are estimates. | §3 |

### Phase D - Spartan primitives and tokens - **DONE (§1.4)**

Design plan P1-P10:
- `components.json` with Vega style and `@spartan-ng/helm` path aliases
- Re-chunk `alert-dialog`
- Signal Forms / direct signal bindings in place of `ngModel` (zero `ngModel` and zero `FormsModule` across frontend)
- Tooltips + `kbd`
- Distribution bar on `hlm-progress` with computed category health warnings
- Candidate card redesign (roving focus, Enter/Space activation, state badges)
- Keyboard-accessible timeline and cards
- Model list loaded dynamically from `GET /api/openrouter/models` (F7)
- Landmarks and skip link (`a[href="#main-content"]`)

### Phase E - Workflow IA - **DONE (§1.4)**

Design plan W1-W10 (state machine in its §3.1, wireframes in §3.3):
- `WorkflowService` state machine deriving step progression and computing primary `nextAction`
- Per-video workspace with stepper (`Chunk` ➔ `Extract` ➔ `Curate` ➔ `Caption` ➔ `Done`) and sticky next action bar (`WorkflowBarComponent`)
- Pipeline board as the landing page (`PipelineBoardComponent`) with stage filters, search, and next actions; backend `kept_count`/`captioned_count` on `GET /api/videos`
- Jobs ticker with live SSE connection state indicator (F11)
- Lightbox modal upgraded with full ARIA dialog attributes, accessible close/arrow buttons, 1-based chunk numbering, and tabular numerals

### Phase 1.5 - Database Reset & Production Dockerization - **DONE**

Full production dockerization and safe database reset completed per user instruction:
1. **Database Reset**:
   - Backed up active SQLite database to `~/.cache/captioning_pipeline/state.db.bak_20261015_225653`.
   - Created `scripts/reset_db.sh` for convenient, safe database resetting with timestamped backup and optional `--purge-all` flag for cached candidates, previews, and filmstrips.
   - Initialized clean schema via `DB(settings.db_path)`.
2. **Multi-Stage Dockerfile**:
   - **Stage 1 (Frontend Builder)**: `node:22-bookworm-slim` builds Angular production bundle into `src/web/dist` (5.3s clean build).
   - **Stage 2 (Runtime)**: `python:3.12-slim-bookworm` with system dependencies (`ffmpeg`, `libgl1`, `libglib2.0-0`, `curl`, `ca-certificates`, `sqlite3`), `uv` package manager, and Uvicorn runtime.
   - Pre-downloads and embeds ONNX CLIP models (`Xenova/clip-vit-base-patch32` vision model, text model, and tokenizer) directly into `/app/models` inside the image via `scripts/download_models.py`.
   - `ClipClassifier` updated with dual-directory resolution: checks mounted cache/custom `CP_MODELS_DIR` first, and falls back to built-in `/app/models` without requiring runtime network requests.
3. **Docker Compose & Configuration**:
   - `docker-compose.yml` mounts Windows video folder `${VIDEO_DIR:-/mnt/c/Users/Patri/Videos/_girls}` to `/videos:ro` (and `/mnt/c/Users/Patri/Videos/_girls:ro`), persistent cache (`${CACHE_DIR}`), finished dataset destination (`${FINISHED_DIR}`), and optional Gemini skills directory.
   - `.env.example` and `.env` provide configuration for `OPENROUTER_API_KEY`, `VIDEO_DIR`, `CACHE_DIR`, `FINISHED_DIR`, and `PORT`.
   - `.dockerignore` filters out `.git`, `node_modules`, `.venv`, `.angular`, `__pycache__`, data, and database files.
4. **Verification**:
   - Container built cleanly (`captioning-pipeline:latest`).
   - Container running healthy: `0.0.0.0:8000->8000/tcp`.
   - All 89 source videos scanned and metadata indexed.
   - Angular SPA root (`/`) and JS bundles (`/main-*.js`) served cleanly (HTTP 200).
   - ONNX model inference verified inside container (`pixel_values` and `input_ids` inputs resolved).
   - Full test suites passing: 81 / 81 Python tests in `tests/`, 42 / 42 Angular Vitest specs.

### Phase 1.6 - Face Recognition, Character Tagging & Auto-Extraction - **DONE**

High-performance, CPU-friendly facial recognition (<10ms inference per frame on ARM64 WSL) and autonomous frame extraction pipeline integrated to prioritize target characters:
1. **Face Recognition Engine (`src/pipeline/face.py`)**:
   - Uses OpenCV YuNet (`face_detection_yunet_2023mar.onnx`) for face detection and landmarks, and SFace (`face_recognition_sface_2021dec.onnx`) for 128-dimensional L2-normalized identity embeddings.
   - Zero heavyweight external dependencies (no InsightFace, dlib, or CUDA required). Runs natively inside WSL 2 CPU in ~6.09 ms/frame.
   - Models auto-downloaded by `scripts/download_models.py` to `~/.cache/captioning_pipeline/models/` and `/app/models/` in Docker.
   - Robust cosine similarity matching against standard SFace threshold (0.363 match cutoff).
   - Multi-exemplar running centroid averaging: when additional face crops are tagged for a person, new unit-normalized embeddings are merged into a running identity centroid, improving recognition across varying angles and lighting.
   - Face crop extraction and disk caching for visual avatar confirmation in the UI.

2. **Database & Schema Enhancements (`src/pipeline/db.py`)**:
   - `persons` table: `id`, `name`, `slug`, `embedding` (BLOB), `exemplar_count`, `crop_path`, `created_at`, `updated_at`.
   - `videos` table: added `target_person_id` and `target_person_name` foreign key tracking.
   - `frames` table / responses: enriched with `target_person_sim`, `target_person_matched`, `face_count`, and bounding boxes.
   - Safe SQLite migrations and helper methods: `create_or_update_person`, `list_persons`, `get_person`, `delete_person`, `set_video_target_person`.

3. **Prioritized Scoring & Diversity (`src/pipeline/scoring.py` & `select.py`)**:
   - Scoring engine heavily boosts frames containing the target character (+0.40 confidence-scaled boost + 0.20 solo presence bonus) and penalizes off-target/different-face frames (-0.50).
   - MMR diversity reduction algorithm prioritizes target person candidates while ensuring camera angle and pose diversity across scene chunks.
   - Distribution metrics calculate `target_person_pct` alongside solo, multi-person, and equipment percentages.

4. **Instant In-Memory/Database Reconciliation**:
   - Real-time frame tagging endpoint (`POST /api/videos/{video_id}/tag-face-from-frame`): when a face is tagged on any frame, the person's embedding is updated, and all existing candidate frames for the video are instantly re-scored and updated in SQLite without needing a full re-extraction.
   - Broadcasts updated match scores and frame states via SSE events (`job_progress` / `reconciled`).

5. **Autonomous Extraction (`src/pipeline/jobs.py` & `api.py`)**:
   - Autonomous extraction job (`submit_auto_extract_job`) and endpoint (`POST /api/videos/{video_id}/auto-extract`): executes autochunking (if needed), chunk-based candidate sampling, face detection, identity matching, score prioritization, and MMR reduction in one seamless automated pipeline.

6. **Angular 22 Frontend Integration**:
   - `PersonService` (`src/web/app/core/services/person.service.ts`): Signal-based state management for persons, active target person selection, face tagging from frames, and auto-extraction triggers.
   - `PersonModalComponent` (`src/web/app/features/modals/person-modal.component.ts`): Character management modal with person creation, exemplar count indicators, target person assignment, and one-click auto-extract button.
   - `WorkflowBarComponent`: Displays the active target person badge (`🎯 Elena`) with click-to-manage action.
   - `CurateToolbarComponent`: Includes a quick `🎯 Target Person` filter toggle that instantly isolates matching frames in the candidate grid.
   - `CandidateGridComponent`: Renders high-visibility match percentage badges (`🎯 88%`) on candidate cards, and card-action quick-tag icon button (`lucideTarget`).
   - `DistributionBarComponent`: Displays a dedicated Target Person stat card with presence percentage and visual progress indicator.
   - `LightboxModalComponent`: Displays face detection count, target similarity score, and a direct "Tag Face as Person" button to tag characters directly from full-screen inspection.

7. **Verification**:
   - 135 / 135 Python tests passing across all 14 test suites (`bash scripts/test.sh`).
   - 49 / 49 Angular Vitest specs passing (`npm test -- --watch=false`).
   - Angular production build succeeds cleanly in ~5s (`npm run build`).

### Phase 1.7 - Multi-Person Roles, 95% Dedup Budget (150-200), and Deterministic Video Hash - **DONE**

1. **Multi-Person Face Recognition with Positive and Negative Roles (`src/pipeline/face.py`, `db.py`, `jobs.py`)**:
   - `video_persons` table: maps videos to multiple persons with role `'positive'` (prioritize) or `'negative'` (exclude/reject).
   - Exclusion logic (`FaceEngine.match_frame_multi`):
     - `excluded = bool(neg_detected and not pos_detected)`: frames showing exclusively the negative person are excluded/rejected.
     - Frames showing the negative person *together with* a positive person are NOT excluded (`excluded = False`) and receive positive scoring boost.
   - Batch face re-evaluation on disk (`POST /api/videos/{video_id}/re-evaluate-faces`): evaluates YuNet/SFace recognition on candidate preview/native frames on disk cache (<1s for 750 frames) without re-decoding raw video streams.
   - Demotes excluded frames to `runner_up` with `state='reject'`.

2. **Target Image Budget Reduction (150-200) & Score-Optimal 95% Dedup (`src/pipeline/refine.py`, `jobs.py`)**:
   - `deduplicate_and_trim_similarity`: analyzes cosine similarity matrix across candidate CLIP embeddings.
   - For image pairs with $\ge 95\%$ visual similarity (`>= 0.950`), retains the image with the superior composite/quality score and demotes near-duplicates to `runner_up` with `state='reject'`.
   - Trims and selects active `pick` count into the target budget range (150 - 200 images, configurable via `user_settings.target_curated`).
   - Available via REST endpoint `POST /api/videos/{video_id}/dedup-similarity` and one-click UI button.

3. **Deterministic Video Title Hash in Captions (`src/pipeline/indexer.py`, `openrouter.py`, `jobs.py`)**:
   - Generates deterministic 8-character hex hash (e.g., `v_3f8a1c02`) from the sanitized video title.
   - Persisted in `videos.title_hash` with automated SQLite migration and backfill.
   - System-side caption injection: `OpenRouterCaptioner.sanitize_and_validate_caption` guarantees the trigger token at Token 0 and the video title hash at Token 1 across all 3 captioning layers (L1 Gemma, L2 DeepSeek, L3 Grok) without duplication.

4. **Angular 22 Frontend Integration**:
   - `PersonModalComponent`: Added `🎯 + Positiv` and `🚫 - Negativ` toggles per person card, active tag badges in the video header, and `⚡ Re-evaluate Extracted Images` button.
   - `CurateToolbarComponent`: Added `⚡ Dedup 95% (150-200)` action button with live count notifications.
   - `WorkflowBarComponent`: Displays deterministic video title hash badge (`🏷️ v_3f8a1c02`).

5. **WSL 2 Verification**:
   - `bash scripts/test.sh`: **142 / 142 passed** (all 15 test suites passing, including `test_face_and_person.py`, `test_refine_and_dedup.py`, and `test_autopilot_e2e.py`).
   - `npm test -- --watch=false`: **49 / 49 passed** (all 6 Vitest suites).
   - `npm run build`: **Clean production bundle** (output: `src/web/dist`, 688 kB, 0 errors).

### Phase 1.8 - Overnight Batch Auto-Extraction & Targeted Single-Chunk Budget Extraction - **DONE**

1. **Batch Auto-Extraction Pipeline for All Videos (Overnight Run) (`src/pipeline/jobs.py`, `api.py`)**:
   - `submit_batch_auto_extract_job(video_ids=None, only_unextracted=True, options=None)` and `_run_batch_auto_extract_job`.
   - Sequential, unattended overnight execution across library videos (safe CPU/RAM resource usage).
   - Automatically auto-chunks unchunked videos using scene cuts into balanced ~300 frame budgets.
   - Runs full candidate sampling, Tier-1 metrics, multi-person facial recognition (positive/negative rules), CLIP scoring, and 95% score-optimal deduplication down to 150-200 images.
   - Resilient failure isolation: individual video exceptions are recorded, transitional statuses settled cleanly, and subsequent videos in the batch proceed uninterrupted.
   - REST endpoint: `POST /api/videos/batch-auto-extract`.

2. **Targeted Multi-Step Single-Chunk Frame Budget Re-Extraction (`src/pipeline/jobs.py`, `api.py`)**:
   - `submit_extract_chunk_job(chunk_id, options=None)` and `_execute_extract_chunk(chunk_id, k, budget_pct, ...)`.
   - Allows fine-tuning frame budgets (`k`, `budget_pct`) per scene chunk and re-extracting frames **strictly** within that chunk's time interval `[start_s, end_s]`.
   - Guaranteed isolation: only files and SQLite records for `chunk_id` are replaced; all other scene chunks, curations, and captions remain completely untouched.
   - Resets chunk `dirty=0` upon successful completion.
   - REST endpoints: `POST /api/chunks/{chunk_id}/extract` and `POST /api/videos/{video_id}/chunks/{chunk_id}/extract`.

3. **Angular 22 Frontend Integration**:
   - `PipelineBoardComponent`: Added `⚡ Auto-Extract All (Overnight)` button in the header toolbar, active batch progress banner with live percentage and step description, and individual `⚡ Auto` action buttons for new/chunked videos.
   - `ChunksPanelComponent`: Added `⚡ Extract Scene` action button in Column 8 with spinning indicator (`chunkService.isExtractingChunk(chunk.id)`), auto-saving budget edits before dispatch.
   - Data layer: `ChunkService.extractChunk`, `VideoService.batchAutoExtract`, and `VideoService.autoExtractSingle`.

4. **WSL 2 Verification**:
   - `tests/test_chunk_and_batch_extract.py`: Unit tests verifying targeted chunk isolation, batch execution, error resilience, and API contracts.
   - All tests passing across the test suite.
   - Clean production build: `src/web/dist` (0 errors).

### Phase 1.9 - Session 5: learn-as-you-tag image tagging, Phases F/G/H, GAP closure - **DONE (macOS-verified)**

**1. Learn-as-you-tag image tagging (new feature).** Like persons for faces, tags are global and learned from the user.
- **Teach:** Curate lightbox → press `T` (or "Mark region") → drag a box → name it (autocomplete; new tags get a
  category). The crop and its mirror are CLIP-embedded (the ONNX model already used for scoring) into `tag_regions`.
  The other windows of the same frame become hidden `context` counter-examples (`parent_id`, cascade). Without them a
  single example also matched its own background: real-CLIP test, empty frames went from 0.69 to no suggestion.
- **Suggest with location:** each frame is embedded as 19 multi-scale windows, cached per video
  (`<cache>/tag_windows/*.npz`, fp16, in-memory copy). Per-tag score = noisy-OR of the exemplar term (top-3 cosine vs
  a calibrated threshold) and a fading CLIP text prior, suppressed near negatives. The best window, refined by a
  score-weighted union, is the suggested box (dashed on the image, with the score).
- **Learn:** confirm = positive example at the box; reject = counter-example and frame `rejected`; un-tag drops the
  frame's examples. Each step recalibrates the tag (threshold, leave-one-out accuracy, shrunk to the default while few
  examples exist), instantly re-scores the video's cached frames (SSE `tags_updated` → grid chips update), and the
  first teach on a video queues a `tag_suggest` job for the unembedded frames. `auto_suggest_tags` also pre-tags
  every new extraction.
- **UI:** lightbox tag panel and box overlay (`features/tags/*`); grid chips (solid = confirmed, dashed = suggested);
  Curate toolbar tag filter (confirmed only / + suggested), "Tags" library and "Auto-tag"; the Tag library modal
  (examples, counter-examples, learned accuracy, rename, category, zero-shot description, merge, delete, per-video
  counts).
- **Downstream:** confirmed tags lead stub captions and the export sidecar (`image_tags`), and reach the VLM as
  "verified visible elements" hints.
- Files: `src/pipeline/image_tags.py`, `src/pipeline/tag_routes.py`, schema in `db.py`, hooks in `jobs.py`
  (`submit_tag_suggest_job`, `_maybe_auto_suggest_tags`, generic `submit_video_task`), `export.py`, `openrouter.py`
  (`tag_hint_text`); `core/{models/tag.model.ts,services/tag.service.ts}`, `features/tags/*`,
  `features/modals/tag-manager-modal.component.ts`, lightbox, grid, toolbar. Tests: `tests/test_image_tags.py` (15).
- Verified in headless Chrome against the real CLIP model on synthetic scenes: draw → name → suggestion with the
  right box on the next frame → confirm → background pass tags the remaining chair frames → reject teaches.

**2. Phase G - Captioning v2:** versioned prompts in `src/pipeline/prompts/` (no runtime `~/.gemini` read), temperature
0.4, a validation gate (word range, preambles/refusals, truncation, token-0 trigger, adult-only wording) that sends
failures down the L1→L2→L3 cascade, per-mode trigger profiles, a `captions` table as the source of truth
(`captions.py`; reconcile imports `.txt` idempotently; the exporter writes from it), per-frame token and cost logging
(`usage: {include: true}`), `GET /api/captions/stats` and `GET /api/frames/{id}/caption`. Tests: `test_captioning_v2.py` (25).

**3. Phase H - Prompt Director v2:**
- **Retrieval:** R7 (query-embedding LRU and warm-up), R2 (word-boundary tag matching, FTS5 BM25 fused with dense
  and tag scores via RRF).
- **R9:** the Qwen tokenizer is used only when it is available offline.
- **UX:** history and favourites (`rag_history` in `state.db`), Generate N (1-10), `POST /api/rag/lint` with inline
  edit and re-lint, `.txt`/`.jsonl` export, and 1/2/3 copy keys.
- **Captions table:** the RAG index now reads the `captions` table first (`vlm` and `imported` rows only; never stubs
  or rejected frames). The curated and merged file scan is the fallback.
- **R6:** incremental reindex runs after every caption job (`rag_auto_reindex`; `POST /api/rag/index
  {incremental}`; `index_rag.py --incremental`).
- **R10:** a deterministic 10% val split, plus `GET /api/rag/eval` and `scripts/eval_rag.py`
  (sibling recall@k / MRR, tag recall and precision, with chance baselines).

**4. Phase F - scoped, CPU-only:**
- **Extraction:** dHash pre-dedup of consecutive samples (`dhash_dedup_max_distance`) and window-peak sampling
  (sharpest and stillest sample per interval).
- **Learning from curation:** a `curation_events` log of keep/reject/star/swap/bulk actions, and logistic-regression
  learned quality weights (`learned_weights.py`, `GET/POST /api/scoring/learned-weights[/fit]`, opt-in
  `use_learned_weights`).
- **Selection:** `POST /api/videos/{id}/reselect` runs quality-weighted facility location with per-chunk floor/cap.
  It never demotes starred or exported frames. Tests: `test_phase_f.py` (15).

**5. GAP_REPORT closure:**
- **Frontend:** F1 (full key table, J/L, Alt/Ctrl arrows, shortcuts suppressed while a modal is open via
  `UiStateService`), F4, F5/F6 (shared `ConfirmService` + `hlm-alert-dialog`), F7 (server-only key), F8 (settings
  validation), F9 (hash deep links), F10, F12 (rAF playhead), F13 (Export this video), B3 (`chunk-fields.utils.ts`).
- **Backend:** B4 (`PUT /api/settings` validated by `config.validate_user_settings`: 400 with `errors`, unknown keys
  reported in `ignored_keys`) and B5 (`PATCH /api/picks` with `rebalance: true` runs the swap logic).
- **Styling:** no emoji icons, raw palette, hex, `text-[Npx]` or `font-mono` left; filename as h2.
- **Tooling:**
  - The server resolves the build per request, never serves raw `src/web`, and answers 503 "Frontend not built"
    instead (`CP_WEB_DIST` overrides the path).
  - `build_web.sh` builds production (`--dev` optional).
  - `setup.sh` installs Node 22 via nvm into `$HOME` when needed, and `env.sh` loads it.
  - `sync_skills.sh` mirrors the skills.

**6. Frontend platform:**
- **Change detection:** zoneless (`provideZonelessChangeDetection`, zone.js removed).
- **HTTP:** `ApiService` on `HttpClient` with `apiErrorInterceptor`, keeping `ApiError` semantics.
- **Dialogs:** `hlm-dialog` for the settings, caption, person and tag-library dialogs; Signal Forms for settings.
- **Fixes found while verifying:**
  - "Seek" from Curate now reaches the Studio player. The request waits for the player, and a seek that arrives
    before the metadata is queued.
  - `<select>`s with `@for` options showed their first option until reselected (the studio status showed "New"
    for a curating video). Options now bind `[selected]`.

**Verification (macOS):**
- `t.sh -q` (pytest with temp `CP_*` dirs): **226 passed, 4 skipped**.
- `npx ng test --watch=false`: **132 passed (17 files)**.
- `npm run build` (production): clean.
- Headless Chrome against the real CLIP model, no console errors:
  - tagging and auto-suggest
  - deep-link reload and back/forward
  - confirm dialog and Escape
  - settings dialog open/close
  - seek to chunk 2 (an H.264 test video: Chrome cannot decode MPEG-4 Part 2)
  - studio status select

**Session 5 continuation (after the stop-hook review), all macOS-verified:**
- **Decision 1:** per-video LoRA profile `concept|character`.
  - Character mode adds the identity gate, solo portrait crops and character caption mode.
  - Code: `pipeline/profiles.py`, `PATCH /api/videos/{id} {profile}`, `POST /api/videos/{id}/apply-profile`, the
    workflow-bar toggle. Tests: `test_profiles.py`.
- **Decision 3:** derived status badge plus an overflow menu (Skip / Restore / Advanced set-status with a confirm),
  in `video-status-menu.component.ts`.
- **Decision 4:** `--success-strong` (#15803D, 5.0:1) and `--warning-strong` (#8A5A00, 5.9:1), used for green and
  yellow text, icons and borders. Documented in `design-system/references/colors.md`.
- **GAP leftovers:**
  - Toasts use Spartan sonner (`hlm-toaster`); the `JobEventService` API is unchanged.
  - No raw `<select>` is left: all are `hlm-native-select`, and the primitive re-applies its value when options
    arrive late.
- **Phase H:**
  - **R1:** per-domain CLIP embeddings (40-word chunks per taxonomy domain, `dense_mode: domain` by default). On a
    synthetic eval with long-caption queries, sibling recall@4 went from 0.56 to 0.98 for dense-only scoring and
    from 0.36 to 0.52 with fusion.
  - **R3:** `GET /api/rag/similar?frame_id=`, a SigLIP image index with CLIP fallback.
  - UI: "Use as prompt seed" and "More like this frame" from the lightbox and Dataset frames, and the K4 Prompt
    Director design pass.
- **Phase F UI:** a Reselect dialog in the Curate toolbar (all knobs, including the axis caps and cross-dataset dedup),
  plus learned-weights status, Fit now and an on/off toggle. A "Pipeline and learning" section in Settings covers
  the tag threshold, dHash distance, cut detector, selection embedder, IQA model, quality v2, window-peak sampling,
  the analysis cache, auto-tagging and RAG auto-reindex.
- **Phase F2 (backend):**
  - **Analysis cache:** one analysis decode per video, cached in `<cache>/analysis/<video>.npz` plus a
    `frame_features` table. A second extraction of a chunk decodes nothing.
  - **Cuts:** a frame-accurate content cut detector (`cuts.py`, PySceneDetect-style adaptive and fade handling, no
    new dependency), used by the filmstrip, autochunk and autopilot.
  - **Quality v2:** `quality.py` applies hard gates, then chunk and video percentile-normalised subject sharpness,
    noise, exposure, face size/pose and motion. IQA is optional (`clip_iqa`, or `topiq_nr` via ONNX). MUSIQ: no
    usable single-file ONNX export found, so TOPIQ is offered instead.
  - **Selection:** optional DINOv2-S embeddings (`embedders.py`, `selection_embedder: dinov2`), and reselect axis caps
    and cross-dataset dedup. Tests: `test_phase_f2.py`.
- **Decoder fix:** `decode.safe_decode_frames` skipped PyAV's flush packet and lost the last buffered frames of every
  video. It now drains the decoder (regression test in `test_phase_f2.py`).
- **Phase I:**
  - `scripts/ng_env.sh` keeps `.angular` on ext4 for `/mnt/*` checkouts.
  - `patch_os.js` now only falls back when `os.networkInterfaces()` throws, and documents the cause.

**Final verification (macOS):**
- `t.sh -q`: **261 passed, 4 skipped**.
- `npx ng test --watch=false`: **192 passed (30 files)**.
- `npm run build` (production): clean.
- Headless Chrome, 16 steps, no console errors:
  - mark region → suggestion box → confirm; ball frame not suggested; 4 tagged cards after the background pass
  - Tag library dialog; Reselect submits with a toast
  - deep link and back/forward; reject-all confirm with Escape
  - profile toggle to `character` persisted; seek to chunk 2
  - status badge and menu; Settings pipeline section; Prompt Director

**Remaining before relying on it (needs your machine):**
- Run §5 in WSL.
- Tune on real footage: tag thresholds, cut detector sensitivity (`cut_*` settings), quality-v2 gates, and
  per-domain RAG retrieval (`/api/rag/eval`). All of these were tuned only on synthetic data.
- ~~First model downloads~~ **Verified on macOS (session 5):** each model downloaded on first use and behaved
  correctly on synthetic images.
  - DINOv2-S (24 MB, 384-d): a mirrored copy scored 0.976 vs 0.610 for an unrelated image.
  - SigLIP (99 MB, 768-d): 0.724 vs 0.707; a narrow margin on synthetic images.
  - TOPIQ-NR (177 MB): a sharp image scored 0.59, its blurred copy 0.20.
  - CLIP-IQA: 0.50 sharp vs 0.39 blurred.
  - On WSL they download again into `~/.cache/captioning_pipeline/models`. The Docker image does not include them
    yet (only CLIP and the face models).
- **Cannot be done from the macOS session:** the WSL run (§5) and tuning on the real library. This machine has no
  WSL, Docker or video library.
- Known limits:
  - Cross-dissolves without a single-frame spike are not detected as cuts.
  - Learned-weights accuracy is measured on the training data.
  - An auto-dismissed toast skips its exit animation.

### Phase F - Extraction / quality / selection v2 - **DONE (session 5, §1.9; MUSIQ replaced by optional TOPIQ)**

Pipeline review §4-6 and roadmap Phases 2-4:
- **Extraction v2:**
  - a single analysis decode, cached as an npz plus a `frame_features` table
  - frame-accurate cuts (TransNetV2 or PySceneDetect)
  - window-peak sampling (sharpest and stillest per 1-2 s within a shot)
  - dHash pre-dedup
  - YuNet face detection and SFace identity gate (OpenCV, no new dependency)
- **Quality v2:**
  - gates, then per-video percentile-normalised components (subject sharpness, noise, exposure, face size and pose)
  - weights fitted from logged keep/reject history (`curation_events`)
  - optional TOPIQ/MUSIQ
- **Selection v2:**
  - DINOv2-S ONNX embeddings on letterboxed frames
  - quality-weighted facility location with per-chunk floor/cap, an elasticity knob, axis caps and cross-dataset dedup
  - `POST /videos/{id}/reselect`, with the knobs exposed in the Curate toolbar
  - pseudocode in §6.3

### Phase G - Captioning v2 - **DONE (session 5, §1.9)**

Pipeline review §7:
- a `captions` table as the source of truth (the exporter and RAG read from it)
- prompts versioned in `src/pipeline/prompts/`, no runtime read of `~/.gemini`
- temperature 0.3-0.5
- validation gate
- per-profile trigger handling
- one shared `render_export_image()`
- cost logging

### Phase H - Prompt Director v2 - **DONE (sessions 4-5, §1.9)**

Clicker review §4-5.

**Quick:**
- R7: warm the model and cache query embeddings
- R2: word-boundary matching plus FTS5 BM25 with RRF
- R8: **DONE** - prompt adult guardrails (21+) aligned; Lene character profile avatar updated
- R4: **DONE** - cap exemplars per source video (`max_per_source` parameter and MMR filtering in `RagRetriever`)
- R9: Qwen tokenizer for token counts

**Then:**
- R1: per-domain embeddings, or a 512-token text embedder
- R6: incremental reindex after export or caption jobs
- R10: eval set from the val split
- R3: SigLIP image embeddings, "more like this frame"

**UX:**
- history and favourites in SQLite (`rag_history`), the biggest workflow win
- batch "Generate N"
- export prompt lists (`.txt`/`.jsonl`)
- "Use as prompt seed" from Curate/Dataset
- inline edit plus re-lint
- `1/2/3` copy shortcuts via `HotkeyService`
- Prompt Director design pass (K4)

### Phase I - Cleanup and docs (continuous)

- **DONE**: Deleted `src/web/app.js` and `src/web/shared/components/**`.
- **DONE**: Synchronized `angular-migration/SKILL.md` and `api-contract/SKILL.md` across `.agents/skills` and `.claude/skills`.
- **DONE (session 5)**: Node 22 via nvm in `setup.sh`/`env.sh`, production build in `build_web.sh` and `start_all.sh`, the
  server never serves raw sources (503 "Frontend not built"), `.claude/skills` canonical + `scripts/sync_skills.sh`
  mirror, skills updated for every route added in session 5 (`api-contract`, `pipeline-overview`, `studio-ui-spec`,
  `angular-migration`), 13 ad-hoc scripts deleted.
- **DONE (session 5)**: `scripts/ng_env.sh` (sourced by `build_web.sh`, `start_web.sh`, `start_all.sh`) links `.angular` to
  `<cache>/angular` on ext4 when the checkout is on `/mnt/*`. `patch_os.js` now only falls back when
  `os.networkInterfaces()` throws. The cause is a libuv `uv_interface_addresses` failure on some WSL 2 network setups,
  and the file shows how to check it. Not reproduced here: no WSL on this machine.

**Dependencies:**
- A1 → A2; A2 → E (Dataset)
- A3 → B3/B4
- B2 → D (cards) / E (board)
- D (P1) → D forms / timeline / K1
- decisions 2, 3, 5 → E
- decision 1 → F/G
- A4 before re-captioning anything

## 5. Verify the current state in WSL (do this first)

```bash
# from Windows: wsl bash -lc "<command>"; inside WSL:
cd /mnt/c/Users/Patri/Documents/captioning_pipeline
bash scripts/setup.sh
npm install
bash scripts/test.sh tests/test_rag.py tests/test_rag_api.py tests/test_phase_a.py tests/test_openrouter.py
bash scripts/test.sh                # full suite; test_phase1::test_api_routes needs the real library
bash scripts/start_all.sh           # builds the UI (production), serves :8000
npx ng test --watch=false           # Vitest specs
curl -s localhost:8000/api/rag/status | python3 -m json.tool
uv run python scripts/audit_captions.py --list   # read-only: truncated captions and stubs from earlier runs
```

0. **Phase A checks:**
   - `localhost:8000` must still open from the Windows browser now that uvicorn binds `127.0.0.1`. WSL 2 localhost
     forwarding should cover it. If not, set `localhostForwarding=true` in `.wslconfig`; do not go back to `0.0.0.0`.
   - `uv sync` should accept the hand-edited `uv.lock` (or re-lock it); commit any lock change.
   - Run the audit before re-captioning. A caption job on an affected video re-captions only the truncated or stub
     files and reuses the rest.

1. **Prompt Director:** open `http://localhost:8000` → Prompt Director.
   - You should see the guided panel with **Build index**. The first build downloads the CLIP text model
     into `~/.cache/captioning_pipeline/models`.
   - Then pick a preset → Preview exemplars → Generate (Ctrl/Cmd+Enter).
   - With no key you get a readable 400. Set `OPENROUTER_API_KEY` in `.env`, or use Local Ollama with a URL.
2. **Golden path:** select a video → suggest → save → extract → curate → caption → export.
   - A1-A5 have landed but have not run against real data. Use a copied video for the first pass.
3. **Image tagging (session 5):** in Curate open a frame → `T` → drag a box → name it → Enter.
   - The next similar frames should show a dashed suggestion box with a score, and grid chips appear after the
     background `tag_suggest` job (jobs ticker).
   - Confirm/reject a few, then open **Tags**: examples, counter-examples and the learned accuracy should update.
   - Real-footage thresholds are untested: if suggestions are too eager or too shy, tune
     `tag_suggest_min_score` (Settings) before changing constants in `image_tags.py` (`DEFAULT_THRESHOLD`,
     `TEXT_CENTER`, `CONTEXT_NEGATIVES`).
4. **Captioning v2 / Phase F:** caption a copied video and check `GET /api/captions/stats` (cost, prompt version);
   try `POST /api/videos/{id}/reselect` and the dHash/window-peak stats in the extract `job_finished` event.

## 6. Unverified - confirm before relying on it

- **Not run in WSL:** nothing from sessions 2, 3 and 5 (session 5 ran on macOS with temp `CP_*` dirs and headless Chrome).
- **Session 5 on real footage:** tag thresholds were tuned on the real CLIP model but with synthetic scenes only;
  the learned-weights accuracy is measured on training data; window-peak sampling timing on 1080p is unmeasured.
- **Phase A assumptions:** WSL 2 forwards `127.0.0.1` binds to Windows `localhost`. The OpenRouter `finish_reason`
  values match the mocked ones. The hand-edited `uv.lock` is accepted.
- **Runtime behaviour, inferred from source:**
  - Space-key `preventDefault` blocking button presses
  - the settings stale-form bug
  - `py-0.2` producing no CSS
  - the lightbox showing the DB `chunk_id`
- **Estimates:**
  - pipeline timings and memory figures
  - CLIP cosine ranges
  - path-traversal exploitability
  - asyncio cross-thread behaviour
  - wheel availability on aarch64 (TransNetV2 port, MediaPipe, PyAV hwaccel)
- **Machine profile:** `scripts/bench.py` says "WSL aarch64, CPU only", so the plan assumes CPU/ARM64.
  The GPU items in pipeline Phase 6 are optional.
