# Captioning Studio adaptations

How the mim26 rules apply to this app. Where this file and a generic reference disagree, **this file wins for Studio screens**. Items marked *(proposal)* are design decisions made during skill setup, not yet reviewed by the product owner. The Angular UI now exists (`src/web/app`); where it already diverges, see "Compliance gaps" at the end.

## Language and copy

- UI copy is **English**, sentence case. Ignore the German examples in the generic references ("Schritt x von n", Impressum, translation service) and the German text in `src/web/shared/components/**`.
- `lang="en"` on `<html>`. Filenames, tags and timecodes are data: never uppercase or translate them.

## Layout: desktop-first workstation *(proposal)*

The Studio is a three-pane tool (queue, player + timeline, chunk editor/curation grid) used with a keyboard and a large screen; a 360 px layout of a frame timeline is not realistic. Keep the mim26 tokens, type, radii, focus and contrast rules unchanged, but:

- Primary target `xl` (>= 1280 px); must remain usable at `lg` (1024); below `lg` degrade gracefully: queue becomes a left `hlm-sheet` drawer, controls wrap, the curation grid drops to 1-2 columns, dialogs become bottom sheets, touch targets stay >= 44 px (helm `h-11 md:h-9` already does this).
- App shell per layout.md: sticky header `h-14 md:h-16` (logo, view tabs with `aria-current`, running-job pills, Next video, Settings); queue as permanent left rail from `lg`; `main#main-content` + skip link. Page H1 = view name in signature style (**Chunking studio**, **Curation grid**, **Finished dataset**, rendered uppercase by the style). The selected video's name is an H2/H3 in sentence case (a filename must not become an uppercase H1).
- The studio workspace may use the full width (`max-w-screen-2xl`) instead of `max-w-screen-xl`; the timeline keeps its own max width. Page background `bg-canvas`, panels are white `rounded-xl` cards with `border` (not heavy shadows).
- No hover-only affordances: the timeline hover preview is a bonus; tap/click seeking and the keyboard remain complete. Every shortcut has a visible button/tooltip equivalent (use a `kbd` primitive to show the key).

## Surfaces

- **Theme (DECIDED: mim26, light-first)**: the Studio uses the mim26 design system as specified; the default theme is **light**. `src/web/styles.css` has the light `:root` (the mim26 tokens in `colors.md`) plus a custom `.dark` block that is *not* part of mim26 (canvas `#0D0F14`, card `#1B1F2A`, muted-foreground `#9EA3AB`, ...). Required changes: remove `class="dark"` from `<html>` in `index.html` so the app starts light; remove the header theme toggle and the `.dark` block (preferred, since no dark values are defined by the design system), or keep them only as an explicitly undocumented-by-design opt-in that is not the default. Do not add dark-specific styling to new components.
- App chrome is light (`bg-canvas`, `bg-background` cards). **Media surfaces are ink**: video stage, timeline track, lightbox image area use `bg-secondary text-secondary-foreground` (never `#000`). Small text on ink uses white at >= 70% opacity.
- Lightbox: dialog `rounded-xl`, image area ink, side panel white.

## Single primary action per view *(proposal)*

| View | Primary (`bg-primary`) | Everything else |
| --- | --- | --- |
| Studio | **Extract & score 750** | Add chunk, Save, Auto-suggest = `outline`/`secondary` |
| Curate | **Caption with VLM** | Keep all / Deselect all / Export = `outline`; Deselect all also needs a confirm dialog |
| Dataset | **Export all curated** | Refresh = `outline` |
| Header | none (Next video = `secondary`) | Settings = `ghost` icon button |

Destructive actions (Deselect all, deleting a chunk with unsaved edits, cancelling a running job) use `variant="destructive"` + `alert-dialog`.

## Status, role and state mapping (never by colour alone: icon + text)

Badge variants available: `success | warning | danger | muted` (+ `outline`). Add an `info` variant to `hlm-badge` (token `--info`, foreground text) if "in review" needs separating from "running"; document it in `styling`.

| Video status | Variant | Icon (lucide) | Label |
| --- | --- | --- | --- |
| `new` | muted | circle-dashed | New |
| `chunking`, `chunked` | muted | scissors | Chunked |
| `extracting`, `extracting_750` (or active job) | warning | loader (spin, `motion-safe`) | Extracting |
| `curating`, `curating_300` | info (or outline) | list-checks | Curating |
| `captioning` | warning | captions | Captioning |
| `exported` | success | check | Exported |
| `skipped`, `ignored` | muted | ban | Skipped |
| `broken` | danger | triangle-alert | broken_reason or "Broken" |

| Pick/role/state | Treatment |
| --- | --- |
| Role badge `PICK #n` | `bg-primary text-primary-foreground` small pill on the thumbnail (4.70:1 ok) |
| Role badge `RUNNER #n` | `outline`/`muted` pill |
| `keep` | card `border-border`, check icon-badge; selected ring `ring-ring` only for the focused/selected card |
| `star` | warning badge + star icon |
| `reject` | `opacity-60`, destructive x icon-badge + "Rejected" text (do not rely on opacity alone) |

## Metrics and distribution

- Metric pills (sharpness, quality, Multi, Solo, Tool/Eq, Face >= 50%) are **neutral** `muted` pills with a distinct lucide icon + text (`users`, `user`, `wrench`, `scan-face`, `focus`, `sparkles`). No per-metric hues (legacy used indigo/sky/amber/pink - off palette).
- Distribution meters use `progress` (fill `bg-primary`). Outside the target range: fill `bg-warning`, plus a warning icon and text ("below 45-55% target") - the target text is always visible, not only a tooltip. Numbers in `tabular-nums`; the big "n / 300" is a KPI (display style).
- Budget badge: valid = `success` + check ("100%"), invalid = `danger` + triangle-alert ("Total 94% - must be 100%").
- Dataset metric cards: KPI numbers in display style, label in caption style.

