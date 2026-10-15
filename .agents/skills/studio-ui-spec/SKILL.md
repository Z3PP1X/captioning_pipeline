---
name: studio-ui-spec
description: Behavioural specification of the Captioning Pipeline Studio UI (originally legacy src/web/app.js, now re-implemented in Angular under src/web/app) - views, state, interactions, keyboard shortcuts, chunk-budget and curation rules, plus a verified parity/defect status of the Angular port and the new Prompt Director view. Use when changing or extending any frontend feature, checking parity against the legacy behaviour, or deciding what to keep, fix or drop.
---

# Studio UI behaviour spec (legacy parity reference)

The legacy UI was one `app.js` with a global `state`, `document.getElementById` wiring and `innerHTML` templates. It has been re-implemented in Angular (`src/web/app`, see `angular-migration`); `src/web/app.js` is now dead code (still in the repo, no longer loaded) kept only as the behavioural reference. The sections below describe the **legacy behaviour**; "Port status" at the bottom records what the Angular app actually does (verified in code). Visuals follow `design-system`, not the legacy CSS.

Additions since the original spec (present in both `app.js` and Angular): per-chunk **caption model** (`caption_model`, `PATCH /api/chunks/{id}`, bulk "set all chunks" select, `use_chunk_models: true` in the caption request), **Sync captions** (`POST /videos/sync-captions`, queue + chunk panel buttons), per-chunk captioned counts/badges from `/chunks` (`captioned_count`, `picks_count`, `is_fully_captioned`), a light/dark theme toggle in the header.

## Layout

Header (logo, tabs, running-job pills, "Next Video", Settings) - left **Video Queue** sidebar - main area with three views switched by tab: **Studio**, **Curate**, **Dataset**. Overlays: Lightbox, Settings dialog, Caption (OpenRouter) dialog, toast stack.

## Global state (-> signal stores)

`videos[]`, `currentVideo`, `filmstrip {thumbnails, cuts}`, `chunks[]`, `candidates[]` (picks + runners), `curation {picks, runners, distribution}`, `showRunnerUps`, `dataset`, `settings`, `inPoint/outPoint`, `snapCuts`, `currentTab`, `activeJobs: Map<job_id, {video_id, video_name, progress, step}>`, `lightboxIdx`, `curateChunkFilter`.

## Startup

`setupEventListeners` -> open SSE -> `GET /settings` -> `GET /videos` -> `GET /videos/next` -> select that video (else first in list).

## Video queue (sidebar)

- Search box (300 ms debounce) + status filter both re-query `GET /videos`; count badge "N videos"; rescan button -> `POST /videos/scan` then reload.
- Card: name, status badge, duration, WxH, chunk count, export count. States: active, broken (shows `broken_reason`), extracting (badge forced to EXTRACTING + inline progress bar + step text from the active job).
- Click selects video: sets titles/meta pills (duration, WxH@fps, codec, MB), enables the status select, clears In/Out, loads `<video src=/api/videos/{id}/stream>`, loads chunks then filmstrip, loads candidates if the Curate tab is active.
- Status select (per video) -> `PATCH /videos/{id}`.
- "Next Video" (hotkey N) -> `GET /videos/next`.

## Studio view

**Player controls**: play/pause (Space), timecode `HH:MM:SS.cc / total`, jump input (accepts `90`, `90s`, `MM:SS`, `HH:MM:SS(.ms)`, Enter or Go; invalid input flashes the border red for 1 s), +-10 s / +-60 s buttons, frame step back/forward (`1/fps`, pauses first), playback speed select (0.25-16x), Snap Cuts toggle, In/Out/Add Chunk buttons, "Watermark Rect" button.

**Timeline** (max ~960 px wide, 64 px tall): filmstrip thumbnails (flex row), scene-cut markers at `t/duration`, chunk bands (label `#n`, click seeks to start), playhead, In/Out range highlight, hover preview (thumbnail by index `floor(pos*count)` + timecode), click-to-seek. Playhead updates on `timeupdate`.

