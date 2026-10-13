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
| `GET /api/videos/{id}/curation-300` | - | `{video, chunks, scene_stats (=chunks), picks, runners, candidates (=picks+runners), stats, distribution}` |
| `GET /api/videos/{id}/candidates` | - | `{frames, candidates (=frames), picks, runners, stats, distribution, scene_stats}` |
| `GET /api/chunks/{chunk_id}/candidates` | - | `{frames}` (only `preview_url`/`full_url` added) |
| `POST /api/videos/{id}/swap-frame` | `{deselected_frame_id}` or `{drop_frame_id, pick_frame_id?}` | `{status, swapped_in:{id}, swapped_out:{id}, dropped, picks, runners, stats, distribution}` (picks/runners rows here lack `is_pick/is_captioned/time_s/thumb_url`) |
| `PATCH /api/picks/{frame_id}` | `{state?, role?, crop?}` | `{pick}` |
| `POST /api/videos/{id}/candidates/{frame_id}/state` | `{state}` | `{status, frame_id, state}`; inserts a pick row with `role='candidate'`, `rank=999` if none exists |
| `POST /api/videos/{id}/curate/keep_all` / `reject_all` | - | `{status, video_id}` sets state of all `role='pick'` rows (server-side bulk; no chunk filter) |
| `GET /api/frames/{id}/preview` (webp) / `/full` (png) / `GET /api/videos/{id}/frames/{fid}/thumb` | - | images |

Frame row (candidates / curation-300): `{id, video_id, chunk_id, t, time_s, metrics, zs_scores, role, rank, state, crop, preview_url, thumb_url, full_url, is_pick, is_captioned}` plus per endpoint: curation-300 -> `chunk_pos, budget_pct, caption_model, preview_path, full_path`; candidates -> `chunk_position, chunk_start, chunk_end, chunk_rule, chunk_tags, chunk_notes, chunk_caption_model`. `is_captioned` = frame has an `exports` row. `state`: `keep|reject|star|candidate`; `role`: `pick|runner_up`.

`distribution`/`stats` = `{multiple_pct, one_person_pct, tools_equipment_pct, face_visible_pct, total_frames}` (computed from `zs_scores`).

## Captioning, export, dataset, settings

| Method + path | Body | Response |
| --- | --- | --- |
| `POST /api/videos/{id}/caption-openrouter` | `OpenRouterCaptionRequest` | `{job_id, status}` |
| `POST /api/openrouter/batch_caption` | same fields + required `video_id`; `trigger_word` maps to `trigger` | same (400 if no `video_id`) |
| `GET /api/openrouter/models` | - | `{models:[{id,name,recommended}]}` |
| `POST /api/export` | `{video_id?, trigger_word?}` | with id: `{exported, stubs_written, captions_kept, per_video_dir, merged_dir}`; without: `{exported, videos_processed}` over statuses `curating, curating_300, chunked` with no queued/running job (`exported` videos are skipped). The exporter never overwrites a VLM caption; it writes tag stubs only for uncaptioned frames, marked by a `.json` sidecar with `caption_source:"stub"` (legacy sidecars without the field are stubs too), which the caption job replaces |
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
VideoStatus = 'new'|'chunking'|'chunked'|'extracting'|'extracting_750'|'curating'|'curating_300'|'captioning'|'exported'|'skipped'|'ignored'
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
| `POST /api/rag/retrieve` | `{selected_tags:{cat:[id]}, user_description, top_k=4}` | `{results:[...]}` hybrid tag + dense retrieval over indexed captions (`[]` if index empty) |
| `POST /api/rag/generate` | `{selected_tags, user_description, character?, top_k=4, model, trigger, local_url?}` | `{..., archetypes:{a,b,c:{name,prompt,token_count,warnings,is_valid}}, rag_context:[{id,stem,score,matched_tags,img_rel_path,full_caption}]}` (top-level keys before `archetypes` not verified); `local_url` switches to an Ollama-style endpoint, else OpenRouter |
| `GET /api/rag/preview/{caption_id}` | - | PNG; 404 if the RAG index (`~/.cache/captioning_pipeline/rag_index.sqlite`) isn't built (`scripts/index_rag.py`) |

## Static serving (changed)

`api.py` serves the Angular build: `src/web/dist/browser` (or `src/web/dist`) if it contains `index.html`, else falls back to `src/web`. It is mounted at `/static` **and** a catch-all `GET /{full_path:path}` returns real files (contained in the web root; `..` cannot escape) or `index.html` (SPA fallback). Unknown `GET /api/...` returns a JSON 404. Build with `<base href="/">`.

## Remaining quirks (verified in current code)

1. (fixed) Export-all now includes `curating_300`; see the export row above.
2. **Mixed row shapes**: `swap-frame` rows and `chunks` from `PUT` lack the extra fields (`is_pick`, `is_captioned`, caption stats). After swap/save, merge or refetch rather than replacing state wholesale.
3. **Chunk naming differs per endpoint** (`chunk_pos` in curation-300 vs `chunk_position`/`chunk_start/...` in candidates): prefer curation-300 + its `chunks` joined by `chunk_id`, normalise in one mapper.
4. **`PATCH /api/picks` doesn't rebalance**; use `swap-frame` for atomic swaps.
5. **Settings**: `PUT /api/settings` accepts any keys unvalidated; validate ranges client-side (no `NaN`).
6. **API key**: `api_key` in the caption body is optional; the job falls back to the key stored in user settings, then `OPENROUTER_API_KEY`. Never log or URL-embed it, and never return it from an endpoint.
7. **Statuses**: backend emits all `VideoStatus` values; the UI must style/select all of them. Reconcile may rewrite statuses (`exported` when every keep/star frame has an export (rejected picks don't count), `curating_300` when picks exist, `chunked` when frames/chunks exist, `new` otherwise) except `skipped/ignored/broken`.

Fixed upstream (no longer a problem): `StreamingResponse` import (SSE works).
Verify in the Angular code rather than assume: whether `distribution-bar` maps `one_person_pct/tools_equipment_pct/face_visible_pct` (legacy JS expected `solo_pct/tools_pct/face_pct`).

## Typing and testing

One model file per resource in `src/web/app/core/models/` (existing: candidate, chunk, dataset, job, prompt-rag, settings, video); map backend shapes there. Backend tests: `tests/test_curation_api.py`, `test_rag.py`, `test_rag_api.py`, `test_openrouter.py`, `test_phase_a.py` (temp DB + `client` fixture pattern for API tests) (fixture-video pattern; never mutate library videos).
