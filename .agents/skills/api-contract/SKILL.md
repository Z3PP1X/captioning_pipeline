---
name: api-contract
description: The REST + SSE contract between the FastAPI backend (src/pipeline/api.py) and the Angular web frontend - endpoints, request/response shapes, SSE job events, media URLs, RAG prompt endpoints, aliases, and verified remaining quirks. Use when writing or changing the Angular data layer (src/web/app/core/services, models), mocking the API in tests, or modifying any /api route.
---

# API contract (frontend <-> backend)

Source of truth: `src/pipeline/api.py`. JSON over same origin (CORS only for the `ng serve` origin `http://localhost:4200`/`127.0.0.1:4200`, no credentials; in dev `proxy.conf.json` forwards `/api` and `/static` to :8000). Servers bind `127.0.0.1`. Errors: non-2xx `{"detail": ...}` where `detail` is a string or, for the 409 re-chunk guard, an object; `ApiService` throws `ApiError {status, detail, message}`.

JSON columns are decoded by `db.py` (`tags`, `metrics`, `zs_scores`, `crop`, `watermark_rect`, `letterbox`). Video ids are strings; frame/chunk/job ids are integers.

## Videos

| Method + path | Body / query | Response |
| --- | --- | --- |
| `GET /api/videos` | `?status=&search=` | `{videos, total}`; rows add `chunk_count`, `export_count`. Status filter aliases: `curating` also matches `curating_300`; `extracting` also matches `extracting_750` (the reverse values alias too) |
| `POST /api/videos/scan` (alias `/rescan`) | - | `{scanned, sync_stats}`; runs `reconcile_captions_and_state` first, then the directory scan |
| `POST /api/videos/sync-captions` | - | `{status:"ok", sync_stats}` reconcile only |
| `GET /api/videos/next` | - | `{video \| null}` first `new`, else first `chunked/extracting` |
| `GET /api/videos/{id}` / `PATCH` | `{status?, watermark_rect?, letterbox?}` | `{video}` |
| `GET /api/videos/{id}/stream` | `Range` | 206 video |
| `GET /api/videos/{id}/filmstrip` | - | `{thumbnails:[{url,..}], cuts:number[]}` |

`sync_stats = {captions_found_on_disk, exports_inserted_or_updated, videos_updated, videos_healed:[{id,name,from_status,to_status,picks,exports}]}`.

## Chunks (a.k.a. scenes)

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/videos/{id}/chunks` (alias `GET .../scenes` -> `{scenes}`) | - | `{chunks: Chunk[]}` with caption stats |
| `PUT /api/videos/{id}/chunks` | `{chunks: ChunkItem[], confirm_destructive?=false}` (1..15, else 400) | `{chunks}` (raw rows, no caption stats), budgets normalised to 100 (1 decimal), `k>=5`. Upsert by `id`; **409** `{detail:{message, requires_confirm:true, affected_chunks:[{id,position,picks,exports}]}}` when a removed chunk holds picks/exports and `confirm_destructive` is false |
| `PATCH /api/chunks/{chunk_id}` | `{caption_model?, budget_pct?, rule?, tags?, notes?}` | `{chunk}` - edits one chunk without replacing all |
| `POST /api/videos/{id}/chunks/suggest` | `{confirm_destructive?=false}` (other fields ignored) | `{suggested: Chunk[]}` (balanced, max 15, persisted). Replaces all chunks, so same **409** as `PUT` when picks exist |

```ts
Chunk = { id, video_id, position, start_s, end_s, k, budget_pct, caption_model: string /* default 'google/gemma-4-31b-it' */,
          rule: 'person'|'equipment'|'both'|'either'|'none', tags: string[], notes, dirty,
          // only from GET chunks/scenes and curation-300 .chunks/scene_stats:
          picks_count, exports_count, captioned_count, total_frames, is_fully_captioned, caption_progress_pct /*0..100*/ }
