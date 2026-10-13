# Design overhaul plan - Captioning Pipeline Studio

Date: 2026-10-13. Author: Design Agent (analysis and planning only; no source files changed).
Inputs: `GAP_REPORT.md` (section 4), `.claude/skills/{design-system (+references), styling, studio-ui-spec, pipeline-overview, api-contract}`, and every template under `src/web/app/features/**`, `src/web/styles.css`, `src/web/index.html`, `shared/ui/{button,badge,input}`.

Method and limits:
- Static reading only. Nothing was built, served or rendered; no screenshots. "Verified" means "confirmed in source".
- Contrast ratios were computed (WCAG 2.x relative luminance, alpha tints composited over the real underlying surface) from the light-theme token values in `src/web/styles.css:44-76`. Script: session scratchpad `contrast.py` (not committed).
- `features/prompt-clicker/**`, `app.component.ts` and `shell/header.component.ts` are being edited by another agent right now. Line numbers cited for those files are from the snapshot read at 2026-10-13 and may drift.
- Product decision honoured: mim26, **light-first**, dark toggle and `.dark` block go; media surfaces (player, timeline track, thumbnails, lightbox image) stay ink.

---

## 1. Executive summary

**Top findings (by user impact)**

1. **The app is organised by tool, but the work is a linear per-video pipeline.** Four peer tabs (`header.component.ts:46-81`) and a video queue give no answer to "what do I do next with this video?". Nothing reads `video.status` to suggest the next step. Stage state is spread over the queue badge, a status `<select>` (`studio-view.component.ts:46-57`), the header job pills and toasts.
2. **Destructive workflow hazard, not shown in the UI.** `PUT /chunks` replaces every chunk. Ids change and frames/picks cascade-delete (`api-contract`). "Save chunks" (`chunks-panel.component.ts:101-112`) is always enabled, even after 750 candidates have been extracted and curated, so one click can silently wipe the curation work. Separately, "Extract & score 750" does not save first (GAP F2), so unsaved edits are ignored without warning.
3. **Next-action affordances are wrong or missing.** Every toast that has a `videoId` shows "Curate Now" (`toast-container.component.ts:35-42`). That includes "Chunks saved", "Auto-suggest complete", "Captioning complete" and **error** toasts (`chunks-panel.component.ts:389-436`, `openrouter-modal.component.ts:215-219`, `job-event.service.ts:64-68`). Clicking a job pill always jumps to Curate (`header.component.ts:185-188`), even while that video is still extracting and the grid is empty.
4. **Disabled states can't explain themselves.** `hlmBtn` sets `data-disabled:pointer-events-none` (`hlm-button.ts:9`, BrnButton sets `data-disabled`). The only explanation, the `title` on the disabled "Extract & score 750" button (`chunks-panel.component.ts:119-120`), therefore never appears. "Caption" (`curate-toolbar.component.ts:111`) has no explanation at all.
5. **Keyboard-first is not real yet.** Only 5 actions are registered (`app.component.ts:150-181`). `HotkeyService` calls `preventDefault()` on Space globally (`hotkey.service.ts:49-51`), which most likely stops Space from activating focused buttons and checkboxes. The handler (`play_pause`) is not even registered, so Space does nothing anywhere. Clickable `div`s are not focusable: video cards (`video-queue.component.ts:100`), candidate thumbnails (`candidate-grid.component.ts:169`), job pills (`header.component.ts:90`). Keyboard users therefore can't select a video or open a frame. The lightbox advertises "(Left arrow)" (`lightbox-modal.component.ts:58,67`), but ArrowLeft/Right are wired to unregistered seek actions.
6. **Status colour is inconsistent and mostly fails contrast.** Five hand-rolled status palettes are used instead of `hlmBadge` variants. **23 concrete text pairs fail AA** (section 6.1), e.g. "Exported" badge 2.00:1, "Chunked" 1.55:1, "Extracting" 1.83:1, budget badge 1.55-3.49:1, grid "Captioned" white-on-success 2.28:1 (section 6). The helm `success|warning|danger|muted` badges already pass, but none of the status chips use them.
7. **Brand red is used everywhere, so it no longer means "the action".** Studio has three `default` (primary) buttons: Play, Add chunk, Extract (`video-player.component.ts:96,216`; `chunks-panel.component.ts:116`). Red is also used for chunk badges, all distribution numbers, budget %, In/Out timecodes, dataset counts and scores. Budget > 100% is shown in brand red (`chunks-panel.component.ts:335`), and captioning status uses brand red with `animate-pulse` (`video-queue.component.ts:196,207`).
8. **Studio layout fights the task.** The page scrolls vertically: header card, then player (`max-h-[500px]` plus two control rows), then timeline, then the chunk list. On a 1080p screen the chunk list is below the fold while you mark In/Out. The player is centred at `md:max-w-[80%]` (`video-player.component.ts:42`) while the timeline and chunks are full width, so the left edges don't line up. The chunk list is a fixed 840 px grid in a 320 px-tall scroll box (`chunks-panel.component.ts:157,160,175`), so 5-6 of up to 15 rows are visible. It has no `k` and no `tags` columns, although the spec has both.
9. **Curation feedback is missing where it matters.** The distribution bar has no out-of-range warning state; the targets are only 10 px grey text (`distribution-bar.component.ts:8-83`). Candidate state is shown only by border colour plus opacity (`candidate-grid.component.ts:157-167`), which is colour-only. The card actions are about 22 px icon buttons with no accessible names (`candidate-grid.component.ts:215-250`). There is no loading state: while `loadCandidates` runs, the grid shows "No candidates match..." (`candidate-grid.component.ts:257-260`). `curateService.isLoading` is never rendered.
10. **Styling debt.** 82 arbitrary `text-[Npx]` sizes (47 × 10 px, 31 × 11 px, 4 × 9 px) and 49 bare `rounded` (4 px, off-scale). 9 × `py-0.2`, which is probably not generated by Tailwind v4 (not verified). Mixed control heights in one row (24/28/32 px). 21 raw palette/hex uses (19 template lines + 2 canvas literals). 45 `font-mono` uses. Emoji as icons in every view. Hand-rolled modals, toasts, inputs and selects. `HlmTooltip` is imported but never used (`header.component.ts:13,20`).

**Proposed direction in one paragraph.** Replace the four peer tabs with a **pipeline board** (all videos by stage, batch overview, landing page) and a **per-video workspace**. The workspace has a stepper `Chunk → Extract → Curate → Caption → Done`, derived from `video.status` plus active jobs. A sticky **workflow bar** carries the single primary "next action" button and always explains why a step is locked. Job progress lives in one **Jobs popover** in the header and inline in the running step. Toasts appear only for finished or failed jobs and carry the correct next action. Curation becomes keyboard-first: roving focus over cards, K/X/S/F act on the focused card, and a `?` shortcut sheet. Dataset and Prompt Director move to a secondary "Library / Tools" area.

---

## 2. Current design, view by view

### 2.1 Shell (`index.html`, `styles.css`, `app.component.ts`, `shell/header.component.ts`)

| Observation | Where | Impact |
| --- | --- | --- |
| Starts dark (`<html class="dark">`), theme toggle in header, custom `.dark` block | `index.html:2`; `header.component.ts:141-156,172-183`; `styles.css:78-102` | Contradicts the decision; remove |
| Google Fonts (Plus Jakarta Sans, JetBrains Mono) still loaded | `index.html:8-10` | Not in mim26; extra network |
| `body { overflow:hidden; height:100vh; user-select:none }` + `h-screen overflow-hidden` | `styles.css:110-112`; `index.html:12`; `app.component.ts:44` | Filenames, captions and paths can't be copied; `100vh` mobile bug; no page scroll fallback |
| Logo is ⚡ emoji in a red tile + uppercase display wordmark | `header.component.ts:37-42` | Emoji; second red element competing with the active tab |
| Tabs are `hlmBtn` toggled `default`/`ghost`; emoji labels; no `aria-current`; no routing | `header.component.ts:45-82` | Red active tab + red logo + red job icons in one bar; no deep links |
| Job pills: clickable `div` (not focusable) holding a 16 px cancel `button` with `title="Cancel extraction job"` for every job type, no confirm | `header.component.ts:89-111` | Keyboard-inaccessible; wrong label for caption jobs; accidental cancel |
| "Next Video" `outline` + `kbd` in `font-mono text-[10px]` | `header.component.ts:115-126` | OK idea; style off-scale |
| No skip link, no `main#main-content`, `<main>` has no id | `app.component.ts:61` | A11y landmark gap |
| Toasts: custom container, `text-[11px]`, auto-dismiss 9 s with no pause on hover, close button without accessible name, "Curate Now" on every toast with a `videoId` | `toast-container.component.ts:13-53`; `job-event.service.ts:116-124` | Wrong next action; errors vanish after 9 s; not `role=status` / `aria-live` |
| No SSE connection indicator; `onerror` only `console.warn` | `job-event.service.ts:98-100` | Users can't tell if progress is stale |

### 2.2 Video queue (`shell/video-queue.component.ts`)

