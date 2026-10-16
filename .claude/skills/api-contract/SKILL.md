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
| `GET /api/videos/{id}/filmstrip` | - | `{thumbnails:[{t,file,url}], cuts:number[], transitions:{start,end,kind:'fade'}[], cut_detector:'content'\|'legacy', cut_config, analysed_frames?}`. Cuts are frame-accurate with the default `cut_detector` 'content' (Phase F2, below); `cut_detector` says what actually ran (a failed content pass falls back to `legacy`, keyframe-only, `transitions: []`) |

`sync_stats = {captions_found_on_disk, exports_inserted_or_updated, captions_imported, videos_updated, videos_healed:[{id,name,from_status,to_status,picks,approved,exports}]}`. `captions_imported` counts `.txt` files newly adopted into the `captions` table (idempotent: 0 on a re-run).

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
| `POST /api/videos/batch-auto-extract` | `{video_ids?: string[], only_unextracted?: bool /* default true: status new\|chunked */, target_curated?: int}` | `{job_id, status:"queued"}` (job `batch_auto_extract`, `video_id: null`); 400 when no video is eligible. Sequential overnight batch with per-video failure isolation |
| `POST /api/chunks/{chunk_id}/extract` (alias `/api/videos/{video_id}/chunks/{chunk_id}/extract`) | `{k?: int, budget_pct?: float}` | `{job_id, status:"queued", chunk_id, video_id}`; extracts frames strictly within `[chunk.start_s, chunk.end_s]`, leaves other chunks untouched |
| `GET /api/videos/{id}/curation-300` | - | `{video, chunks, scene_stats (=chunks), picks, runners, candidates (=picks+runners), stats, distribution}` |
| `GET /api/videos/{id}/candidates` | - | `{frames, candidates (=frames), picks, runners, stats, distribution, scene_stats}` |
| `GET /api/chunks/{chunk_id}/candidates` | - | `{frames}` with all unified frame fields |
| `POST /api/videos/{id}/swap-frame` | `{deselected_frame_id}` or `{drop_frame_id, pick_frame_id?}` | `{status, swapped_in:{id}, swapped_out:{id}, dropped, picks, runners, candidates, stats, distribution}`. Validates chunk containment (400) and video ID (400/404); auto-replacement picks best runner via $\operatorname{argmax}_j (\operatorname{sim}(\text{drop}, j) \cdot q_j)$. Promoted runner is re-decoded at full native resolution (`native=1`). All rows include `is_pick`, `is_captioned`, `time_s`, `thumb_url`, `native`, and unified chunk fields |
| `PATCH /api/picks/{frame_id}` | `{state?: keep\|reject\|star\|candidate, role?: pick\|runner_up, crop?, rebalance?: bool}` | `{pick}`; other values 400. `role:'runner_up', rebalance:true` on a pick runs the swap-frame logic (best replacement of the chunk is promoted) and adds `rebalanced` (the swap-frame response) |
| `POST /api/videos/{id}/candidates/{frame_id}/state` | `{state}` | `{status, frame_id, state}`; validates state in `('keep', 'reject', 'star', 'candidate', null)` (400 if invalid); inserts fallback pick row with `role='runner_up'`, `rank=999`; re-decodes native full-res image when state in `('keep', 'star')` |
| `POST /api/videos/{id}/curate/keep_all` / `reject_all` | - | `{status, video_id}` sets state of all `role='pick'` rows (server-side bulk; no chunk filter) |
| `POST /api/videos/{id}/reselect` | `{target_count?: int (1-5000), per_chunk_floor?: int, per_chunk_cap?: int, diversity?: 0..1, elasticity?: 0..1, axis_caps?: {multi_person\|solo\|equipment\|face: 0..1}, cross_dataset_dedup?: bool}` | curation-300 shape (`{video, chunks, scene_stats, picks, runners, candidates, stats, distribution}`) **plus** `reselect: ReselectReport`. Re-runs only pick vs runner_up over stored frames (Phase F, below); **409** while the video has a queued/running job, **400** for infeasible knobs (floors above target, floor > cap, no frames) or an unknown axis / share outside 0..1, **404** unknown video, **422** out-of-range values |
| `GET /api/frames/{id}/preview` (webp) / `/full` (png) / `GET /api/videos/{id}/frames/{fid}/thumb` | - | images |

Frame row (candidates / curation-300 / swap / chunk candidates): unified across all endpoints: `{id, video_id, chunk_id, t, time_s, metrics, zs_scores, role, rank, state, crop, preview_url, thumb_url, full_url, is_pick, is_captioned, native, chunk_pos, chunk_position, chunk_start, chunk_end, budget_pct, caption_model, target_person_sim, target_person_matched, face_count, faces}`. `is_captioned` = frame has an `exports` row. `native` = 1 if decoded at native camera resolution, 0 if downsampled runner. `state`: `keep|reject|star|candidate`; `role`: `pick|runner_up`.

`distribution`/`stats` = `{multiple_pct, one_person_pct, tools_equipment_pct, face_visible_pct, target_person_pct, total_frames}` (computed from `zs_scores` and target face matches).

### Phase F: sampling stats, curation log, learned weights, reselect