```
`ChunkItem` = Chunk fields the client sends (`caption_model` optional, falls back to the default). `PUT` upserts by `id`: chunks with an unknown/missing `id` are inserted, chunks missing from the list are deleted (cascades to their frames, picks and exports, hence the 409 guard). A chunk whose `start_s`/`end_s` changed keeps its frames and gets `dirty=1`. Saving does not demote a curated/exported video to `chunked`. Caption stats add `approved_count` (keep/star); `is_fully_captioned`/`caption_progress_pct` are computed against it, `picks_count` still counts `role='pick'`.

## Extraction, curation

| Method + path | Body | Response |
| --- | --- | --- |
| `POST /api/videos/{id}/extract-750` (alias `extract_750`) | `{}` | `{job_id, status:"queued"}` |
| `POST /api/videos/batch-auto-extract` | `{video_ids?: string[], only_unextracted?: bool, options?: dict}` | `{job_id, status:"queued", video_count: int}`; sequential overnight batch extraction across videos with failure isolation |
| `POST /api/chunks/{chunk_id}/extract` (alias `/api/videos/{video_id}/chunks/{chunk_id}/extract`) | `{k?: int, budget_pct?: float}` | `{job_id, status:"queued", chunk_id, video_id}`; multi-step targeted chunk extraction: extracts frames strictly within `[chunk.start_s, chunk.end_s]`, leaves other chunks untouched |
| `GET /api/videos/{id}/curation-300` | - | `{video, chunks, scene_stats (=chunks), picks, runners, candidates (=picks+runners), stats, distribution}` |
| `GET /api/videos/{id}/candidates` | - | `{frames, candidates (=frames), picks, runners, stats, distribution, scene_stats}` |
| `GET /api/chunks/{chunk_id}/candidates` | - | `{frames}` with all unified frame fields |
| `POST /api/videos/{id}/swap-frame` | `{deselected_frame_id}` or `{drop_frame_id, pick_frame_id?}` | `{status, swapped_in:{id}, swapped_out:{id}, dropped, picks, runners, candidates, stats, distribution}`. Validates chunk containment (400) and video ID (400/404); auto-replacement picks best runner via $\operatorname{argmax}_j (\operatorname{sim}(\text{drop}, j) \cdot q_j)$. Promoted runner is re-decoded at full native resolution (`native=1`). All rows include `is_pick`, `is_captioned`, `time_s`, `thumb_url`, `native`, and unified chunk fields |
| `PATCH /api/picks/{frame_id}` | `{state?, role?, crop?}` | `{pick}` |
| `POST /api/videos/{id}/candidates/{frame_id}/state` | `{state}` | `{status, frame_id, state}`; validates state in `('keep', 'reject', 'star', 'candidate', null)` (400 if invalid); inserts fallback pick row with `role='runner_up'`, `rank=999`; re-decodes native full-res image when state in `('keep', 'star')` |
| `POST /api/videos/{id}/curate/keep_all` / `reject_all` | - | `{status, video_id}` sets state of all `role='pick'` rows (server-side bulk; no chunk filter) |
| `GET /api/frames/{id}/preview` (webp) / `/full` (png) / `GET /api/videos/{id}/frames/{fid}/thumb` | - | images |

Frame row (candidates / curation-300 / swap / chunk candidates): unified across all endpoints: `{id, video_id, chunk_id, t, time_s, metrics, zs_scores, role, rank, state, crop, preview_url, thumb_url, full_url, is_pick, is_captioned, native, chunk_pos, chunk_position, chunk_start, chunk_end, budget_pct, caption_model, target_person_sim, target_person_matched, face_count, faces}`. `is_captioned` = frame has an `exports` row. `native` = 1 if decoded at native camera resolution, 0 if downsampled runner. `state`: `keep|reject|star|candidate`; `role`: `pick|runner_up`.

`distribution`/`stats` = `{multiple_pct, one_person_pct, tools_equipment_pct, face_visible_pct, target_person_pct, total_frames}` (computed from `zs_scores` and target face matches).

## Persons & Face recognition (character prioritization)

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/persons` | - | `{persons: Person[]}` |
| `POST /api/persons` | `{name: string}` | `{person: Person}` |
| `GET /api/persons/{id}` | - | `{person: Person}` |
| `PATCH /api/persons/{id}` | `{name?: string}` | `{person: Person}` |
| `DELETE /api/persons/{id}` | - | `{status: "deleted"}` |
| `GET /api/persons/{id}/crop` | - | Face crop image (JPEG) |
| `POST /api/videos/{id}/target-person` | `{person_id: number \| null}` | `{status, video_id, target_person_id, target_person_name}` |
| `GET /api/videos/{id}/persons` | - | `{video_id, positive: Person[], negative: Person[]}` multi-person tags |
| `POST /api/videos/{id}/persons/{person_id}` | `{role: 'positive'\|'negative'\|'none'}` | `{video_id, persons, re_evaluation}`; assigns positive/negative role or removes person, re-evaluates face detection across extracted frames |
| `POST /api/videos/{id}/re-evaluate-faces` | - | `{updated_frames, pos_matches, neg_matches, excluded, demoted_picks}`; rapid re-evaluation on disk cache frames without raw video re-decoding |
| `POST /api/videos/{id}/dedup-similarity` (alias `/postprocess-similarity`) | `{dup_similarity?=0.950, target_count?=180}` | `{before_picks, after_picks, demoted_count, max_similarity, mean_similarity}`; score-optimal dedup (keeps higher composite score) and budget trimming (150-200 frames) |
| `POST /api/videos/{id}/tag-face-from-frame` | `{frame_id, person_id?, person_name?, face_index?}` | `{status, person, re_scored_count, frame_id}`. Merges 128-dim running centroid, caches avatar crop, instantly re-scores and updates all video candidate frames in SQLite and broadcasts SSE update |
| `GET /api/frames/{frame_id}/faces` | - | `{frame_id, face_count, faces: [{bbox, confidence, similarity, matched}]}` |
| `POST /api/videos/{id}/auto-extract` | `{target_person_id?: number}` | `{job_id, status: "queued"}` autonomous chunking + candidate sampling + face match + score prioritization + MMR reduction |