**In/Out + snapping**: marking In/Out takes `currentTime`; if Snap Cuts is on, snaps to the nearest cut within `settings.snap_window_s` (default 1.5 s). Out may be before In (range uses min/max).

**Add chunk** (Enter in Studio): needs In and Out, max 15 chunks, then inserts a chunk and re-normalises budgets (see rules). Chunk row fields: index, start/end editable time inputs + duration, `budget_pct`, `k`, `rule` select (`person|equipment|both|either|none`), tags input (comma list), delete. Header shows `n / 15 max` and a **budget badge** that turns invalid when the sum is not within +-0.5 of 100.

**Buttons**: Auto-Suggest (`POST /chunks/suggest`, replaces local chunks, alerts if empty) - Save Chunks (`PUT /chunks`) - **Extract & Score 750**: requires a video and >=1 chunk, rescales budgets proportionally to 100 if off (or evenly if all zero), saves chunks silently, then `POST /extract-750` and toasts with a "Curate Now" action.

**Budget rules**: even split `floor(100/n)` with the remainder distributed +1 to the first chunks; proportional rescale when the sum != 100; server normalises again to 1 decimal and fixes rounding on the largest chunk. Server forces `k >= 5`.

## Curate view

- **Distribution bar**: four meters (multiple people target 50% - warn outside 45-55; one person 25-30% - warn outside 20-35; tools/equipment 20-25% - warn outside 15-30; face >=50% - warn below 20) plus "Curated Set n / 300". Meter fill is `min(100, v)%`. Out-of-range = warning state.
- **Toolbar**: chunk filter (options built from chunks: `Chunk #n (start -> end) [tags] - picks / total`), role filter (all / pick / runner_up), state filter (all / keep / reject / star), "View Candidate Pool" toggle (shows runner-ups; default hides them unless the role filter is explicitly runner_up), Keep All (optimistic `state=keep` for non-keep/star, honours chunk filter), Deselect All (swaps every pick in the filter, sequentially), Caption button (needs a video and >=1 pick).
- **Grid** grouped by chunk (sorted by chunk position). Section header: `Chunk #n`, time range + duration, tags, picks count, actions Keep All / Deselect All / Seek (jumps Studio video to chunk start and switches tab).
- **Candidate card**: lazy `preview_url` image, badge `C#n - PICK #r` / `RUNNER #r`, timecode, metric pills (sharpness, quality, Multi|Solo, Tool/Eq, Face>=50%), actions: picks -> Keep, Swap, Star; runner-ups -> Promote; zoom (1:1). Card state classes: keep / star / reject (dimmed) / runner-up styling.
- **Swap** = `POST /swap-frame {deselected_frame_id}` then replace `picks/runners/distribution` from the response and toast. **Promote** = demote the lowest-ranked pick of the chunk (`role=runner_up,state=reject`) then promote the runner (`role=pick,state=keep`).
- **Lightbox**: full PNG, timecode, role/state badges, JSON dump of the 7 scores + zero-shot probs, buttons Keep (K), Reject (X; swaps if pick, else `state=reject`), Star (S); each closes the lightbox. Esc closes. (Legacy has no prev/next navigation although `lightboxIdx` exists - a worthwhile improvement.)
- **Caption dialog**: API key (password), model select, mode (`concept|character|style`), trigger word, class noun, fixed spec pills (3-layer cascade, high reasoning, 300 tokens, temp 0.80, <=512 px). Start -> `POST /caption-openrouter`, button disabled + "Queueing Batch..." while pending, toast on success, alert on failure.

## Dataset view

Refresh + "Export All Curated Videos" (`POST /export {}`; reports count + videos), two metric cards (total exported images, curated videos) + static "PNG 100% lossless sRGB (snap %16)" card, table: video name, frames exported, per-video folder `data/finished/per_video/{slug}/`, merged folder `data/finished/merged/`.