- **Sampling** (`extract_750`, `extract_chunk`): window-peak sampling (setting `window_peak_sampling`, default true; `window_peak_oversample`=3 sub-samples per sampling window, the sharpest/stillest is kept, so the sample count is unchanged) then dHash pre-dedup of consecutive near-identical samples (`dhash_dedup_max_distance`=4 bits, 0 = off) before metrics, faces and CLIP. Kept samples get `metrics.motion_energy` (mean grey-level frame difference). `job_finished.stats` adds `{dhash_dropped, learned_weights_active, sampling:{raw_samples, dhash_dropped, dhash_max_distance, window_peak, subsamples}}`; `extract_chunk`'s `job_finished` now carries `stats` too.
- **Curation events**: candidate state (`keep|reject|star`, `star` -> `candidate`/`null` = `unstar`; unchanged states are not logged), `swap-frame` (`swap_out` dropped + `swap_in` promoted), `PATCH /api/picks` (role `pick`/`runner_up` = `swap_in`/`swap_out`, state as above) and `curate/keep_all|reject_all` (`bulk_keep|bulk_reject`, only picks whose state changes) append rows to `curation_events(id, video_id, frame_id, chunk_id, action, features JSON, created_at)` (no foreign keys: events outlive re-extracted frames). `features` = `{sharpness, quality, persons, objects, face, composite, target_person_sim, tile_sharp, mean_luma, clipping, contrast, entropy, motion_energy?, face_count, role, state, t}` at logging time (after the action).
- **Learned weights** (`learned_weights.py`): logistic regression (numpy, L2=1, standardised features) of P(keep) on the components `sharpness, quality, persons (=max multiple/one person), objects (=max tools/equipment), face`; label 1 = `keep|star|bulk_keep|swap_in`, 0 = `reject|bulk_reject|swap_out`, the latest labelled event per frame wins, `unstar` is unlabelled. Positive raw-scale coefficients normalised to sum 1 become `weights`. With `use_learned_weights` (default false) and a fit on >= `learned_weights_min_events` (50) events, new scores get `composite = (1-b)*default + b*sum(w*component)` with `b = learned_weights_blend` (0.5) and keep the unblended value in `zs_scores.composite_base`; reselect blends the same way. Off = the default scoring path, unchanged.
- **Reselect** (`reselect.py`, review §6.3): quality-weighted facility location, greedy, on the stored CLIP embeddings (whitened per video) + `0.25*exp(-|dt|/6 s)` within a chunk. Gain = `diversity * coverage_gain / max_gain + (1 - diversity) * q` (q = per-video min-max composite); `diversity` defaults to setting `reselect_diversity` (0.5). Per-chunk quota = largest-remainder share of the target by `budget_pct`; floor `floor((1-e)*quota)`, cap `ceil((1+e)*quota)` with `e = elasticity` (setting `reselect_elasticity`, 0.2) unless `per_chunk_floor`/`per_chunk_cap` (absolute, every chunk) are given; floors are filled first, then the rest goes globally under the caps. `target_count` defaults to the current pick count. Starred and exported frames always stay picks (state kept, they count toward the target); rejected frames are never picked (stay `runner_up` with state `reject`); a candidate with raw cosine > `similarity_dedup_threshold` (0.95) to a pick is skipped. New picks get state `keep`, other non-rejected runners `candidate`; runners are ranked per chunk by final marginal gain. Promoted runners keep `native=0` until export / caption decode them (no decode in the request, about 0.2 s for 750 frames).