## Autopilot (autonomous extraction, priority review & refinement)

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/videos/{id}/autopilot` | - | `{stage, video_id, priorities, chunks:[{id,position,start_s,end_s,priority,budget_pct}], target_person, report?}` |
| `POST /api/videos/{id}/autopilot/start` | `{exclude_ranges?, target_person_id?, target_curated?, review_chunks?}` | `_autopilot_payload`: starts `autopilot_plan` job |
| `POST /api/videos/{id}/autopilot/priorities` | `{priorities: {chunk_id: "skip"\|"low"\|"normal"\|"high"}}` | Updates scene priorities and dynamically recalibrates scene frame budgets |
| `POST /api/videos/{id}/autopilot/approve` | `{priorities?}` | Approves plan and launches extraction + refinement cascade |
| `POST /api/videos/{id}/autopilot/refine` | `{dup_similarity?, dup_phash?, person_focus?, purge_off_target?}` | Runs cross-set deduplication, off-target replacement, and quota repair |
| `POST /api/videos/{id}/autopilot/reset` | - | Resets autopilot state back to `idle` |
| `POST /api/autopilot/batch` | `{video_ids: string[], ...config}` | `{queued:[video_id], skipped:[video_id]}` batch autopilot across multiple videos |



## Captioning, export, dataset, settings

| Method + path | Body | Response |
| --- | --- | --- |
| `POST /api/videos/{id}/caption-openrouter` | `OpenRouterCaptionRequest` | `{job_id, status}`; **409 Conflict** if an active job already exists for this video. Captions run on dedicated 4-worker network executor |
| `POST /api/openrouter/batch_caption` | same fields + required `video_id`; `trigger_word` maps to `trigger` | same (400 if no `video_id`, 409 if video is busy) |
| `GET /api/openrouter/models` | - | `{models:[{id,name,recommended}]}` |
| `POST /api/export` | `{video_id?, trigger_word?}` | with id: `{exported, stubs_written, captions_kept, per_video_dir, merged_dir}`; without: `{exported, videos_processed}` over statuses `curating, curating_300, chunked` with no queued/running job (`exported` videos are skipped). The exporter never overwrites a VLM caption; it writes tag stubs only for uncaptioned frames, marked by a `.json` sidecar with `caption_source:"stub"` (legacy sidecars without the field are stubs too), which the caption job replaces. Standardized `render_export_image` shared with captioner; `manifest.jsonl` deduped |
| `GET /api/dataset` | - | `{total_exports, per_video:[{id,name,slug,count}], finished_dir}` |
| `GET/PUT /api/settings` | partial object | `{settings}` (defaults deep-merged). `openrouter_api_key` is never returned; the response has `openrouter_api_key_set: bool`. A `PUT` with an empty key keeps the stored one |
| `GET /api/jobs?video_id=` / `DELETE /api/jobs/{id}` | - | `{jobs}` (last 20) / `{cancelled}`: `false` for finished/unknown jobs. A queued job is cancelled at once; a running job emits `job_progress` step `Cancelling...` and ends as `cancelled` at its next checkpoint without touching frames/picks |
| `GET /api/events` | - | SSE (below) |

```ts
OpenRouterCaptionRequest { api_key?, model='google/gemma-4-31b-it', fallback_layer2='deepseek/deepseek-v4.1-flash',
  fallback_layer3='x-ai/grok-4.7', reasoning_effort='none' /* any effort raises max_tokens to >=1200 */,
  use_chunk_models=true /* layer-1 model = chunk.caption_model when set, else `model` */,
  mode='concept'|'character'|'style', trigger='restrained_elegance', class_noun='woman',
  max_tokens=600 /* no 300 cap; cut-off replies (finish_reason length / no closing sentence) are retried, then the frame fails */,
  temperature=0.80, top_p=0.90 }