- Header: uppercase H2 "Video Queue" (overline style, but bold `text-sm`). It has two `icon-xs` (24 px) buttons. One is the 📥 emoji with a nested `title` (`:27-54`).
- Raw `<input>` and `<select>`: `h-8` and `h-7`, `focus:outline-none focus:ring-1` (`:61-86`). The status filter lists 8 statuses, missing `chunking, extracting_750, curating_300, ignored, broken`. Filtering is client-side over the full list, which is fine.
- Card = clickable `div` (`:100-108`): not focusable and has no `aria-selected`. The selected state is `border-primary bg-muted`. Broken cards use `opacity-60`, which drops the meta text to 2.47:1.
- Meta row is all emoji (⏱️📐🎞️📦), with cryptic "3c", "0e" (`:126-131`).
- Status badge text is `(video.status).toUpperCase()` (`:224`). So `extracting_750` (no active job), `chunking` and `ignored` render as "EXTRACTING_750", "CHUNKING", "IGNORED" in the default muted style. Colour mapping (`:192-212`) disagrees with `studio-adaptations.md`: chunked = warning (spec: muted), captioning = brand red + pulse (spec: warning), active extract = `amber-500` (raw palette).
- No skeleton while loading; the empty state is plain 12 px text.
- Missing: per-video progress such as kept/300 or captioned/exported. The API list only has `chunk_count` and `export_count`.

### 2.3 Studio (`studio/*`)

**Video header card** (`studio-view.component.ts:27-60`)
- The video **filename is the page H1**, in uppercase signature style (`:29-31`). This breaks the type rules. With no video selected, the H1 reads "Select a video from the queue".
- Emoji meta pills (`:35-38`).
- The status `<select>` has 6 options (`:46-57`). Five statuses render blank (GAP F3). It is also the wrong control: status is mostly derived by the pipeline and rewritten by reconcile. Only skip, restore and ignore are human decisions.

**Player** (`video-player.component.ts`)
- The stage is `bg-black` (`:47`) and should be `bg-secondary` (ink).
- The watermark draw-mode banner is destructive red (`:72`). Drawing an exclusion rect is not destructive. Its Clear/Done are underlined `text-[11px]` raw buttons with `hover:text-white` (`:74-85`).
- The control bar is one `flex-wrap` row with roughly 20 controls in three groups (`:91-264`):
  - Heights mixed: play `size-8` (overrides helm icon), jump input `h-7`, Go `h-7`, quick jumps `py-1` (~24 px, raw buttons with no focus style, `:136-165`), frame step `xs` → `h-7`, In/Out/Add `sm` (32 px).
  - Glyphs as icons: "◀ Frame", "Frame ▶", "[ In", "] Out", "+ Add Chunk ↵". The Add-chunk `kbd` is white @ 75 % on primary (3.23:1).
  - Raw checkbox for Snap cuts (`:228-236`); raw speed select (`:238-251`); emoji 🏷️ (`:261`).
  - Three primary buttons in Studio (Play, Add chunk, Extract).
- `alert()` for missing In/Out (`:373`). The Add button could be disabled with an inline reason instead.
- Canvas stroke is hex `#D73816` and fill `rgba(215,56,22,.15)` (`:465,470`).

**Timeline** (`timeline.component.ts`)
- The track is `bg-black/60` over white = #666, with thumbs at `opacity-80` (`:26,32`). Cut markers are `bg-yellow-400/80` (`:48`). Chunk bands are `bg-primary/25` with `border-primary/70`; the band edge vs track is **1.06:1** (`:61`), so it is invisible.
- In/Out uses `bg-accent/30 border-accent` (`:77`). In light theme, `accent` is grey #F4F5F6, so In/Out reads as a grey haze, not a marked range. The spec says warning.
- The playhead is primary with `ring-white`, positioned with magic numbers `top-[22px]`, `top-[28px]` (`:101,104`).
- The track is mouse-only: not focusable, no `role="slider"`, no `aria-valuenow`. The hover preview is fine.

**Chunks panel** (`chunks-panel.component.ts`)
- The header crams 6 controls into one row (`:60-134`):
  - a raw bulk-model select with a hard-coded model list (duplicated in 3 files, see 5.3);
  - Auto-suggest (outline, with a red sparkle icon);
  - Sync captions (belongs to the library, not to chunk editing);
  - Save (`secondary` = ink);
  - Extract (`default` = red).
- Budget badge colours: success/primary/warning as **text** (`:332-337`), which fails contrast. Over 100 % uses brand red, not destructive.
- Rows: fixed `grid-cols-[135px_95px_95px_85px_1fr_110px_165px_36px] min-w-[840px]` (`:160,175`) inside `max-h-80` (`:157`), with horizontal scroll inside vertical scroll.
- The whole row is a clickable `div` that seeks. Every cell stops propagation, so a click target is hard to predict.
- `k` and `tags` are **not editable**, although the spec has both.
- The delete icon is a raw button with no accessible name and no undo or confirm (`:289-296`).
- The empty state is plain text that mentions keys ("[ In", "] Out") which don't work (F1).
- The active extraction banner is brand-red tinted (`:138-154`) and duplicates the button label and the header pill.

### 2.4 Curate (`curate/*`, `modals/lightbox-modal.component.ts`)

**Distribution bar** (`distribution-bar.component.ts`)
- Five tiles with emoji labels (`:12,27,42,57`).
- Every value is `text-primary` and every fill is `bg-primary`, regardless of target. There is **no warning state**: the spec ranges (45-55, 20-35, 15-30, ≥20) are not implemented. Targets show as "(Target: 50%)" in `text-[10px]`.
- The meter track `bg-muted` on white is 1.09:1, so the track is invisible and the scale can't be read.
- The "Curated set n/300" KPI sits on `bg-primary/10`. "+N over budget" is primary on primary/10 at 10 px (4.06:1, fails).

**Toolbar** (`curate-toolbar.component.ts`)
- The title is "Curation Grid: {filename in red}" (`:19-21`).
- Three raw selects (`:30-62`). The chunk filter options are just "Chunk #n"; the spec has time range, tags and picks/total.
- "View pool" turns **primary** when active (`:67`).
- "Keep all picks" has no confirm and ignores the chunk filter (server `keep_all` has none). `onRejectAll` exists (`:148-153`) but has no button.
- "Sync captions" is duplicated here as well.
- The primary "VLM Captioning (Cascade)" button is disabled when there are no candidates, with no explanation (`:106-117`).

**Grid** (`candidate-grid.component.ts`)
- Section header:
  - chunk badge primary on primary/10, uppercase (`:67`, 4.06:1, fails);
  - 🏷️ emoji tags (`:77`);
  - captioned chips: success-as-text (2.00:1), ⏳ emoji (`:85-93`);
  - a per-chunk caption-model select (third copy of the model list).
- Per-chunk Keep all / Deselect all loop sequential PATCHes from the client (`:394-406`). There is no progress, no confirm and no undo.
- Cards:
  - state = `border-success|warning|destructive` + `ring-1` + `opacity-40` only (`:157-167`), so colour only. The kept border vs white is 2.28:1; starred is 1.68:1.
  - `bg-black` thumbnail (`:171`), `group-hover:scale-105` without `motion-safe` (`:176`).
  - "✓ Captioned" is white on `bg-success` (2.28:1, `:191`); score and time use `bg-black/70 text-white font-mono` (`:198,206`).
  - **No metric pills** (the spec has sharpness, quality, Multi/Solo, Tool/Eq, Face).
  - Action buttons are `p-1` + 14 px icons (~22 px targets). They have no `sr-only` names and use `title` hints like "Keep frame (K)", which suggest hotkeys that don't work on cards (`:215-250`).
- No loading skeleton; the empty state is the same text for "loading", "not extracted yet" and "filter matches nothing" (`:257-260`).

**Lightbox** (`lightbox-modal.component.ts`)
- Hand-rolled overlay: `bg-black/80` (`:31`), no focus trap, no `role=dialog` / `aria-modal`, and the close button has no accessible name (`:39-44`).
- Prev/next arrows say "(Left arrow)", but those keys are not wired (`:58,67`).
- The active **Keep and Star both use the red `default` variant** (`:95,106`). The star icon is `text-warning` on white (1.68:1).
- Shows "Chunk #{{ c.chunk_id }}" (`:81`), which is the database id, not the 1-based chunk number. This is likely wrong (not verified with data).
- Metrics are `font-mono` everywhere; there are 7 rows of numbers with no visual scale or context.

### 2.5 Dataset (`dataset/dataset-view.component.ts`)

- The page title is an H2 in signature style (`:17`), with no H1.
- "Export all curated videos" has no confirm, no progress and no result summary in the view. Results are not verified, and backend bug B1 makes it skip `curating_300`.
- KPI cards: total images in red, curated videos in ink, and a static "PNG 100% / Lossless sRGB (Snap % 16)" card that is not data (`:79-86`).
- Raw `<table>` with `text-[11px]` uppercase header (`:98-105`).
- The folder column shows `/finished/{slug}/`, while the spec says `data/finished/per_video/{slug}/`, a copy mismatch. The merged-folder column is missing. "Re-Export" has no confirm or feedback.
- Not connected to the per-video workflow: you can't jump from a row to that video's workspace.