```ts
ReselectReport = { target_count, picks_count, shortfall, promoted: number[], demoted: number[], locked, dup_skipped,
  excluded_rejected, diversity, elasticity, learned_weights_active: boolean,
  embedder: 'clip'|'dinov2', embedder_fallback: string|null /* why DINOv2 was requested but not used */,
  axis_caps: Record<Axis, {max_share, cap /* floor(max_share*target) */, picks, pool, overflow}>,
  cross_dataset: {enabled, reference_frames, blocked, blocked_ids: number[]},
  chunks: {chunk_id, quota, floor, cap, pool, picks}[] }
// Axis = 'multi_person'|'solo'|'equipment'|'face'
LearnedWeightsStatus = { weights: Record<Component, number>|null, default_weights, coefficients /* standardised */, intercept,
  n_events /* labelled events now */, n_events_at_fit, n_positive, n_negative, accuracy /* training */, auc, fitted_at,
  fitted: boolean, min_events, can_fit: boolean, use_learned_weights: boolean, blend, active: boolean, features: Component[] }
// Component = 'sharpness'|'quality'|'persons'|'objects'|'face'
```

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/scoring/learned-weights` | - | `LearnedWeightsStatus` |
| `POST /api/scoring/learned-weights/fit` | - | `LearnedWeightsStatus` after fitting and storing (settings key `learned_weights`); **400** with fewer than `learned_weights_min_events` labelled events or only one class |

Settings (Phase F): `dhash_dedup_max_distance` (4, 0-32), `window_peak_sampling` (true), `window_peak_oversample` (3, 2-8), `use_learned_weights` (false), `learned_weights_blend` (0.5, 0-1), `learned_weights_min_events` (50, 10-100000), `reselect_diversity` (0.5, 0-1), `reselect_elasticity` (0.2, 0-1).

### Phase F2: analysis cache, content cuts, quality v2, DINOv2, axis caps, cross-dataset dedup

- **Analysis cache** (`analysis.py`, setting `reuse_analysis_cache`, default true): `<cache>/analysis/<video_id>.npz` holds every decoded candidate sample (JPEG q92 at 512 px, before the letterbox/watermark crop) with pts, Laplacian variance, motion, luma mean/std, clipping and dHash, the sampled coverage (range, interval, mode), and CLIP/DINOv2 embeddings of the cropped samples keyed by (model, crop signature). `extract_750` / `extract_chunk` decode only the parts of a chunk the cache does not cover at the requested density (window-peak re-run over the cached samples), and reuse cached CLIP embeddings; samples are JPEG round-tripped on both paths, so a cache hit is bit-identical to a fresh decode. A size/mtime change of the video file deletes the npz and the video's `frame_features`; a missing file disables caching. Table `frame_features(video_id, t PK, lap_var, motion, mean_luma, luma_std, clipping, dhash, face_count, face_summary {count, boxes, max_short_side_px, max_area_frac}, emb_refs {clip?, dinov2?: "analysis/<vid>.npz#emb_<kind>"}, gates string[], q_components {raw, norm}, updated_at)`: one row per sampled timestamp, gated ones included (no API route yet). `job_finished.stats` adds `{quality_v2, quality_gates: {reason: count}, clip_embeddings: {computed, cached}, frame_features, iqa?, selection_embedder?}` and `sampling.analysis_cache: {served, decoded, decoded_ranges, decoded_s}`.
- **Cuts** (`cuts.py`, setting `cut_detector` 'content' | 'legacy'): one PyAV pass over every frame at native rate (~96 px, swscale) computes the PySceneDetect ContentDetector score (mean |dH|,|dS|,|dV|); a cut is a frame with score >= `cut_min_content_val` (15) and >= `cut_adaptive_threshold` (3.0) x the mean of its 2+2 neighbours (AdaptiveDetector), cuts closer than `cut_min_scene_s` (0.5) keep the stronger; a dark run (mean < `cut_fade_threshold`, 12) not touching the start/end is a cut at its middle plus a `fade` transition zone (content cuts inside it are dropped). Cross-dissolves without a per-frame spike are not detected. The cut time is the first frame of the new shot. Thumbnails come from the same pass. `filmstrip.json` is rebuilt when the detector or its knobs change; files from before F2 count as `legacy`. Autochunk / `chunks/suggest` / autopilot planning read the same `cuts[]`.
- **Quality v2** (`quality.py`, setting `quality_v2`, default true; false = the previous scoring path unchanged): gates per chunk before faces/CLIP: `too_dark` (luma < 16), `blown_out` (> 245), `clipping` (> 40 %), `letterbox_black` (dark borders > 45 % of the frame, `metrics.border_black`), `blur` (tile sharpness < max(15, 25 % of the chunk median)). Survivors get `metrics.q_raw` = `{sharp (Laplacian var on the largest face expanded to head+shoulders, else the centre 60 %, minus 20*noise^2), noise (Immerkaer sigma on flat regions), exposure (subject band 40-210, subject + global clipping), face (size in native px saturating at 160, yaw proxy from YuNet landmarks, score; null without a face), motion, iqa?, subject, subject_luma, face_short_px?, face_yaw?}`. Each component -> `0.7 * percentile in chunk + 0.3 * percentile in video` (lower-better inverted; `extract_chunk` uses the other chunks' `frame_features` as the video reference; no face = 0.5). `zs_scores.sharpness` = q_sharp and `zs_scores.quality` = 0.30 exposure + 0.20 noise + 0.20 motion + 0.30 face (IQA 25 % when on) feed the unchanged composite, so learned weights blend as before; `zs_scores` adds `quality_version: 2` and `q_components` (normalised). Faces in `metrics.faces` now carry `landmarks`.
- **IQA** (setting `iqa_model` 'off' default | 'clip_iqa' | 'topiq_nr'): `clip_iqa` = CLIP-IQA probe ("Good photo." vs "Bad photo.", softmax x100) on the existing CLIP embeddings, no download; `topiq_nr` = TOPIQ-NR (CFANet ResNet-50, KonIQ) ONNX `nocturna/topiq-nr-koniq-onnx` (~177 MB, input 1x3x384x384 RGB 0..1, ~0.15 s/frame CPU; CC BY-NC-SA 4.0, personal use). Load failure -> `stats.iqa = {model, active: false, error}` and no IQA term. MUSIQ: no maintained single-file ONNX export on HF found, so not offered.
- **Selection embedder** (setting `selection_embedder` 'clip' default | 'dinov2'): DINOv2-S `onnx-community/dinov2-small` `onnx/model_quantized.onnx` (~24 MB, HF download into `<cache>/models`, offline from `CP_MODELS_DIR` / `/app/models`), whole frame letterboxed to 224 (ImageNet-mean padding), CLS token L2-normalised (384-d). Extraction caches it for the stored frames in the npz (`stats.selection_embedder: {embedder, computed, cached}` or `{embedder:'clip', fallback}`); reselect takes it from the cache, computes missing ones from the frames' previews (and caches them), and falls back to CLIP when the model is unavailable or an image is missing. Coverage similarity uses the selection embedder; the hard near-duplicate check stays on CLIP (`similarity_dedup_threshold` is CLIP-calibrated).
- **Axis caps**: `axis_caps` (default setting `reselect_axis_caps`, `{}`; an explicit `{}` in the request disables stored caps) map `multi_person` (multiple_persons >= 0.45 or face_count >= 2), `solo` (one_person >= 0.45), `equipment` (max(tools, equipment) >= 0.45), `face` (face_visible_50pct or face_score >= 0.5) to a max share; aliases `multiple_persons`, `one_person`, `tools_equipment`, `tools`, `face_visible`. A frame in a full axis (`floor(share * target)` picks, locked frames count) is only picked when no uncapped eligible frame is left, counted as `overflow`.
- **Cross-dataset dedup** (`cross_dataset_dedup`, default setting `reselect_cross_dataset_dedup`, false): candidates whose CLIP cosine to any frame exported from another video is >= `similarity_dedup_threshold` are excluded (never starred/exported frames of this video); `cross_dataset.blocked_ids` lists them, they stay runners.

Settings (Phase F2): `reuse_analysis_cache` (true), `cut_detector` ('content'), `cut_adaptive_threshold` (3.0, 1-20), `cut_min_content_val` (15, 1-255), `cut_min_scene_s` (0.5, 0-30), `cut_fade_threshold` (12, 0-64), `quality_v2` (true), `iqa_model` ('off'), `selection_embedder` ('clip'), `reselect_axis_caps` ({}; keys validated against the 4 axes, shares 0-1), `reselect_cross_dataset_dedup` (false).

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
| `GET /api/captions/stats?video_id=&job_id=` | - | `CaptionStats` from the `captions` table (both filters optional) |
| `GET /api/frames/{frame_id}/caption` | - | `{frame_id, caption: CaptionRow \| null}`; **404** if the frame does not exist |
| `POST /api/export` | `{video_id?, trigger_word?}` | with id: `{exported, stubs_written, captions_kept, per_video_dir, merged_dir}`; without: `{exported, videos_processed}` over statuses `curating, curating_300, chunked` with no queued/running job (`exported` videos are skipped). The exporter never overwrites a VLM caption; it writes tag stubs only for uncaptioned frames, marked by a `.json` sidecar with `caption_source:"stub"` (legacy sidecars without the field are stubs too), which the caption job replaces. Standardized `render_export_image` shared with captioner; `manifest.jsonl` deduped. Phase G: the `captions` table is the source of truth - a `vlm`/`imported` row (re)writes its `.txt`, a pre-existing VLM `.txt` without a row is adopted as `imported`, stubs get a `stub` row that never displaces a caption |
| `GET /api/dataset` | - | `{total_exports, per_video:[{id,name,slug,count}], finished_dir}` |
| `GET/PUT /api/settings` | partial object | `{settings}` (defaults deep-merged). `openrouter_api_key` is never returned; the response has `openrouter_api_key_set: bool`. A `PUT` with an empty key keeps the stored one. `PUT` validates against the defaults (type of each default, `SETTING_RANGES`, `SETTING_CHOICES` in `config.py`; finite numbers only): any error is 400 `{detail: {message, errors: string[]}}` and nothing is stored; unknown keys are dropped and listed in `ignored_keys` |
| `GET /api/jobs?video_id=` / `DELETE /api/jobs/{id}` | - | `{jobs}` (last 20) / `{cancelled}`: `false` for finished/unknown jobs. A queued job is cancelled at once; a running job emits `job_progress` step `Cancelling...` and ends as `cancelled` at its next checkpoint without touching frames/picks |
| `GET /api/events` | - | SSE (below) |

```ts
OpenRouterCaptionRequest { api_key?, model='google/gemma-4-31b-it', fallback_layer2='deepseek/deepseek-v4.1-flash',
  fallback_layer3='x-ai/grok-4.7', reasoning_effort='none' /* any effort raises max_tokens to >=1200 */,
  use_chunk_models=true /* layer-1 model = chunk.caption_model when set, else `model` */,
  mode='concept'|'character'|'style', trigger='restrained_elegance', class_noun='woman',
  max_tokens=600 /* no 300 cap; cut-off replies (finish_reason length / no closing sentence) are retried, then the frame fails */,
  temperature=0.4 /* Phase G default, 0.3-0.5 recommended; was 0.80 */, top_p=0.90 }