VideoStatus = 'new'|'chunking'|'chunked'|'extracting'|'extracting_750'|'curating'|'curating_300'|'captioning'|'exported'|'skipped'|'ignored'|'broken'
```
Not used by the UI: `POST /api/jobs`, `/auto-process`, `/batch-auto-process`.

### SSE `/api/events`

`data: {"type","data"}` per message; first `{"type":"connected"}`. `job_queued|job_started` `{job_id,video_id,video_name,chunk_id}`; `job_progress` + `{progress:0..1, step}`; `job_finished` + `{picks_count?, runners_count?, stats?}` (caption job: `{exported, failed}`; with failures the job row is `finished` and `error` holds a summary); `job_error` `{job_id,video_id,video_name,error}`; `job_cancelled` `{job_id, video_id, ...}`. A `: keepalive` comment is sent every 15 s; a slow client loses its oldest events (queue max 500). On error/cancel the server settles a video left in `extracting*`/`captioning`: `exported` if every keep/star frame has an export, else `curating_300` with picks, `chunked` with chunks, else `new`. The caption job ends `exported` only when every approved frame is captioned.

## RAG prompt director (Krea prompt tool; frontend: `prompt-rag.service.ts`)

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/rag/categories` | - | `{categories}` taxonomy: `{equipment\|surroundings\|position\|camera_lighting: {title, icon, description, groups:{name:[{id,label,keywords}]}}}` |
| `GET /api/rag/presets` | - | `{presets:[{id,title,description,tags:{category:[tagId]}}]}` |
| `GET /api/rag/characters` | - | `{characters:{sara\|lene...: {id,name,age,avatar,summary,description,tag_tokens}}}` |
| `GET /api/rag/status` | - | `{running: bool, ready: bool, total_captions: int, indexed_captions: int, progress: float, error: str \| null, openrouter_key_configured: bool}` |
| `POST /api/rag/index` | `{limit?: int}` | `{running, ready, total_captions, indexed_captions, progress}` starts background build; **409** if build already running |
| `POST /api/rag/retrieve` | `{selected_tags:{cat:[id]}, user_description, top_k=4}` | `{results:[...], index_ready: bool}` hybrid tag + dense retrieval over indexed captions (`results: []` if index empty/not ready) |
| `POST /api/rag/generate` | `{selected_tags, user_description, character?, top_k=4, model, trigger, local_url?, api_key?}` | `{archetypes:{a,b,c:{name,prompt,token_count,warnings,is_valid}}, rag_context:[...], grounded: bool, notices: string[]}`; **400** if OpenRouter key missing and not local; **502** (`RagUpstreamError`) if upstream API call fails |
| `GET /api/rag/preview/{caption_id}` | - | PNG; 404 if the RAG index (`~/.cache/captioning_pipeline/rag_index.sqlite`) isn't built |