### 2.6 Modals

**Settings** (`settings-modal.component.ts`)
- Hand-rolled overlay; labels have colons and **no `for`/`id` association** (`:40-105`).
- "Survivor Fraction to Classifier (%)" has range 0.1-1.0 (`:77-85`), a unit mismatch.
- **Stale form (likely functional bug):** `formData` is copied from `settingsService.settings()` in the constructor (`:153-155`). The component is created when the app renders, before `loadSettings()` resolves (`app.component.ts:140`). So the dialog shows client defaults, and "Save" PUTs them over the server values. This needs a runtime check.

**Caption / OpenRouter** (`openrouter-modal.component.ts`)
- Emoji 🚀 in the title and in the primary label (`:30,150`). The title is "VLM Captioning (Gemma 4 31B ➜ DeepSeek ➜ Grok 4.7 Cascade)".
- A dense 11 px info paragraph (`:44-49`).
- The model list is hard-coded (`:73-80`).
- The frame count falls back to 300 when the distribution isn't loaded (`:175-177`), which can mislead.
- `alert()` on failure (`:223`). The API key goes to `localStorage`.
- This is a **step** of the workflow, presented as a modal launched from a toolbar.

### 2.7 Prompt Director (`prompt-clicker/prompt-clicker-view.component.ts`, under concurrent edit)

- The page sits on `bg-background` (white) with white cards (`:56`) and a right column `bg-card/30` (`:445`). Cards don't separate from the page; it should be `bg-canvas`.
- The H1 is `font-sans font-black` sentence case (`:65`), not signature style.
- Four copy-pasted category blocks (`:204-359`); emoji 👤🎭👩👧 (`:134,153,173,193`). The category icons come from the API (`cat.icon`) and are likely emoji too (not verified).
- 34 arbitrary text sizes, the most of any file: `text-[9px]` "Selected" chip (3.08:1), group labels `muted-foreground/80` (3.60:1), placeholder `/60` (2.47:1).
- The selected character card is primary text on primary/10 (4.06:1), with the description at 80 % opacity (3.18:1). Unselected descriptions are 3.56:1.
- Every selected tag chip is solid primary red. With many selections the left column turns red, which competes with the single primary "Generate" button. The count chip on that button is white on white/20-over-primary (3.56:1).
- The Generate button sits at the end of a long scrolling column, so it is often below the fold.
- There is no dedicated empty or error state for "RAG index not built" (404). It only shows the generic red error box (`:436-441`).

---

## 3. Workflow-oriented redesign

### 3.1 The per-video state machine (drives everything)

Derive a `VideoWorkflow` view-model (one `computed` in a new `WorkflowService`) from `video.status`, `video.broken`, the active job for the video (SSE), local chunk dirty state, and, for the current video, kept-count and caption stats.

| `status` (+ condition) | Step 1 Chunk | Step 2 Extract | Step 3 Curate | Step 4 Caption | Step 5 Done | Next action (single primary) | Badge (variant · lucide icon · label) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `new` | current | locked | locked | locked | locked | **Auto-suggest chunks** (or Mark In) | muted · circle-dashed · New |
| `chunking` / `chunked`, chunks unsaved (dirty) | current, dirty | locked: "Save chunks first" | locked | locked | locked | **Save chunks** | muted · scissors · Chunked |
| `chunked`, saved, budget = 100 | done | current | locked | locked | locked | **Extract & score 750** | muted · scissors · Chunked |
| `chunked`, budget ≠ 100 | current (error) | locked: "Budget is 94% - must be 100%" + "Normalise" link | locked | locked | locked | **Normalise budgets** | danger · triangle-alert · Budget 94% |
| `extracting*` or active `extract_750` job | done | running (progress, step text, cancel) | locked: "Waiting for extraction" | locked | locked | **Next video** (secondary: Cancel job) | warning · loader (motion-safe spin) · Extracting 42% |
| `curating*` | done (locked; "Re-chunk" behind alert-dialog) | done | current (kept n/300, distribution warnings) | available when kept ≥ 1 | locked | **Caption 300 frames** | info · list-checks · Curating 287/300 |
| `captioning` or active `openrouter_caption` | done | done | done (read-only during the job) | running | locked | **Next video** | warning · captions · Captioning 120/300 |
| `exported` | done | done | done | done | current | **Next video** (secondary: Re-export, Open dataset) | success · check · Exported |
| `skipped` / `ignored` | n/a | n/a | n/a | n/a | n/a | **Restore to queue** | muted · ban · Skipped / Ignored |
| `broken` | disabled | disabled | disabled | disabled | disabled | **Skip & next video** | danger · triangle-alert · `broken_reason` |

Rules:
- **Exactly one primary button per screen**, in the workflow bar. Everything else is `outline`, `secondary` or `ghost`.
- **A locked step always says why.** The step tooltip/popover shows the reason, and the workflow bar shows it as visible text next to the disabled primary. Never only a `title` on a disabled button (pointer events are off). Pattern: `<button hlmBtn disabled aria-describedby="why-id">` plus `<p id="why-id" class="text-xs text-muted-foreground">`.
- **Status is derived, not edited.** Replace the status `<select>` (`studio-view.component.ts:46-57`) with an overflow `hlm-dropdown-menu`: "Skip video", "Mark ignored", "Restore", and an "Advanced: set status..." submenu that lists **all** statuses (fixes F3).
- **Chunk lock after extraction.** From `curating*` on, chunk geometry and budgets are read-only. "Edit chunks" opens an `alert-dialog`: "Re-chunking deletes 750 candidates and your curation of this video. Continue?" (`variant="destructive"`). The per-chunk caption model stays editable (`PATCH /chunks/{id}` is safe).
- **Extract = Save + normalise + extract** in one action (fixes F2). If the budget ≠ 100, a confirm shows the proposed normalised values.

### 3.2 Information architecture

```
/                       -> redirect to /board
/board                  Pipeline board (all videos by stage; batch ops; landing)
/v/:id                  -> redirect to /v/:id/<derived step>
/v/:id/chunk            Step 1  Chunk (player + timeline + chunk list)
/v/:id/extract          Step 2  Extract & score (summary + running progress)
/v/:id/curate           Step 3  Curate 300 (grid, distribution, chunk navigator)
/v/:id/caption          Step 4  Caption (inline form, not a modal) + progress
/v/:id/done             Step 5  Done (export summary, folders, re-export)
/dataset                Finished dataset (library-level)
/tools/prompt-director  Prompt Director (independent tool)
```

- Primary nav (header, `aria-current="page"`): **Board** · **Workspace** (last opened video) · **Dataset** · (Tools ▾) **Prompt Director**. Use `hlm-tabs` styled as nav links, or plain `a[hlmBtn variant=ghost]` with an underline active indicator. Do not use red-filled tabs.
- Header right: **Jobs (n)** popover (`hlm-popover`) with a live/reconnecting dot; **Next video** (`secondary`, `kbd` N); Settings (`ghost` icon). No theme toggle.
- Routing (GAP F9) is required for this. It brings deep links, back/forward, and a `?` focus target per route. Recommendation: Angular Router with `withComponentInputBinding()`. This is a product decision, but the stepper makes it necessary.

### 3.3 Wireframes

**App shell (≥ xl)**

```
+--------------------------------------------------------------------------------------------+
| [logo] Captioning Pipeline   Board  Workspace  Dataset  Tools v     (o) Jobs 2   Next ▸ N  ⚙ |  h-14, white, border-b
+--------------------------------------------------------------------------------------------+
| skip link -> main#main-content                                                             |
| <page>                                                                                     |  bg-canvas
+--------------------------------------------------------------------------------------------+
```

**Pipeline board (`/board`, landing).** It replaces the queue sidebar as the place for batch overview.

```
+--------------------------------------------------------------------------------------------+
| PIPELINE BOARD                                                     [Rescan] [Sync captions] |  H1 signature
| 94 videos · 3 need you · 2 running                                                          |
|                                                                                            |
| ( All 94 ) ( New 34 ) ( Chunked 5 ) ( Extracting 2 ) ( Curating 3 ) ( Captioning 1 )       |  toggle-group chips
| ( Exported 46 ) ( Skipped 2 ) ( Broken 1 )                   [ search videos...        ]   |  w/ counts
+--------------------------------------------------------------------------------------------+
| Video                      Stage                          Chunks  Picks/Exp  Dur    Next    |  hlm-table
|--------------------------------------------------------------------------------------------|
| sunset_harbor_02.mp4       ●●●○○  Curating 287/300   [i]   12     287/0     24:10  [Curate]|
| studio_take_7.mp4          ●●◐○○  Extracting 42% ▓▓░░      9      -         11:02  [View]  |
| rooftop_b.mp4              ●○○○○  Chunked                  4      -         08:40  [Extract]
| beach_long.mov             ○○○○○  New                      0      -         52:13  [Chunk] |
| broken_clip.avi            !      Broken: no video stream  -      -         -      [Skip]  |
+--------------------------------------------------------------------------------------------+
  Row click = open workspace at the derived step. Row "Next" = outline button (not primary).
  Picks/Exp per video needs a backend field (see 8. Open questions); until then show chunks/exports.
```