CaptionRow { frame_id, video_id, text, model, layer /* layer1|layer2|layer3 */, mode, prompt_version /* '<mode>-g1-<sha8>' */,
  trigger, word_count, cost_usd: number|null /* null when OpenRouter reported no cost */, prompt_tokens, completion_tokens,
  warnings: string[], finish_reason, job_id, created_at, source: 'vlm'|'imported'|'stub' }
CaptionStats { total_captions, total_cost_usd, prompt_tokens, completion_tokens, avg_words /* non-stub */,
  by_source: {vlm, imported, stub}, by_model: [{model, layer, source, count, cost_usd, prompt_tokens, completion_tokens, avg_words}],
  by_prompt_version: [{prompt_version, count}], current_prompt_versions: {concept, character, style} }
VideoStatus = 'new'|'chunking'|'chunked'|'extracting'|'extracting_750'|'curating'|'curating_300'|'captioning'|'exported'|'skipped'|'ignored'|'broken'
```
Not used by the UI: `POST /api/jobs`, `/auto-process`, `/batch-auto-process`.

### SSE `/api/events`

`data: {"type","data"}` per message; first `{"type":"connected"}`. `job_queued|job_started` `{job_id,video_id,video_name,chunk_id}`; `job_progress` + `{progress:0..1, step}`; `job_finished` + `{picks_count?, runners_count?, stats?}` (caption job: `{exported, failed, layers:{layer1,layer2,layer3,reused}, rejected:{layer1,layer2,layer3}, prompt_version, cost_usd /* null if no cost reported */, prompt_tokens, completion_tokens, api_calls}`; with failures the job row is `finished` and `error` holds a summary ending in `Cost $X, N prompt + M completion tokens over K calls.`); `job_error` `{job_id,video_id,video_name,error}`; `job_cancelled` `{job_id, video_id, ...}`. A `: keepalive` comment is sent every 15 s; a slow client loses its oldest events (queue max 500). On error/cancel the server settles a video left in `extracting*`/`captioning`: `exported` if every keep/star frame has an export, else `curating_300` with picks, `chunked` with chunks, else `new`. The caption job ends `exported` only when every approved frame is captioned.

## LoRA profile (HANDOFF decision 1; frontend: workflow-bar toggle, `VideoService.setProfile`)

`videos.profile` = `'concept'` (default) | `'character'`; the character is the video's positive person (`video_persons`).
| Route | Body | Response |
| --- | --- | --- |
| `PATCH /api/videos/{id}` | `{profile}` (with the other VideoPatch fields) | `{video, profile_result?}`; switching to `character` runs the gate at once; other values 400 |
| `POST /api/videos/{id}/apply-profile` | - | `{video_id, profile, applied, reason?, demoted[], promoted[], cropped[], unfilled}`; 409 while a job holds the video |

Character gate (`pipeline/profiles.py`): picks without the target person are demoted (never starred/exported ones) and replaced by matching runners of the same chunk (solo first); multi-person picks showing the character get `picks.crop` = a 2:3 portrait around the matched face (manual crops win). Runs after `extract_750` and after face re-evaluation. `POST /caption-openrouter` without `mode` uses `character` for a character video, else the `openrouter_mode` setting; the response echoes `mode`.

## Image tags (learn-as-you-tag; frontend: `tag.service.ts`, `features/tags/*`, `tag-manager-modal`)

Boxes are normalised to the frame: `TagBox = {x, y, w, h}` in 0..1 (top-left origin). Routes live in `src/pipeline/tag_routes.py`.

| Route | Body | Response |
| --- | --- | --- |
| `GET /api/tags` | - | `{tags: ImageTag[], total, categories: TagCategory[]}` |
| `POST /api/tags` | `{name, category?, prompt?, color?}` | `{tag}`; an existing name/slug returns that tag; empty name 400 |
| `GET /api/tags/{id}` | - | `{tag, regions: TagRegion[]}` (examples + counter-examples; hidden `context` negatives excluded); 404 |
| `PATCH /api/tags/{id}` | `{name?, category?, prompt?, color?}` | `{tag}`; name clash 409 |
| `DELETE /api/tags/{id}` | - | `{status, deleted}`; cascades exemplars and frame tags |
| `POST /api/tags/{id}/merge` | `{into_tag_id}` | `{tag}` (target); exemplars and frame tags move, source deleted |
| `GET /api/tag-regions/{id}/crop` | - | JPEG crop of an exemplar |
| `DELETE /api/tag-regions/{id}` | - | `TagLearnResponse` (forget one exemplar) |
| `GET /api/frames/{frame_id}/tags?live=true` | - | `FrameTagsResponse`; `live=false` returns stored suggestions only (no embedding) |
| `POST /api/frames/{frame_id}/tags` | `{box, tag_id? \| tag_name?, category?, polarity: 'positive'\|'negative'}` | `TagLearnResponse`; a new `tag_name` creates the tag; 400 without tag/image, 404 unknown frame/tag |
| `POST /api/frames/{frame_id}/tags/{tag_id}/accept` | `{box?}` (adjusted box; default = suggested box) | `TagLearnResponse` |
| `POST /api/frames/{frame_id}/tags/{tag_id}/reject` | `{box?}` | `TagLearnResponse` (negative exemplar, frame `rejected`) |
| `DELETE /api/frames/{frame_id}/tags/{tag_id}` | - | `TagLearnResponse` (un-tag: drops the frame's exemplars for the tag) |
| `POST /api/videos/{id}/suggest-tags` | - | `{job_id, status:"queued"}` (job type `tag_suggest`; 400 when no tag exists; 409 when the video has an active job) |
| `GET /api/videos/{id}/tags` | - | `{video_id, tags: [{tag_id, name, color, category, confirmed, suggested, rejected}]}` |

```ts
ImageTag = { id, name, slug, category, prompt, color, threshold, accuracy: number|null, positive_count, negative_count,
  context_count, confirmed_count, suggested_count, cover_url: string|null, created_at, updated_at }
TagCategory = 'object'|'clothing'|'equipment'|'setting'|'pose'|'lighting'|'style'|'other'
TagRegion = { id, tag_id, frame_id: number|null /* null once the frame was re-extracted */, video_id, video_name?,
  polarity: 'positive'|'negative', source: 'manual'|'accepted'|'rejected', box: TagBox, crop_url }