## Timeline *(proposal)*

On the ink track: filmstrip thumbs full-bleed; scene cuts `bg-background/70` 2 px lines; chunk bands `bg-primary/60` with white `#n` labels (UI boundary vs ink is 3.70:1); playhead `bg-background` 2 px with a round handle; In/Out range `bg-warning/40` with `border-warning` (bright on ink). Hover preview is a white card (`shadow-lg`, `rounded-lg`) with the thumbnail and a `tabular-nums` timecode. Timecodes everywhere use `tabular-nums`; a mono font is **not** introduced (mim26 allows Helvetica/Arial/Noto only) - use `tabular-nums` for alignment.

## Controls and dialogs

- Player buttons: `icon` size buttons with `sr-only` labels + tooltip with the shortcut; groups (jump -60/-10/+10/+60, frame step) use `variant="outline"` size `sm`.
- Selects: `hlm-select` or `native-select` (native is fine for speed/status filters). Snap Cuts = `switch` (generate it). Chunk start/end time inputs are text inputs using the same timestamp parser as the jump box.
- Settings and Caption dialogs: centered `max-w-lg` (Caption `max-w-2xl`) from `md`, bottom sheet below. Labels via `hlm-field`; the long explanatory "info box" of the legacy caption modal shrinks to a short helper text + a collapsible "How the cascade works". Model options come from the API.
- Replace every `alert()`/`confirm()` with toast (info/success/error) or `alert-dialog` (destructive).
- Empty states use the `empty` primitive with an icon and one clear next action ("No chunks yet - mark In/Out or use Auto-suggest").
- Loading: `skeleton` for the video list and grid, `spinner` inline in buttons while a request is pending (never change the label text only).

## Compliance gaps in the implemented Angular UI (`src/web/app`, verified)

| Rule | Gap | Where |
| --- | --- | --- |
| No emoji, lucide icons | Emoji used as icons/labels in nearly every view | `shell/header.component.ts` (tab labels, logo), `shell/video-queue.component.ts:36,127-130`, `studio/studio-view.component.ts:35-38`, `studio/video-player.component.ts:261`, `curate/distribution-bar.component.ts:12-57`, `curate/candidate-grid.component.ts:77,87`, `modals/openrouter-modal.component.ts:30,150`, `prompt-clicker/prompt-clicker-view.component.ts:134-193` |
| Tokens only, no raw palette | `bg-black/*`, `text-white`, `bg-black` (modal scrims, thumbnails, player): `modals/*.component.ts`, `curate/candidate-grid.component.ts`, `studio/video-player.component.ts`, `prompt-clicker/...`; `bg-yellow-400/80`, `ring-white`, `bg-black/60`: `studio/timeline.component.ts:48+`; `amber-500` status tint: `shell/video-queue.component.ts:198`. Use `bg-foreground/*`, `bg-secondary`, `text-secondary-foreground`, `bg-warning`. |
| No hex in code | `ctx.strokeStyle = '#D73816'` in `studio/video-player.component.ts:465` - read `--primary` via `getComputedStyle` instead |
| Status = semantic badge + icon + text | Hand-built `rounded border ... text-xs` pills with colour classes instead of `hlmBadge` variants (`shell/video-queue.component.ts` status classes, `studio/chunks-panel.component.ts` captioned badges, budget badge uses `text-success`/`text-warning` as text colour - fails the contrast note "never as text on white") |
| No `alert()` | `core/services/chunk.service.ts:53`, `studio/video-player.component.ts:373`, `modals/openrouter-modal.component.ts:223` |
| Spartan primitives, no hand-rolled | Modals are custom `fixed inset-0` overlays (`modals/settings`, `lightbox`, `openrouter`) instead of `hlm-dialog`/`sheet` + `alert-dialog`; inputs/selects/checkbox are raw elements with `ngModel` (9 files) instead of helm `input`/`select`/`checkbox`/`switch`; only `HlmButton`, `HlmBadge`, `HlmTooltip` are imported (22/8/2 uses); toasts are a custom container, not `hlm-toaster`; no `progress`, `skeleton`, `empty`, `table` helm usage |
| Forms: Signal Forms | `FormsModule`/`ngModel` throughout (`styling` rule 6) |
| Signature type only for page titles | `studio-view` renders the **video filename** as an uppercase `h1` (`studio/studio-view.component.ts:29`); `dataset-view` uses the signature style on an `h2`; `prompt-clicker` h1 uses `font-sans font-black`. Page H1 = view name; filename = sentence-case H2/H3. |
| No extra fonts | `font-mono` for timecodes in 8 files (should be `tabular-nums`); `index.html` still loads Google Fonts (Plus Jakarta Sans, JetBrains Mono); 82 arbitrary `text-[Npx]` sizes instead of the type scale |
| Touch targets / mobile | Fixed pixel grids (e.g. `grid-cols-[135px_95px_...]` in `studio/chunks-panel.component.ts:160`), `h-screen overflow-hidden` shell, no drawer for the queue below `lg`, `user-select: none` on `body` (blocks copying filenames/captions) |
| A11y | Title-attribute tooltips instead of `hlmTooltip` (only 2 uses); icon-only buttons without `sr-only` text not audited; no `main#main-content`/skip link, no `aria-current` on tabs (not verified exhaustively) |
| Language | UI copy is English (OK) |
| Drift hazard | `.agents/skills/{design-system,styling}` is an upstream duplicate of these skills; edits are not mirrored |