**Workspace frame (all steps).** It has a compact queue rail and a sticky workflow bar.

```
+-------------+------------------------------------------------------------------------------+
| UP NEXT   ‹ | sunset_harbor_02.mp4  24:10 · 1920x1080@25 · h264          [··· Skip/Status] |  H2 sentence case
| [search  ]  |------------------------------------------------------------------------------|
| Needs you   |  (1 Chunk ✓)──(2 Extract ✓)──(3 Curate ●)──(4 Caption)──(5 Done)             |  stepper (nav)
|  ▸ sunset.. |  Kept 287/300 · 2 targets off          [Keep all…] [Caption 300 frames ▸]    |  workflow bar:
|    rooftop  |                                        why-disabled text appears here        |  sticky, 1 primary
| Running     |------------------------------------------------------------------------------|
|  ◐ studio.. |                                                                              |
| Done        |                       <step content>                                         |
|  ✓ ...      |                                                                              |
+-------------+------------------------------------------------------------------------------+
  Rail: w-64, collapsible to icons; below lg it becomes hlm-sheet (left). Grouped by workflow state,
  not raw status. Keyboard: ↑/↓ in rail, Enter opens.
```

**Step 1 - Chunk (two columns at xl, so the chunk list is never below the fold)**

```
+-----------------------------------------------------+------------------------------------+
| [ video stage, ink, aspect-video ]                  | Chunks 6/15      Budget 100% ✓     |
|                                                     | [Auto-suggest] [Normalise] [Save]  |
|                                                     |------------------------------------|
| ▶  00:03:12.40 / 00:24:10.00  [jump 14:30 ][Go]     | #1 00:00:12-00:01:40 1:28  20% k8  |
| [-60][-10][+10][+60]  [‹ frame][frame ›]  1.0x v    |    rule Person v  tags [beach, ..] |
| [ In 00:03:10 ] [ Out 00:03:58 ] [+ Add chunk ⏎]    |    model Gemma 4 v          [🗑]    |
| [◻ Snap to cuts  C]  [Watermark area]               | #2 ...                             |
|-----------------------------------------------------|  (rows = hlm-item cards; expand    |
| [ timeline: filmstrip | cuts | bands | In/Out ]     |   for tags/notes; inputs h-8)      |
|  ← focusable slider: ←/→ seek, [ ] mark            |------------------------------------|
|                                                     | Empty: "No chunks yet. Mark In [   |
|                                                     |  and Out ], or Auto-suggest."      |
+-----------------------------------------------------+------------------------------------+
  Workflow bar primary: "Save chunks" (dirty) -> "Extract & score 750" (saved, 100%).
  Player controls are outline/ghost; Add chunk = secondary (ink). Play = ghost icon on the ink stage.
```

**Step 2 - Extract (running)**

```
+------------------------------------------------------------------------------------------+
| Extracting & scoring 750 candidates                                                       |
| Step: scoring CLIP zero-shot  ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  42%      elapsed 3:10        [Cancel…] |  hlm-progress
| 12 chunks · quota per chunk shown below · you can keep working on another video           |
| [Next video ▸] (primary in bar)                                                           |
| Chunk quotas: #1 60 · #2 45 · ...   (read-only summary of the saved chunks)               |
+------------------------------------------------------------------------------------------+
  Finished -> toast "Extraction complete - Start curating" (action opens /v/:id/curate).
  Error -> inline hlm-alert (destructive) with the server error and [Retry extraction].
```

**Step 3 - Curate**

```
+----------------+-------------------------------------------------------------------------+
| CHUNKS         | Multi 52% ✓(45-55) | Solo 31% ✓ | Tools 12% ! below 15-30 | Face 61% ✓ | KEPT 287/300 |
| #1 48/60 ✓capt |-------------------------------------------------------------------------|
| #2 30/45 ◐     | Show: (Picks)(Runner-ups)(All)   State: (Any)(Kept)(Starred)(Rejected)   |
| #3 12/12 ✓     |                                        [Bulk ▾]   [? Shortcuts]          |
| ...            |-------------------------------------------------------------------------|
| (click = filter|  Chunk #2 · 00:04:10-00:06:30 · beach, rope · 30/45 kept   [Keep all][⋯] |
|  ↑↓ + Enter)   |  [card][card][card][card][card][card]   <- roving focus, ring-ring on   |
|                |  [card][card][card][card][card][card]      focused card only            |
+----------------+-------------------------------------------------------------------------+
 Card: thumb (ink), top-left role pill (PICK #12 primary / RUNNER #3 outline), top-right state
 icon-badge (✓ Kept / ★ Starred / ✕ Rejected = icon + sr text), bottom metric pills (muted:
 users/user/wrench/scan-face), time (tabular-nums). Actions in a hover/focus toolbar plus the keys.
 Workflow bar primary: "Caption 300 frames" (disabled with reason if kept = 0 or job running).
```

**Step 4 - Caption (inline, not a modal)**

```
+------------------------------------------------------------------------------------------+
| Caption 287 kept frames                                                                   |
| API key   (•) Using OPENROUTER_API_KEY from server env   ( ) Use a key for this session   |  radio-group
| Mode      [ Concept ]  [ Character ]  [ Style ]       (radio cards w/ one-line help)      |
| Trigger   [restrained_elegance     ]  Class noun [woman, ropes      ]                     |  hlm-field
| Layer-1 model: per chunk (12 chunks: Gemma 4 ×10, DeepSeek ×2)  [Edit per chunk]          |
| > How the cascade works (collapsible)                                                     |
+------------------------------------------------------------------------------------------+
  Primary in bar: "Start captioning (287 frames)". Running -> progress + per-chunk captioned n/m.
```

**Step 5 - Done**

```
+------------------------------------------------------------------------------------------+
| EXPORTED                                                     (signature = success title)  |
| 287 frames · 287 captions · PNG lossless, edges snapped to 16 px                          |
| Folder  data/finished/per_video/sunset_harbor_02/   [Copy path]                           |
| [Re-export…] [Open in dataset]                       primary in bar: [Next video ▸]       |
+------------------------------------------------------------------------------------------+
```

**Jobs popover (header)**

```
 Jobs  (o) live                                       
 ◐ studio_take_7.mp4    Extract & score   42%  ▓▓▓▓░░  [Open] [Cancel…]
 ◐ harbor_02.mp4        Captioning       120/300 ▓▓░░  [Open] [Cancel…]
 ─ Recent: ✓ rooftop_b extracted 2 min ago · ✕ beach_long failed: <error>  [Open]
```

### 3.4 Cross-cutting behaviour

**Job progress and SSE (one source, three views)**
- `JobEventService` stays the store. Header **Jobs** popover = global list. Board row and rail item = compact progress. The running **step body** = detailed progress. Remove the header pill strip (`header.component.ts:88-112`) and the duplicate red banner in the chunk panel (`chunks-panel.component.ts:138-154`).
- Connection state signal (`connecting|live|reconnecting`) from `onopen`/`onerror` (`job-event.service.ts:98`). Show it as a dot in the Jobs button with text in the popover (fixes F11).
- Keep a "recent jobs" ring (last 10 finished/failed) so failures don't vanish with a 9 s toast. `GET /api/jobs` can back-fill it after reload.
- Cancel always goes through `alert-dialog`, with the label from the job type ("Cancel extraction" / "Cancel captioning").

**Toasts (`hlm-sonner`)**
- Show only events the user didn't trigger in the current view, or outcomes of long jobs. Typed actions: extraction finished → "Start curating"; caption finished → "View export"; job error → "Open video" (destructive styling, persists until dismissed); bulk ops → "Undo" where possible.
- Never attach "Curate Now" generically. `showToast(title, msg, {action?: {label, route}})` replaces the `videoId` side channel.
- Pause on hover; errors don't auto-dismiss; the region is `aria-live="polite"`.

**Keyboard-first (one keymap, scoped by route, visible everywhere)**
- Fix the global handler. Don't `preventDefault()` Space/Enter when the target is a button, link, checkbox or `[role=option|gridcell]`. Skip handling when a dialog is open (except its own keys) or the target is `contenteditable` (spec).
- Chunk step: the full spec table (Space, J/L, arrows with Shift/Ctrl/Alt, `,` `.`, `[`/I, `]`/O, Enter, C, G, PageUp/Down).
- Curate step (new):

| Key | Action |
| --- | --- |
| ←/→/↑/↓ | move focus between cards |
| Home/End | first/last card in chunk |
| PageUp/PageDown | previous/next chunk |
| K | keep focused card |
| X | reject (swap if pick) |
| S | star |
| P | promote runner-up |
| W | swap |
| Enter / Space | open lightbox |
| Shift+K | keep all in chunk |
| U | undo last state change |
| R | toggle runner-ups |
| F | cycle state filter |

  In the lightbox, ←/→ = previous/next and K/X/S apply and **advance** to the next card (fast triage).