## Static serving (changed)

`api.py` serves the Angular build: `src/web/dist/browser` (or `src/web/dist`) if it contains `index.html`, else falls back to `src/web`. It is mounted at `/static` **and** a catch-all `GET /{full_path:path}` returns real files (contained in the web root; `..` cannot escape) or `index.html` (SPA fallback). Unknown `GET /api/*` routes return a JSON 404 (`{"detail": "Unknown API route: /api/..."}`) rather than falling through to `index.html`. Build with `<base href="/">`.

## Remaining quirks (verified in current code)

1. (fixed in Phase A) Export-all now includes `curating_300`; see the export row above.
2. (fixed in Phase C) Row shapes unified: `swap-frame`, `curation-300`, `candidates`, and `chunks/{id}/candidates` now all include `is_pick`, `is_captioned`, `time_s`, `thumb_url`, `native`, and unified `chunk_pos`/`chunk_position`/`chunk_start`/`chunk_end`. `PUT /chunks` returns `scene_stats`.
3. (fixed in Phase C) Per-video mutex active: concurrent extraction or caption jobs on the same video return HTTP 409 Conflict (`JobConflictError`). Executors segregated: decode executor (max 2 workers) vs caption executor (max 4 workers).
4. **`PATCH /api/picks` doesn't rebalance**; use `swap-frame` for atomic swaps.
5. **Settings**: `PUT /api/settings` accepts any keys unvalidated; validate ranges client-side (no `NaN`).
6. **API key**: `api_key` in the caption body is optional; the job falls back to the key stored in user settings, then `OPENROUTER_API_KEY`. Never log or URL-embed it, and never return it from an endpoint.
7. **Statuses**: backend emits all `VideoStatus` values; the UI must style/select all of them. Reconcile may rewrite statuses (`exported` when every keep/star frame has an export (rejected picks don't count), `curating_300` when picks exist, `chunked` when frames/chunks exist, `new` otherwise) except `skipped/ignored/broken`.

Fixed upstream (no longer a problem): `StreamingResponse` import (SSE works).
Verify in the Angular code rather than assume: whether `distribution-bar` maps `one_person_pct/tools_equipment_pct/face_visible_pct` (legacy JS expected `solo_pct/tools_pct/face_pct`).

## Typing and testing

One model file per resource in `src/web/app/core/models/` (existing: candidate, chunk, dataset, job, prompt-rag, settings, video); map backend shapes there. Backend tests: `tests/test_curation_api.py`, `test_rag.py`, `test_rag_api.py`, `test_openrouter.py`, `test_phase_a.py` (temp DB + `client` fixture pattern for API tests) (fixture-video pattern; never mutate library videos).