## Jobs and toasts

SSE job pills in the header: spinner, short name (18 chars), `step (pct%)`, progress bar, cancel (`DELETE /jobs/{id}`), click -> select that video + go to Curate. Toast: title + message, optional "Curate Now", close, auto-dismiss 9 s, error variant. Errors are mostly `alert()` in legacy - replace with toast/alert-dialog.

## Keyboard shortcuts (ignored while an INPUT/SELECT/TEXTAREA is focused)

| Key | Action |
| --- | --- |
| Space | play/pause |
| K | pause (Studio) / Keep (lightbox open) |
| X / S | Reject / Star (lightbox open only) |
| Esc | close lightbox |
| G | focus jump-to-time input |
| J / L | -2 s / +2 s |
| Left / Right | -+1 s; Shift 10 s; Ctrl 60 s; Alt 300 s |
| PageUp / PageDown | +60 s / -60 s |
| , / . | previous / next frame |
| [ or I / ] or O | mark In / mark Out |
| Enter | add chunk (Studio tab only) |
| C | toggle Snap Cuts |
| N | next video |

Implement as one `HotkeyService` with a declarative keymap and the same focus guard (also skip when a dialog input is focused or `contenteditable`).

## Defects and dead UI in the legacy code (status in the Angular port: see "Port status")

1. `setupEventListeners` threw at `document.getElementById('btn-export-this-video').onclick` (element absent), so everything registered after it never bound in legacy: export-all, refresh, lightbox close, status select, settings dialog, **all keyboard shortcuts**. (Angular: crash fixed, shortcuts still unbound.)
2. `#btn-cancel-job` referenced but absent; cancel only on header job pills.
3. Watermark button/canvas had no handlers (now implemented in Angular).
4. `innerHTML` templates interpolated filenames/tags/messages unescaped (XSS) - fixed by Angular templates; never use `[innerHTML]` with such values.
5. Backend/contract mismatches: see `api-contract` -> "Known mismatches" (some fixed upstream: `StreamingResponse` import).
6. Settings inputs filled once at startup, no validation (`NaN` can reach the server), `|| default` hides legitimate `0`; status `<select>` blank for statuses outside its 6 options.
7. Keep All fired un-awaited PATCHes; Deselect All swapped sequentially with a re-render each time (Angular uses server-side bulk endpoints).
8. `alert()`/`confirm()`-style UX for errors and confirmations.

## Port status in the Angular app (verified in code)

Legend: DONE = implemented as spec'd, CHANGED = implemented differently, MISSING = not implemented/wired, FIXED = legacy defect no longer present.