- Global: N next video, `?` shortcut sheet (`hlm-sheet` side right, grouped by context), `g b` / `g d` go to board/dataset (optional).
- Discoverability: every shortcut-bearing button gets an `hlmTooltip` with a `kbd` (generate `kbd`). The workflow bar shows a context hint line ("K keep · X reject · S star · ⏎ open · ? all shortcuts").
- Grid semantics: `role="grid"` per chunk section, roving `tabindex`, `aria-selected`. Each card has a label like "Pick 12, 00:04:12, kept, multiple people, face visible".

**Empty, loading and error states (catalogue)**

| Surface | Loading | Empty | Error |
| --- | --- | --- | --- |
| Board / rail | `hlm-skeleton` rows (6) | `hlm-empty`: "No videos found in {video_dir}" + [Rescan] | `hlm-alert` destructive: "Could not load videos" + [Retry] (today `console.error` only, `video.service.ts:59`) |
| Board filter | - | "No videos match 'xyz' in Curating" + [Clear filters] | - |
| Chunk list | skeleton rows | "No chunks yet. Mark In `[` and Out `]`, or Auto-suggest." [Auto-suggest] | inline alert on save/suggest failure (keep edits) |
| Timeline | shimmer track | "Filmstrip not generated yet" + spinner | "Filmstrip failed" + [Retry] |
| Extract | progress | - | alert with server error + [Retry] |
| Curate grid | skeleton cards per chunk (uses `curateService.isLoading`) | three distinct states: "Not extracted yet - Extract & score 750 first" [Go to Extract]; "Filter matches no frames" [Reset filters]; "All picks rejected in this chunk" [Show runner-ups] | alert + [Retry] |
| Caption | button spinner | - | inline field errors; request error = alert, not `alert()` |
| Dataset | skeleton KPIs/table | "Nothing exported yet. Finish a video to see it here." [Open board] | alert |
| Prompt Director | skeleton chips | "RAG index not built. Run `scripts/index_rag.py` in WSL." (detect 404) | alert |

**Destructive actions and dirty state**
- `alert-dialog` for: re-chunk after extraction, delete chunk with picks, Deselect/Reject all (chunk or video), cancel job, re-export (overwrites), reset Prompt Director selections (optional).
- Chunk dirty tracking: `ChunkService.isDirty` (compare to last loaded). Show a "Unsaved changes" pill in the chunk panel header and in the workflow bar. Also add a `canDeactivate` guard / `beforeunload` when leaving the workspace or switching video with unsaved chunks.
- Undo for single-frame state changes (keep a stack of the last N `{frameId, prevState}`) as a cheap alternative to confirms.

### 3.5 Spartan component map (what replaces what)

| Need | Use | Status in repo |
| --- | --- | --- |
| Nav links / step tabs | `hlm-tabs` or `a[hlmBtn]` + router `aria-current` | vendored |
| Stepper | compose: `nav > ol` of `hlm-item` / buttons + `hlm-separator`, step state via `data-state` | compose (no Spartan stepper) |
| Queue rail drawer < lg | `hlm-sheet` side left | vendored |
| Board table | `hlm-table` | vendored |
| Stage filter chips, curate role/state filters | `toggle-group` | **generate** (`toggle`/`toggle-group` not vendored) |
| Status / role / metric pills | `hlmBadge` (`success\|warning\|danger\|muted\|outline` + **add `info`**) | vendored, add variant |
| Progress (jobs, meters, budget) | `hlm-progress` | vendored |
| Jobs popover | `hlm-popover` | vendored |
| Overflow menus (status, bulk) | `hlm-dropdown-menu` | vendored |
| Dialogs (settings, lightbox) | `hlm-dialog`; destructive confirm `hlm-alert-dialog` | vendored |
| Toasts | `hlm-sonner` | vendored |
| Form fields | `hlm-field` + `hlmInput` / `hlm-native-select` / `hlm-select` / `hlm-checkbox` / `textarea` + Signal Forms | vendored |
| Snap cuts toggle | `switch` | **generate** |
| Shortcut hints | `kbd` | **generate** |
| Budget per chunk | `slider` (or number input + progress) | **generate** |
| Caption mode | `radio-group` | **generate** |
| Tooltips | `hlmTooltip` (replace all ~44 `title=` hints) | vendored, unused |
| Empty/loading | `hlm-empty`, `hlm-skeleton`, `hlm-spinner` | vendored, unused |
| Inline errors | `hlm-alert` | vendored |
| "How the cascade works" | `hlm-collapsible` | vendored |
| Icons | `@ng-icons/lucide` via `NgIcon` | in use |

`components.json` must be created before the CLI can generate `switch`, `kbd`, `slider`, `toggle-group`, `radio-group` (styling skill). Run it in WSL.

---

## 4. Spacing review

### 4.1 Findings

| # | Issue | Evidence |
| --- | --- | --- |
| S1 | **82 arbitrary font sizes** below the type scale: `text-[10px]` ×47, `text-[11px]` ×31, `text-[9px]` ×4. Most used: prompt-clicker 34, openrouter-modal 8, video-player 8, distribution-bar 6, candidate-grid 6 | e.g. `header.component.ts:98,125`; `video-queue.component.ts:88,118,126,142`; `distribution-bar.component.ts:21,36,51,66,71,79`; `prompt-clicker...:155,175,195,427` (9 px) |
| S2 | **Mixed control heights in the same row**: `h-6` (icon-xs), `h-7` (inputs/selects/xs buttons forced), `h-8` (sm), `size-8` play (overrides helm `icon`), raw quick-jump buttons ≈ 24 px. Helm defaults are `h-9` desktop / `h-11` mobile | `video-player.component.ts:100,122,129,139-160,175,185`; `chunks-panel.component.ts:63,217,229,261,276`; `curate-toolbar.component.ts:33,45,56,71,83,97`; `candidate-grid.component.ts:101,117,129,141` |
| S3 | **Half-steps off the documented scale** (`ui-tokens.md`: 0.5/1/2/3/4/5/6/8): `gap-1.5` ×32, `gap-2.5` ×7, `px-1.5` ×23, `px-2.5` ×9, `p-2.5` ×5, `pb-2.5`, `space-y-2.5` ×3, `space-y-1.5` | all feature files |
| S4 | **`py-0.2` ×9** - not a multiple of 0.25; most likely emits no CSS in Tailwind v4 (unverified), so the intended padding is absent | `chunks-panel.component.ts:198,202`; `prompt-clicker...:155,173,175,193,195,427,639` |
| S5 | **Page padding differs per view**: Studio `p-4 md:p-6 gap-4`, Curate `p-3 md:p-5 gap-3`, Dataset `p-4 md:p-6 gap-6`, Prompt header `px-6 py-3` + columns `p-4 md:p-6 space-y-5` | `studio-view.component.ts:23`; `curate-view.component.ts:18`; `dataset-view.component.ts:13`; `prompt-clicker...:58,127,445` |
| S6 | **Card padding differs**: `p-2`/`p-2.5` (queue cards, distribution tiles), `p-3` (toolbar, distribution bar), `p-3 md:p-4` (grid sections), `p-4` (studio header, chunks), `p-5` (KPI cards, lightbox side), `p-6` (modals) | `video-queue.component.ts:102`; `distribution-bar.component.ts:8,10`; `curate-toolbar.component.ts:16`; `candidate-grid.component.ts:62`; `dataset-view.component.ts:59`; `lightbox-modal.component.ts:74` |
| S7 | **Bare `rounded` ×49** (Tailwind default 4 px; the theme only defines sm..2xl) next to `rounded-md/lg/xl/full`. Chips are `rounded`, `rounded-md`, `rounded-full` or `rounded-4xl` (badge) at random | `candidate-grid` 13, `chunks-panel` 10, `openrouter-modal` 6, `prompt-clicker` 5 ... |
| S8 | **Fixed pixel grid** for chunk rows (`135/95/95/85/1fr/110/165/36 px`, `min-w-[840px]`) inside `max-h-80`: horizontal + vertical scroll, 5-6 visible rows of 15 | `chunks-panel.component.ts:157,160,175` |
| S9 | **Misaligned columns in Studio**: player host `md:max-w-[80%] mx-auto` vs full-width timeline/chunks; `max-h-[500px]` on the stage | `video-player.component.ts:42,47` |
| S10 | **Magic offsets** in the timeline (`pt-7`, `top-[22px]`, `top-[28px]`, `w-[1.5px]`) tie the playhead to the label height | `timeline.component.ts:22,48,86,101,104` |
| S11 | Hit targets in dense areas are below 24 px (WCAG 2.5.8 AA minimum): card actions `p-1`+14 px ≈ 22 px, toast/job cancel ≈ 16-18 px, chunk delete `p-1`+16 px ≈ 24 px | `candidate-grid.component.ts:215-250`; `header.component.ts:103-109`; `toast-container.component.ts:45-50`; `chunks-panel.component.ts:289-296` |
| S12 | Arbitrary viewport sizes in the lightbox (`max-h-[90vh]`, `max-h-[85vh]`, `min-h-[300px]`, `lg:min-h-[500px]`) | `lightbox-modal.component.ts:35,47,51` |