FrameTag = { tag_id, name, color, category?, score: number|null, box: TagBox|null }
FrameTagsResponse = { frame_id, video_id, confirmed: FrameTag[], rejected: FrameTag[], suggestions: FrameTag[] /* score >= tag_suggest_min_score */,
  regions: (TagRegion & {name, color})[] }
TagLearnResponse = { status, tag_id, frame_id?, video_id?, region_id?, threshold?, accuracy?, auto_tag_job_id?, tag: ImageTag|null, frame: FrameTagsResponse|null }
```
Curation frames (`curation-300`, `candidates`, `swap-frame`, chunk candidates) carry `tags: [{tag_id, name, color, status:'confirmed'|'suggested', score}]`.
Every learning step re-scores the video's cached frames and broadcasts SSE `tags_updated` `{video_id, tag_id?, frames, scored, suggested, tags}` (`video_id: null` after a tag delete/merge); the first teach on a video with unembedded frames queues a `tag_suggest` job (`auto_tag_job_id`). Its `job_finished` adds `{frames, scored, suggested, tags}`.
Settings: `tag_suggest_min_score` (0.5), `auto_suggest_tags` (true: queue `tag_suggest` after `extract_750` when exemplars exist).

## RAG prompt director (Krea prompt tool; frontend: `prompt-rag.service.ts`)

| Method + path | Body | Response |
| --- | --- | --- |
| `GET /api/rag/categories` | - | `{categories}` taxonomy: `{equipment\|surroundings\|position\|camera_lighting: {title, icon, description, groups:{name:[{id,label,keywords}]}}}` |
| `GET /api/rag/presets` | - | `{presets:[{id,title,description,tags:{category:[tagId]}}]}` |
| `GET /api/rag/characters` | - | `{characters:{sara\|lene...: {id,name,age,avatar,summary,description,tag_tokens}}}` |
| `GET /api/rag/status` | - | `{ready, indexed, index:{count,source,source_path,built_at,updated_at,embedding_model}\|null, sources:{source,path,caption_files,searched}\|null (null while building), build:{state:"idle"\|"running"\|"done"\|"error",mode:"full"\|"incremental"\|null,pending: bool,progress,step,error,started_at,finished_at,result}, build_command, openrouter_key_configured, retrieval:{fusion:"rrf", bm25: bool, token_counter:"qwen"\|"heuristic", dense_mode:"whole"\|"domain", dense_modes:["whole","domain"], domain_embeddings: bool, image_index:{ready: bool, count, embedder:"siglip"\|"clip", model\|null, enabled: bool}}, auto_reindex: bool}`. `retrieval.dense_mode` = the mode used when a request does not choose one (`domain` when the index has per-domain vectors, else `whole`); `image_index.embedder` is the index's embedder, or the one a new index would try first (`siglip`) when there is none; `enabled` = setting `rag_image_index`. `source`: `"captions_table"\|"curated"\|"finished_merged"\|null` (`caption_files` = caption count for the table). `build.mode` is the requested mode, `build.result.mode` what ran (an incremental request without a compatible index runs a full build, `result.full_reason` says why); `pending` = an incremental update is queued behind the running build. When `ready`, the first call starts a one-off background warm-up (index load + CLIP text model) |
| `POST /api/rag/index` | `{limit?: int, incremental?: bool = false}` | same shape as `/status`; starts the background build (also (re)builds the FTS5 table); `incremental: true` embeds only new/changed captions (`result: {mode, added, updated, relabelled, removed, indexed, count, full_reason?}`), `limit` applies to full builds only; **409** if a build is already running |
| `POST /api/rag/retrieve` | `{selected_tags:{cat:[id]}, user_description, top_k=4 (1-12), dense_mode?: "whole"\|"domain"}` | `{results:[{id,stem,split,frame_id,source_id,score,matched_tags,img_rel_path,full_caption,equipment_text,surroundings_text,position_text,lighting_text}], index_ready: bool, dense_mode: "whole"\|"domain"}` (`results: []` if index empty/not ready; `dense_mode` = the mode that ran, `domain` falls back to `whole` for an index without domain vectors; **422** other values). `frame_id` is set for captions-table exemplars (null for file sources); `source_id` = video id (table) or stem prefix (files); `split` = `train\|val`. `score` is the RRF-fused relevance normalised to 0..1 (1 = rank 1 in every signal) |
| `POST /api/rag/generate` | `{selected_tags, user_description, character?, top_k=4, model, trigger, local_url?, api_key?, n=1 (1-10), dense_mode?: "whole"\|"domain"}` (exemplar retrieval as in `/retrieve`) | first variation's legacy fields `{model, trigger, character, grounded, notices, archetypes:{a,b,c:{name,prompt,token_count,warnings,is_valid,edited?}}, rag_context:[...]}` **plus** `{history_id: int\|null, batch_id: str, variations: HistoryEntry[]}` (all successful variations, each stored in history). `n>1`: pool of `min(12, 2*top_k)` exemplars, variation 0 = best `top_k`, others a seeded subset; temperature 0.70 → 0.95; max 3 concurrent LLM calls. Failed variations are reported in top-level `notices` (`"k of n variations failed: ..."`). **400** OpenRouter key missing (not local); **502** if every call fails upstream; **422** `n` outside 1-10 |
| `POST /api/rag/lint` | `{prompt (max 8000 chars), archetype: "a"\|"b"\|"c"\|"tags"\|"short"\|"dense" = "b", trigger, character?}` | `{clean_prompt, warnings, token_count, is_valid, archetype:"tags"\|"short"\|"dense", token_counter}` (inline edit + re-lint; a/b/c map to tags/short/dense) |
| `GET /api/rag/history` | query `favorites=1`, `limit=50` (1-500), `offset=0` | `{items: HistoryEntry[], total}` newest first |
| `PATCH /api/rag/history/{id}` | `{favorite?: bool, prompts?: {a?,b?,c?: str}}` | updated `HistoryEntry`; `prompts` are re-linted server-side and flagged `edited: true`; **404** unknown id |
| `DELETE /api/rag/history/{id}` | - | `{deleted: id}`; **404** unknown id |
| `GET /api/rag/preview/{caption_id}` | - | exemplar image (`image/png`, or `image/jpeg`/`webp` by suffix for cached frames); 404 if the RAG index (`~/.cache/captioning_pipeline/rag_index.sqlite`) isn't built, the row has no image or the file is gone |
| `GET /api/rag/similar` | query `frame_id` (required), `top_k=8` (1-50) | R3 "more like this frame": `{results:[...same item shape as /retrieve...], index_ready: bool, embedder:"siglip"\|"clip"}`. Embeds the frame's image (`frames.full_path`, else `preview_path`) with the embedder that built the image index and ranks the indexed captions by the image embedding of their exemplar image (`score` = cosine clipped to 0..1, `matched_tags: []`, the frame's own caption excluded, no MMR). **404** unknown frame, or no readable image on disk; **200** `{results: [], index_ready: false, embedder}` when there is no image index (text index missing, setting off, or no exemplar images); **503** the index's embedder cannot be loaded or does not match (rebuild the index) |
| `GET /api/rag/eval` | query `k=4` (1-50), `max_queries=200` (1-5000), `fusion=rrf\|linear`, `query_words=40` (0-500, 0 = whole caption), `dense_mode=whole\|domain` (default: server default) | R10 retrieval metrics on the val split: `{k, fusion, dense_mode, query_words, stage, split:{val, candidates}, queries, siblings:{queries, recall_at_k, hit_at_k, mrr, chance_recall_at_k}, tags:{queries, recall_at_k, precision_at_k}, index:{...}\|null, duration}` (metric values 0..1 or null when no query qualifies). Synchronous (one ranking per query). **404** index not built; **422** bad params |

`HistoryEntry` = `{id: int|null, created_at: epoch s, favorite, batch_id, batch_index, inputs:{selected_tags, user_description, character, model, trigger, top_k, n, local_url, dense_mode (null = default; missing in older entries)}, result: <generate result without history fields>}` (`id` is null only if the history write failed). Stored in the **`rag_history` table of the main state DB** (`~/.cache/captioning_pipeline/state.db`, created lazily by `pipeline/rag/history.py`, not `db.py`) so it survives index rebuilds; non-favourites beyond the newest 500 are pruned, favourites are kept. The API key is never stored.

Index sources (`pipeline/rag/indexer.py`, first non-empty wins): (1) the **`captions` table** of state.db, sources `vlm` + `imported` only (never `stub`), skipping frames whose pick is `reject`; exemplar image = the export PNG (`exports.merged_path`, else `per_video_path`), else the cached `frames.full_path`/`preview_path`, stored in `rag_captions.img_rel_path` at index time (project-relative when under the project, else absolute); stem = export file stem (`<slug>_cNN_<ms>`) or `<slug>_f<frame_id>`; (2) `data/curated_2500/{train,val}/*.txt`; (3) `finished/merged/*.txt`. Rows carry `frame_id, source_id, content_hash, caption_source` (old indexes are migrated by `ALTER TABLE`; the retriever loads them without these columns). **Split (R10):** table rows and merged files get `val` when `sha1(key)[:8] % 10000 < 1000` (10%, `VAL_FRACTION`), key = `frame:<frame_id>` resp. the file stem, else `train`; curated folders keep their own split. **Incremental (R6):** `RagCaptionIndexer.update_index` matches rows by frame id (else stem) + `content_hash`, embeds only new/changed captions, drops vanished ones, relabels metadata-only changes in place, and swaps SQLite rows + FTS5 + NPZ in one transaction + atomic rename (the retriever hot-reloads on the NPZ mtime and is also invalidated by `on_finished`). Falls back to a full build when the index is missing, the source or embedding model changed, or rows predate R6. **Per-domain vectors (R1):** `rag_embeddings.npz` also holds `seg_ids` (caption id), `seg_domains` (0-3 = equipment, surroundings, position, camera_lighting) and `seg_embeddings`: each domain text cut into chunks of at most 40 words (whole sentences, `DOMAIN_CHUNK_WORDS`; max 46 CLIP tokens on the fixture, below the 77-token context). An index without them still loads (`whole` only); an incremental update embeds them for new/changed rows and backfills rows that lack them (`result.domain_backfilled`). **Image index (R3):** `rag_image_embeddings.npz` in the cache dir (`ids, embeddings, valid, paths, embedder, model, built_at`), built during full and incremental builds when setting `rag_image_index` (default true) is on: SigLIP `Xenova/siglip-base-patch16-224` (`onnx/vision_model_quantized.onnx`, 768-d, bicubic 224x224 without crop, mean/std 0.5; downloaded once into `<cache>/models` or `CP_MODELS_DIR`, `/app/models` checked first) else, when SigLIP cannot be obtained, the CLIP vision model (512-d, centre crop); `CP_RAG_IMAGE_EMBEDDER=clip` skips SigLIP, `=off` disables. Incremental updates embed new rows and rows whose image path changed, keep the index's embedder, and re-embed everything when the embedder changes; a failing image stage is reported in `result.image_index.error` and never fails the text index. Build results add `domain_segments` and `image_index:{enabled, embedder, count, missing, error}`. After every successful `openrouter_caption` job the API queues an incremental update (`pipeline/rag/auto_reindex.py` hook, setting `rag_auto_reindex`, default true; queued behind a running build). **Eval (R10, `pipeline/rag/evaluation.py`, CLI `scripts/eval_rag.py [--fusion both] [--dense-mode both] [--query-words 0] [--log runs.jsonl]`):** each val caption's first `query_words` words are the description (no tags), ranked by `RagRetriever.score` (fused ranking before MMR / `max_per_source`) over the non-val captions without the query itself. `siblings`: relevant = captions of the same source video; `recall_at_k` = relevant in top k / min(k, #relevant), `hit_at_k`, `mrr` (1/rank of the first relevant, full ranking), `chance_recall_at_k` = random-ranking expectation. `tags`: the query's own taxonomy tags; `recall_at_k` = share covered by the top k, `precision_at_k` = mean share of a top-k caption's tags that the query has.

Retrieval (`pipeline/rag/retriever.py`): three rankings fused with Reciprocal Rank Fusion (k=60): weighted taxonomy-tag overlap (only captions with a match contribute), dense CLIP cosine of description + tag labels (query embeddings LRU-cached, 512 entries; `dense_mode` `whole` = one vector per caption, `domain` (R1 default) = the description split by the caption segmenter plus each selected tag's label in its own category, each domain part chunked like the index and scored by the max cosine over the caption's chunks of that domain (or its best other-domain chunk minus 0.05), averaged over the query's domains with weight 2 for domains with selected tags), and BM25 over `full_caption` + domain texts for the description via the SQLite FTS5 table `rag_captions_fts` in `rag_index.sqlite` (porter tokenizer). The FTS table is built with the index, or lazily on first load for an index that predates it (`rag_meta.fts_built_at` must match `built_at`); without FTS5 the BM25 list is skipped. Tags and domain sentences use whole-word keyword matching (optional plural, space = hyphen); rebuild the index to re-tag old captions. Token counts use the Qwen3-VL tokenizer only if `CP_QWEN_TOKENIZER` points at a `tokenizer.json` or it is already in the local HF cache (never downloaded), else the word heuristic. Export (`.txt` one prompt per line, `.jsonl` one object per prompt with inputs and `exemplar_stems`) is client-side (`core/utils/prompt-export.utils.ts`). Hotkeys `1/2/3` (HotkeyService actions `copy_prompt_1..3`) copy the B/A/C cards while the Prompt Director is mounted.

## Static serving (changed)

`api.py` serves the Angular build, resolved **per request** (a rebuild is picked up without a restart): `$CP_WEB_DIST` if set, else `src/web/dist/browser`, else `src/web/dist`, whichever contains `index.html`. The raw sources in `src/web` are never served: without a build every non-API GET answers **503** with a short "Frontend not built" HTML page (and startup logs a warning). A catch-all `GET /{full_path:path}` returns real files (contained in the build root; `..` cannot escape) or `index.html` (SPA fallback); `/static` is mounted on `src/web/dist/browser`. Unknown `GET /api/*` routes return a JSON 404 (`{"detail": "Unknown API route: /api/..."}`) rather than falling through to `index.html`. Build with `<base href="/">` (`bash scripts/build_web.sh` = production; `--dev` for a fast build).

## Remaining quirks (verified in current code)

1. (fixed in Phase A) Export-all now includes `curating_300`; see the export row above.
2. (fixed in Phase C) Row shapes unified: `swap-frame`, `curation-300`, `candidates`, and `chunks/{id}/candidates` now all include `is_pick`, `is_captioned`, `time_s`, `thumb_url`, `native`, and unified `chunk_pos`/`chunk_position`/`chunk_start`/`chunk_end`. `PUT /chunks` returns `scene_stats`.
3. (fixed in Phase C) Per-video mutex active: concurrent extraction or caption jobs on the same video return HTTP 409 Conflict (`JobConflictError`). Executors segregated: decode executor (max 2 workers) vs caption executor (max 4 workers).
4. **`PATCH /api/picks` rebalances only with `rebalance: true`**; a plain role change still shrinks the chunk quota.
5. **Settings**: `PUT /api/settings` is validated server-side (B4); still validate in the dialog for inline errors.
6. **API key**: `api_key` in the caption body is optional; the job falls back to the key stored in user settings, then `OPENROUTER_API_KEY`. Never log or URL-embed it, and never return it from an endpoint.
7. **Statuses**: backend emits all `VideoStatus` values; the UI must style/select all of them. Reconcile may rewrite statuses (`exported` when every keep/star frame has an export (rejected picks don't count), `curating_300` when picks exist, `chunked` when frames/chunks exist, `new` otherwise) except `skipped/ignored/broken`.

Fixed upstream (no longer a problem): `StreamingResponse` import (SSE works).
Verify in the Angular code rather than assume: whether `distribution-bar` maps `one_person_pct/tools_equipment_pct/face_visible_pct` (legacy JS expected `solo_pct/tools_pct/face_pct`).

## Typing and testing

One model file per resource in `src/web/app/core/models/` (existing: candidate, chunk, dataset, job, prompt-rag, settings, video); map backend shapes there. Backend tests: `tests/test_curation_api.py`, `test_rag.py`, `test_rag_api.py`, `test_openrouter.py`, `test_phase_a.py` (temp DB + `client` fixture pattern for API tests) (fixture-video pattern; never mutate library videos).