| Area | Status |
| --- | --- |
| Startup flow (SSE, settings, videos, next video) | DONE (`AppComponent.ngOnInit`). "Next" now picks the next non-broken, non-exported video after the current one (CHANGED: no `/videos/next` call). |
| Video queue: search + status filter, rescan, sync captions, badges, job progress, broken state | DONE; filtering is client-side over the loaded list (status groups `curating`/`curating_300`, `extracting`/`extracting_750` merged). Queue status badge handles `curating_300`/`captioning`. |
| Per-video status `<select>` | CHANGED: still only the 6 legacy options, so `extracting_750`, `curating_300`, `captioning`, `chunking`, `ignored` render blank. |
| Player: play/pause, timecode, jump input (`parseTimestamp`), +-10/60 s, frame step, speed, Snap toggle, In/Out/Add chunk | DONE as buttons. |
| **Keyboard shortcuts** | MISSING for all but N, Esc, K/X/S (lightbox). `HotkeyService` knows the keys but only 5 actions are registered; no J/L or Alt+Arrow. (Legacy never bound them either - defect #1 - so the app has had no working player shortcuts in either version.) |
| Snap to cuts | CHANGED: fixed 0.5 s window (`ChunkService.snapTimeToCut`) instead of `settings.snap_window_s` (1.5 s). |
| Timeline (filmstrip, cuts, chunk bands, playhead, In/Out, hover preview, seek) | DONE (`TimelineComponent`, template bound). Playhead still driven by `timeupdate`. |
| Chunk editing: add (max 15), delete, time/k/rule/tags, budget badge, Auto-Suggest, Save | DONE. Budget on add: remainder goes to chunk 0 (legacy: first N chunks +1). Max-15 uses `alert()`. |
| Extract & Score 750 | CHANGED: no longer rescales budgets and **no longer saves chunks first** - it posts `/extract-750` immediately, so unsaved edits are ignored by the job. |
| Watermark rect | FIXED/DONE: canvas drawing + `PATCH watermark_rect` (normalised 0-1 rect), drawn in `#D73816`. |
| SSE job pills, toasts, cancel, caption-vs-extract messages | DONE (`JobEventService`, header pills, `ToastContainerComponent`). No explicit reconnect handling beyond EventSource defaults. |
| Curate: distribution bar (backend key names), filters, grouped grid, Keep/Swap/Star/Promote, chunk Keep/Deselect/Seek, runner-up pool toggle, lightbox with prev/next | DONE. Keep All / Reject All are now server-side (`/curate/keep_all`, `/curate/reject_all`) - FIXED fire-and-forget PATCH loops. Lightbox prev/next added. Reject-all has no confirm. |
| Per-video "Export this video" | CHANGED: lives on the Dataset table as "Re-Export" per row (`exportVideo`); no button in the Curate toolbar. FIXED the missing-element crash of legacy defect #1. |
| Caption dialog | DONE; model list is hard-coded in the template (not from `/api/openrouter/models`); API key still cached in `localStorage` (`cp_openrouter_api_key`); uses `alert()` on failure. |
| Settings dialog | DONE; only HTML `min/max`, no validation. |
| Dataset view (metrics, table, export all) | DONE. |
| XSS via `innerHTML` | FIXED (no `innerHTML`/`bypassSecurity*` in `src/web/app`). |
| `alert()` UX | NOT fixed: 3 remaining (`chunk.service.ts:53`, `video-player.component.ts:373`, `openrouter-modal.component.ts:223`). |
| Tests | DONE (42 specs in vitest covering time.utils, chunk budgets/snapping, curate filters, hotkeys, and spartan components). |

## Prompt Director view (new, `features/prompt-clicker`, tab "Prompt Director")

RAG-powered prompt tool for Krea (characters Sara/Lene, taxonomy categories, curated presets): `PromptRagService` calls `GET /api/rag/{categories,presets,characters,status}`, `POST /api/rag/index`, `POST /api/rag/retrieve`, `POST /api/rag/generate`, `GET /api/rag/preview/{id}`; shows pollable build status banner ("Build index") and retrieved dataset exemplars as context. Backend in `src/pipeline/rag/` (can be built from UI or via `scripts/index_rag.py`). Independent of the selected video; queue sidebar is hidden on this tab.

## Parity checklist (ticked = confirmed in the Angular code)

- [x] Startup flow, video queue (search, filter, rescan, badges, progress, broken state)
- [ ] Player controls work, **but** all shortcuts except N/Esc/K/X/S are unbound
- [x] Timeline: filmstrip, cuts, chunk bands, playhead, In/Out, hover preview, click seek, snap (window differs)
- [ ] Chunk editing with budget rules: Add/Save/Suggest done; Extract no longer normalises or saves first
- [x] SSE job pills + toasts + cancel
- [x] Curate: distribution bar, filters, grouped grid, keep/swap/star/promote, bulk actions, lightbox
- [x] Caption dialog -> job (hard-coded models); Settings dialog (no validation)
- [x] Dataset view, export single + all
- [x] Tests: Angular `test` target active with 42 passing specs
- [ ] All `VideoStatus` values selectable (select still 6 options; badges mostly covered)
- [ ] No `alert()` left (3 remain)