### 4.2 Proposed density system for this desktop-first tool

Within the mim26 scale (`ui-tokens.md`). Write it into `studio-adaptations.md` so it becomes the rule.

| Element | Rule |
| --- | --- |
| Smallest text | `text-xs` (12 px). Delete all `text-[9|10|11px]`. Overline = `text-xs font-bold uppercase tracking-widest text-muted-foreground` |
| Page | `p-4 lg:p-6`, sections `gap-4` (`gap-6` between major regions on board/dataset) |
| Panel (card) | `rounded-xl border bg-card`, header row `px-4 py-3` (+ `border-b`), body `p-4`, internal `gap-3` |
| Dense toolbars (player, curate filters) | all controls helm `size="sm"` (32 px) / `icon-sm` (32 px); groups `gap-2`; separators between groups. No forced `h-7` |
| Lists / rows | `px-3 py-2 gap-3`; row height ≥ 40 px |
| Card grid | `gap-3`; section gap `gap-4`; thumbnails `rounded-md` |
| Chips / badges | helm `hlmBadge` (h-5, `rounded-4xl` from primitive) - never hand-rolled spans |
| Hit area | ≥ 32 px desktop for anything clickable (≥ 44 px below `md` via helm defaults) |
| Radii | `rounded-md` controls (helm), `rounded-lg` inner tiles, `rounded-xl` panels/dialogs, `rounded-full` pills. Ban bare `rounded` |
| Timecodes / numbers | `tabular-nums` (no `font-mono`; 45 uses across 8 files) |

---

## 5. Colour review

### 5.1 Raw palette and hex (must become tokens)

| Where | Current | Replace with |
| --- | --- | --- |
| `video-player.component.ts:47`, `candidate-grid.component.ts:171`, `lightbox-modal.component.ts:47` | `bg-black` | `bg-secondary` (ink #1A1A1A) |
| `timeline.component.ts:26` | `bg-black/60` | `bg-secondary` (solid ink track) |
| `timeline.component.ts:48` | `bg-yellow-400/80` cut lines | `bg-background/70` 2 px (spec) |
| `timeline.component.ts:101` | `ring-white` | `ring-background` |
| `video-player.component.ts:76,82` | `hover:text-white` | `hover:text-destructive-foreground` (or drop: banner becomes neutral) |
| `candidate-grid.component.ts:191` | `bg-success text-white` | `hlmBadge variant="success"` + check icon on a `bg-background/90` chip |
| `candidate-grid.component.ts:198,206` | `bg-black/70 text-white` | `bg-secondary/80 text-secondary-foreground` |
| `lightbox-modal.component.ts:41,57,66` | `bg-black/60 text-white hover:bg-black/90` | `bg-secondary/70 text-secondary-foreground hover:bg-secondary` |
| `settings-modal:17`, `lightbox-modal:31`, `openrouter-modal:20` | `bg-black/80` scrim | `hlm-dialog` overlay (`bg-foreground/*` per styling adaptations) |
| `prompt-clicker...:625` | `bg-black/40` | `bg-secondary` |
| `video-queue.component.ts:198` | `amber-500` | `hlmBadge variant="warning"` |
| `video-player.component.ts:465,470` | `'#D73816'`, `rgba(215,56,22,.15)` | `getComputedStyle(document.documentElement).getPropertyValue('--primary')` + `globalAlpha`. Better: use `--info` for a neutral "exclusion" overlay |

### 5.2 Semantic misuse

- **Brand red as decoration or status (rule 1).** Studio header pills and chunk badges; distribution % values and fills; budget % text (`chunks-panel.component.ts:251`); In/Out timecodes (`video-player.component.ts:202,212`); dataset counts (`dataset-view.component.ts:111`); lightbox score (`lightbox-modal.component.ts:137`); RAG score/badges (`prompt-clicker...:68,456,633`); sparkle icons; toast success icon (`toast-container.component.ts:22`).
- **Brand red as error.** Budget > 100 % (`chunks-panel.component.ts:335`) and "+N over budget" (`distribution-bar.component.ts:79`) should be `danger` (destructive) with an icon.
- **Destructive red for a non-destructive mode.** The watermark draw banner (`video-player.component.ts:72`) and button variant (`:255`).
- **Primary for toggles and states.** View-pool toggle (`curate-toolbar.component.ts:67`); lightbox Keep/Star active (`lightbox-modal.component.ts:95,106`); selected RAG chips (`prompt-clicker...:228` etc.). Toggles should use `toggle`/`toggle-group` (`data-[state=on]:bg-muted` + check icon) or `secondary`.
- **`accent` used as a highlight colour.** In/Out range (`timeline.component.ts:77`). `accent` is #F4F5F6 in light mode, a hover grey.
- **Success/warning as text or icon colour on white** (forbidden in `colors.md`). Queue badges, budget badge, captioned chips, `text-success` check icons (`curate-toolbar.component.ts:85`, `candidate-grid.component.ts:120`), `text-warning` star (`lightbox-modal.component.ts:111`, `candidate-grid.component.ts:226`).
- **Five separate status palettes** (queue, budget, chunk-captioned, grid-captioned, toasts) instead of one mapping.
- **Animated status** (`animate-pulse` on captioning badges, `video-queue.component.ts:196,207`; `text-primary animate-pulse` ⚡ in job pills, `header.component.ts:95`). This is not `motion-safe`, and pulse is decoration rather than information.

### 5.3 Proposed status colour system (one source of truth)

Create `core/ui/status.ts`: `videoStatusMeta(status, job?) → {variant, icon, label}`, used by board, rail, workspace header and Jobs popover. Add an `info` badge variant: `border-info/30 bg-info/10 text-foreground [&>ng-icon]:text-info` (text 14.94:1, icon 5.54:1).

| VideoStatus | Variant | Icon | Label (sentence case) |
| --- | --- | --- | --- |
| `new` | muted | circle-dashed | New |
| `chunking` | muted | scissors | Chunking |
| `chunked` | muted | scissors | Chunked |
| `extracting`, `extracting_750`, active extract job | warning | loader-circle (`motion-safe:animate-spin`) | Extracting · 42% |
| `curating`, `curating_300` | info | list-checks | Curating · 287/300 |
| `captioning`, active caption job | warning | captions | Captioning · 120/300 |
| `exported` | success | check | Exported |
| `skipped` | muted | skip-forward | Skipped |
| `ignored` | muted | ban | Ignored |
| `broken` (flag) | danger | triangle-alert | Broken: {broken_reason} (truncate, full text in tooltip) |

Frame/role/state:

| Item | Treatment |
| --- | --- |
| PICK #n | `hlmBadge` default (white on primary 4.70:1). This is the only red on a card |
| RUNNER #n | `hlmBadge variant="outline"` on `bg-background/90` |
| kept | `success` icon-badge (check) + sr "Kept" |
| starred | `warning` icon-badge (star, `text-foreground` icon) + "Starred" |
| rejected | `danger` icon-badge (x) + "Rejected" + image `opacity-60` (text stays 100 %) |
| captioned | `outline` badge with captions icon "Captioned" |
| focused or selected | `ring-2 ring-ring ring-offset-2` |
| Metric pills | `muted` + lucide icon (users, user, wrench, scan-face, focus, sparkles) |
| Distribution in range | `progress` fill `bg-foreground` (ink) or `bg-info` |
| Distribution out of range | fill `bg-warning` + triangle-alert + "below 45-55 % target" |
| Distribution KPI 287/300 | signature KPI in ink; **red only if it is the primary target highlight** (choose one) |

Model list: hard-coded in 4 places (`chunks-panel.component.ts:67-72,278-283`, `candidate-grid.component.ts:104-109`, `openrouter-modal.component.ts:74-79`). Move it to one `CaptionModelService` fed by `GET /api/openrouter/models` (GAP F7).

---

## 6. Contrast review (light theme, computed)

Base matrix from `colors.md` is confirmed (white/ink 17.40, primary on white 4.70, muted-fg on white 5.52 / canvas 4.62 / muted 5.06). Tinted pairs are composited over the actual surface (white card unless noted; "sel." = selected queue card `bg-muted`). Threshold 4.5 for text (all of these are ≤ 14 px), 3.0 for icons/UI boundaries.

### 6.1 Failures (text)

| Pair | Where | Ratio | Need |
| --- | --- | --- | --- |
| warning #FFBC1F on warning/15 - "CHUNKED" badge | `video-queue.component.ts:202` | **1.55** (sel. 1.44) | 4.5 |
| amber-500 on amber/20 - "EXTRACTING" badge | `video-queue.component.ts:198` | **1.83** (sel. 1.70) | 4.5 |
| success #22C55E on success/15 - "EXPORTED" badge | `video-queue.component.ts:209` | **2.00** (sel. 1.85) | 4.5 |
| primary on primary/15 - "CAPTIONING" badge | `video-queue.component.ts:207` | **3.77** (sel. 3.49) | 4.5 |
| primary on primary/20 - active-caption badge | `video-queue.component.ts:196` | **3.49** (sel. 3.22) | 4.5 |
| destructive on destructive/10 - broken badge on selected card | `video-queue.component.ts:193` | **4.12** (4.51 on white, borderline) | 4.5 |
| muted-fg @ 60 % - broken card meta | `video-queue.component.ts:107,126` | **2.47** | 4.5 |
| Budget badge ok / <100 / >100 (success, warning, primary on tints) | `chunks-panel.component.ts:334-336` | **2.00 / 1.55 / 3.49** | 4.5 |
| Chunk "✓" captioned chip success on success/15 | `chunks-panel.component.ts:198` | **2.00** | 4.5 |
| Chunk progress chip / active-row budget %: primary on primary/10 | `chunks-panel.component.ts:202,251` (active row) | **4.06** | 4.5 |
| Grid "✓ Captioned" white on success | `candidate-grid.component.ts:191` | **2.28** | 4.5 |
| Grid section chunk badge / partial-caption chip: primary on primary/10 | `candidate-grid.component.ts:67,90` | **4.06** | 4.5 |
| Grid section "✓ Captioned (n/m)" success on success/15 | `candidate-grid.component.ts:86` | **2.00** | 4.5 |
| Rejected card content at `opacity-40` | `candidate-grid.component.ts:165` | **2.52** (text), 1.77 (icons) | 4.5 / 3 |
| "+N over budget" primary 10 px on primary/10 | `distribution-bar.component.ts:79` | **4.06** | 4.5 |
| Add-chunk `kbd` white @ 75 % on primary | `video-player.component.ts:222` | **3.23** | 4.5 |
| Timeline chunk label white on primary/25 over a bright thumbnail | `timeline.component.ts:61,67` | **1.84** (5.94 over bare track) | 4.5 |
| PD selected character title / desc @ 80 % | `prompt-clicker...:148,158` | **4.06 / 3.18** | 4.5 |
| PD unselected character desc @ 80 % | `prompt-clicker...:158,178,198` | **3.56** | 4.5 |
| PD "Selected" 9 px chip primary on primary/20 | `prompt-clicker...:155,175,195` | **3.08** | 4.5 |
| PD group label `muted-foreground/80` 10 px | `prompt-clicker...:217,333` | **3.60** | 4.5 |
| PD textarea placeholder `muted-foreground/60` | `prompt-clicker...:375` | **2.47** | 4.5 (advisory) |
| PD Generate count chip white on white/20-over-primary | `prompt-clicker...:427` | **3.56** | 4.5 |
| primary text on `bg-canvas` (any red label placed directly on the page) | rule from `colors.md` | **3.94** | 4.5 |
| primary text on `bg-muted` (e.g. red value in a muted tile) | rule | **4.31** | 4.5 |

### 6.2 Failures (icons, UI boundaries, non-text, 3:1)

| Pair | Where | Ratio |
| --- | --- | --- |
| success icon/border on white (kept check, kept card border) | `curate-toolbar.component.ts:85`; `candidate-grid.component.ts:120,159-161,218` | **2.28** |
| warning icon/border on white (star, starred border) | `candidate-grid.component.ts:162-163,226-227`; `lightbox-modal.component.ts:111` | **1.68** |
| Meter track `bg-muted` vs white (distribution bars) | `distribution-bar.component.ts:15,30,45,60` | **1.09** (the fill vs track 4.31 is fine; the track end is invisible) |
| Job/queue progress track `bg-border` vs `bg-muted` | `header.component.ts:100`; `video-queue.component.ts:136` | **1.29** |
| Timeline chunk band border primary/70 vs ink track | `timeline.component.ts:61` | **1.06** |
| Timeline In/Out `accent/30` vs track | `timeline.component.ts:77` | **1.82** |
| Timeline cut line yellow-400/80 vs track | `timeline.component.ts:48` | **2.92** |
| Card borders `border-border` vs white (decorative, allowed) | everywhere | 1.40 (OK only as decoration, never as the sole state cue) |

### 6.3 Passing pairs worth keeping

`hlmBadge` success/warning/danger variants (ink text on tint: 14.6-16.0); `muted` badge 5.06; info text on info/15 (5.13; sel. 4.72); white on primary 4.70; white on destructive 5.38; muted-fg on white/muted/canvas (5.52/5.06/4.62); `border-input` on white 3.44; focus ring primary vs white 4.70 / vs muted 4.31; grid score chip white on ink/70 8.45+.

### 6.4 Focus, disabled, motion

- **Focus rings.** Raw inputs/selects use `focus:outline-none focus:ring-1 focus:ring-ring` (1 px). This passes contrast (4.70) but is thin and inconsistent with helm `ring-3`. Raw buttons have no focus style at all: quick jumps, card actions, toast/job/lightbox/modal close, chunk delete, RAG chips. Replace them with helm primitives. `ring-ring/50` would fail (2.18); keep the styling rule "never `/50`".
- **Non-focusable interactive elements** (fail 2.1.1): `video-queue.component.ts:100`, `candidate-grid.component.ts:169`, `header.component.ts:90`, `timeline.component.ts:24` (track), `chunks-panel.component.ts:173` (row seek).
- **Disabled states.** Disabled primary renders white on #EB9C8A (2.18) and disabled outline 3.36. WCAG exempts disabled controls, so this isn't a failure. But because the control can't be hovered or focused (`tabindex=-1`, `pointer-events-none`), the **reason must be visible text** (`aria-describedby`), never `title`.
- **Status by colour only.** Card states (border), budget badge (colour + number), and the queue selected state (`border-primary` only) need icon + text.
- **Motion.** `animate-pulse` (`header.component.ts:95`, `video-queue.component.ts:196,207`), `animate-spin` (several), and `group-hover:scale-105` (`candidate-grid.component.ts:176`) are not prefixed with `motion-safe:`. The global reduced-motion rule (`styles.css:137-144`) neutralises them, so this is low risk; prefix them anyway per `ui-tokens.md`.

### 6.5 Fix strategy and token proposal (needs product-owner sign-off)

1. **Default fix, no new tokens.** Every status chip becomes `hlmBadge` (`success|warning|danger|muted|info`) with ink text and an icon. This resolves all badge rows in 6.1.
2. **Icons/borders that must carry state on white** (kept, starred): put them inside a tinted badge with an ink icon (passes), or add two "strong" tokens to `styles.css` + `colors.md`:
   - `--success-strong: #15803D` (5.02:1 on white, 4.60 on success/10; usable as text and icon)
   - `--warning-strong: #8A5A00` (5.93 on white, 5.46 on warning/15)

   Recommendation: start with (1). Add the strong tokens only if a coloured icon on white is unavoidable. This is a design-system change, so flag it to the owner.
3. **Red tints for text** (primary on primary/10-20 = 3.1-4.1) are not allowed for small text. Use ink text on the tint plus a red icon or border, or solid primary with white text.
4. **Timeline on ink:** cuts `bg-background/70`; In/Out `bg-warning/40 border-warning` (bright on ink); chunk bands solid `bg-primary/60` with white `#n` label on a `bg-secondary/80` label chip, so it stays readable over any thumbnail.

---

## 7. Phased plan

Effort: **S** ≤ 2 h, **M** ½-1 day, **L** 2-4 days (single developer, includes tests where applicable). Coordinate with the agent currently editing `prompt-clicker/**`, `app.component.ts` and `header.component.ts`, and do not start Phase 0 items in those files until that work has merged.

### Phase 0 - Quick wins (≈ 2-3 days total, no IA change)

| # | Item | Files | Effort |
| --- | --- | --- | --- |
| Q1 | Light-first: drop `class="dark"`, `.dark` block, theme toggle, Google Fonts; remove `user-select:none` from body (keep it only on the timeline/player) | `index.html:2,8-10`; `styles.css:78-102,112`; `header.component.ts:141-156,172-183` | S |
| Q2 | One status mapping (`status.ts`) + `hlmBadge` (+ new `info` variant) in queue; labels sentence case; all statuses covered (no raw `toUpperCase`) | `shared/ui/badge/src/lib/hlm-badge.ts`; `shell/video-queue.component.ts:117-122,192-225`; new `core/ui/status.ts` | S |
| Q3 | Fix failing chips: budget badge → `hlmBadge success/danger` + icon + "Total 94% - must be 100%"; captioned chips → `hlmBadge`; grid "Captioned" → outline badge | `chunks-panel.component.ts:52-57,196-206,332-337`; `candidate-grid.component.ts:85-93,190-194` | S |
| Q4 | Toast actions: only "Extraction complete" gets "Start curating"; others get none or the right one; errors persist | `job-event.service.ts:64-91,116-124`; `toast-container.component.ts:35-42`; callers in `chunks-panel.component.ts:389-436`, `openrouter-modal.component.ts:215` | S |
| Q5 | Hotkeys: don't `preventDefault` on interactive targets; register all Studio keys (F1) and lightbox ←/→; K/X/S advance in lightbox | `hotkey.service.ts:39-123`; `app.component.ts:150-181` (or per-view registration); `video-player.component.ts` | M |
| Q6 | Disabled-with-reason pattern: visible helper text under Extract/Caption/Add-chunk; replace `alert()` ×3 with inline text/toast (F5) | `chunks-panel.component.ts:114-133`; `curate-toolbar.component.ts:106-117`; `video-player.component.ts:367-375`; `chunk.service.ts:53`; `openrouter-modal.component.ts:223` | S |
| Q7 | Extract = save (+normalise) then extract (F2); chunk dirty flag + "Unsaved changes" pill | `chunk.service.ts:128-150`; `chunks-panel.component.ts` | M |
| Q8 | Guard re-chunk after extraction (alert-dialog when status ∈ curating*/captioning/exported) | `chunks-panel.component.ts:101-112`; `chunk.service.ts:128` | S |
| Q9 | One primary per view: Play → ghost/outline icon; Add chunk → secondary; View-pool → toggle; lightbox Keep/Star active → secondary + icon; watermark mode → outline/info | `video-player.component.ts:94-107,214-223,253-262,72`; `curate-toolbar.component.ts:65-75`; `lightbox-modal.component.ts:93-124` | S |
| Q10 | Tokens for media surfaces (`bg-secondary`), remove raw palette/hex (table 5.1) | `video-player`, `timeline`, `candidate-grid`, `lightbox-modal`, modals, `video-queue:198` | S |
| Q11 | Type scale: replace 82 `text-[Npx]` with `text-xs`/`text-sm`; `font-mono` → `tabular-nums`; bare `rounded` → scale; drop `py-0.2` | all feature files (prompt-clicker after the other agent merges) | M |
| Q12 | Settings dialog: re-read settings on open (stale-form bug), label `for`/`id`, fix "%" unit | `settings-modal.component.ts:40-105,153-155` | S |
| Q13 | Curate loading/empty states (skeleton while `curateService.isLoading`; 3 distinct empty messages) | `candidate-grid.component.ts:257-260`; `curate.service.ts` | S |
| Q14 | Emoji → lucide everywhere (header logo/tabs, queue meta, studio pills, distribution, grid, modals) | listed in `studio-adaptations.md` compliance table | S-M |

### Phase 1 - Primitive and token compliance (≈ 1-1.5 weeks)

| # | Item | Files | Effort |
| --- | --- | --- | --- |
| P1 | `components.json` + generate `switch`, `kbd`, `slider`, `toggle-group`, `radio-group` (in WSL) | repo root, `shared/ui/*` | S |
| P2 | Modals → `hlm-dialog` (Settings, Lightbox) + `alert-dialog` (destructive confirms) with focus trap, `aria-modal`, named close buttons | `modals/*.component.ts`, `app.component.ts` | M |
| P3 | Toasts → `hlm-sonner` with typed actions, `aria-live` | `toast-container.component.ts`, `job-event.service.ts` | S |
| P4 | Forms → helm `field`/`input`/`native-select`/`checkbox` + Signal Forms (settings, caption, filters, chunk rows) (F8) | `settings-modal`, `openrouter-modal`, `video-queue`, `curate-toolbar`, `chunks-panel`, `video-player` | L |
| P5 | Tooltips: replace ~44 `title=` with `hlmTooltip` + `kbd` | all features | M |
| P6 | Distribution bar → `hlm-progress` with target ranges, warning state + text, visible target labels | `distribution-bar.component.ts` | S |
| P7 | Candidate card redesign (role/state icon-badges, metric pills, 32 px actions with sr names, ring for focus) | `candidate-grid.component.ts` | M |
| P8 | Timeline restyle on ink + focusable `role=slider` with keyboard seek | `timeline.component.ts` | M |
| P9 | Caption-model list from `GET /api/openrouter/models` via one service (F7) | `chunks-panel`, `candidate-grid`, `openrouter-modal`, new `caption-model.service.ts` | S |
| P10 | Landmarks: skip link, `main#main-content`, `aria-current`, focusable video items (`button`/`role=option` listbox) | `app.component.ts`, `header.component.ts`, `video-queue.component.ts` | S |

### Phase 2 - Workflow IA (≈ 2 weeks)

| # | Item | Files | Effort |
| --- | --- | --- | --- |
| W1 | Angular Router with the routes in 3.2 (F9); `currentTab` signal removed; deep links `?video=` replaced by `/v/:id/:step` | `app.config.ts`, `app.component.ts`, `main.ts`, new `app.routes.ts` | M |
| W2 | `WorkflowService` (state machine 3.1) + unit tests | new `core/services/workflow.service.ts` (+ spec once a test target exists) | M |
| W3 | Workspace frame: stepper + sticky workflow bar + video header (H2) + status overflow menu (F3) | new `features/workspace/*`; retire `studio-view` header card | L |
| W4 | Pipeline board (`/board`): stage chips with counts, `hlm-table`, search, row next-action, Rescan/Sync | new `features/board/*`; reuses `VideoService` | L |
| W5 | Queue rail (compact, grouped "Needs you / Running / Done", sheet below lg) | refactor `shell/video-queue.component.ts` | M |
| W6 | Jobs popover + SSE connection state + recent-jobs history (F11) | `header.component.ts`, `job-event.service.ts` | M |
| W7 | Studio two-column layout at xl (player+timeline left, chunk list right), chunk rows as `hlm-item` with k/tags editable | `studio/*` | L |
| W8 | Extract step page (progress, error/retry, quotas) | new `features/workspace/extract-step.component.ts` | S |
| W9 | Caption step inline (from modal), Done step (export summary, copy path, re-export confirm) | `modals/openrouter-modal` → `features/workspace/caption-step`; new `done-step`; `dataset.service.ts` | M |
| W10 | Dataset: page H1, `hlm-table`, row → workspace link, confirm + result summary for export-all (needs backend B1 fix) | `dataset-view.component.ts`, `api.py` (B1) | M |

### Phase 3 - Keyboard-first curation and polish (≈ 1 week)

| # | Item | Files | Effort |
| --- | --- | --- | --- |
| K1 | Roving-focus grid (`role=grid`), K/X/S/P/W/U on the focused card, chunk navigator column, PageUp/Down between chunks | `candidate-grid.component.ts`, `curate.service.ts`, `hotkey.service.ts`, new `curate/chunk-nav.component.ts` | L |
| K2 | Undo stack for frame-state changes; bulk ops with progress + confirm (server-side per-chunk bulk would help, see open questions) | `curate.service.ts`, `candidate-grid.component.ts` | M |
| K3 | Shortcut sheet (`?`) + context hint line in workflow bar | new `shell/shortcuts-sheet.component.ts` | S |
| K4 | Prompt Director pass: `bg-canvas`, H1 signature, one category component (×4 dedupe), toggle chips not red-filled, sticky Generate bar, "index not built" empty state | `prompt-clicker/*` (after the concurrent work lands) | M |
| K5 | Responsive degrade below lg (rail sheet, grid 1-2 cols, dialogs as bottom sheets) and a contrast re-audit of the finished screens | all | M |

Dependencies: Q2 → P7/W4; P1 → P4/P8/K1; W1 → W3-W10; W2 → W3/W4/W5; backend B1 → W10.

---

## 8. Not verified / open questions

- **No runtime evidence.** I couldn't confirm the Space-key `preventDefault` blocking button activation, the settings stale-form bug, `py-0.2` producing no CSS, or the lightbox "Chunk #{{chunk_id}}" being a DB id. All are inferred from source. Verify in a browser (WSL).
- **Workflow semantics.** Per `pipeline-overview`, the caption job ends in `exported`. I assumed caption implies export, so "Done" follows "Caption". If export is a separate human step, add an "Export" step between them.
- **Board data.** Per-video kept/captioned counts aren't in `GET /api/videos` (only `chunk_count`, `export_count`). The board's "287/300" needs a backend field such as `picks_count`/`kept_count`/`captioned_count`, or a lazy per-row fetch.
- **Bulk per chunk.** Server `keep_all`/`reject_all` have no chunk filter. Per-chunk bulk is a client PATCH loop (`candidate-grid.component.ts:394-406`). Consider `?chunk_id=` on the endpoints.
- **API key UX.** Whether the server has `OPENROUTER_API_KEY` set isn't exposed. The caption step's "Using server key" radio needs a flag (e.g. in `GET /api/settings`). Key persistence policy (localStorage) is still a product decision (GAP F7).
- **Owner sign-off needed** for: Router adoption (F9), the `info` badge variant, the optional `--success-strong`/`--warning-strong` tokens, the distribution KPI colour (ink vs red), and replacing the status `<select>` with derived status plus an overflow menu.
- **Prompt Director** line numbers and some findings may be outdated by the concurrent edit. `cat.icon` values from `/api/rag/categories` were not checked (possibly emoji).
- **Chunk label "Chunk #{{ c.chunk_id }}"** in the lightbox: not verified against real data.
