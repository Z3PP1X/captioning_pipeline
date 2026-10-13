# Pipeline review: extraction, quality, diversity, captioning

Date: 2026-10-13. Reviewer: Pipeline Review Agent. Scope: `src/pipeline/*` (excluding `rag/`), `scripts/curate_dataset.py`, `scripts/bench.py`, `tests/`.

**Method and evidence legend.** This was static analysis only. Nothing was run: no WSL, `uv`, `ffmpeg` or Python deps on this Mac, and the video library and `data/` were not touched. Each claim carries a tag:

- **[V]** verified by reading the cited code (file:line refers to the working tree at `258a2d3` plus uncommitted changes).
- **[D]** from vendor documentation fetched this session (OpenRouter reasoning-token docs).
- **[A]** assumed or estimated (typical library behaviour, performance numbers, model magnitudes). These need confirming with `scripts/bench.py` in WSL.

**Hardware note.** `scripts/bench.py:3` says the target machine is "WSL aarch64, CPU only", and `AGENTS.md` asks for Linux aarch64 wheels. The task brief assumes a single home GPU. Every recommendation below therefore has a **CPU/ARM64 default** and an **optional NVIDIA path**. In WSL on ARM there is no NVDEC/VA-API and no CUDA [A]. That makes decode cost and model size the main constraints.

---

## 1. Executive summary

The pipeline is a reasonable first version: sample, then tier-1 metrics, then CLIP zero-shot, then chunk-stratified MMR, then a VLM cascade. But it has several **data-loss and correctness bugs that matter more than any algorithm change**:

1. **"Export" overwrites the VLM captions with tag stubs.** In practice the stub is just the trigger word. "Export All" already does this today for every `exported` video, and fixing B1 naively would also do it for every `curating_300` video. [V]
2. **Saving or suggesting chunks cascade-deletes all frames, picks and export links** for the video (FK `ON DELETE CASCADE`). [V]
3. **Gemma captions are probably truncated.** `reasoning.effort=high` with `max_tokens=300` gives about 80% of the budget to hidden reasoning [D], leaving about 60 visible tokens. Truncated output is accepted as a caption. [V]
4. **Cancel does not cancel.** A cancelled extract-750 still wipes the old frames and commits partial picks. A cancelled caption job marks the video `exported`. [V]
5. **Swapped-in runner-ups are only ≤720 px** and get captioned and exported at that size. Runner ranks are all 0, so "auto-replace" picks an arbitrary frame. [V]
6. Reconcile counts *rejected* picks, so a captioned video with any rejects flips back to `curating_300` at the next restart or scan. [V]
7. Memory: about 1,125–1,500 candidates (not 750) are held in RAM at 720p, plus all 300 native-resolution picks, with 4 parallel workers. That is roughly 6 GB per 1080p job and 12 GB or more per 4K job [V for the counts, A for the GB figures].
8. Security: SPA path traversal plus `CORS *` with credentials. Any web page can trigger paid caption jobs or read settings, including a stored API key. [V for the code, A for exploitability]

**Algorithmically**, quality is mostly an absolute Laplacian score on a 512 px downscale plus a global-luma heuristic. Semantics come from CLIP ViT-B/32 (quantized, centre-cropped 224 px), using uncalibrated sigmoid probes whose category bonus (up to +0.75) outweighs the quality score (0–1). Diversity is per-chunk MMR on raw CLIP cosine, which is compressed within one video, plus a cross-chunk dedup that only looks at about 20 neighbours. The extract-750 path skips hard gates (black, fade, blur floor) and skips intra-chunk pHash dedup entirely.

**Recommended new process (detail in §4–§6):**

- **A. One analysis pass per video, cached.** Decode once with `skip_frame=NONREF` and swscale-downscaled outputs, and store per-frame signals: TransNetV2 or adaptive-histogram shot cuts, motion, cheap sharpness. Chunk edits and re-extraction then need no full re-decode.
- **B. Window-peak candidate sampling inside shots**: the sharpest, least-motion frame per 1–2 s window, avoiding transitions. Then a dHash dedup and the gates: technical, person/face presence, and an identity gate for character LoRAs (OpenCV YuNet + SFace, no new dependencies).
- **C. Quality score v2**: weighted percentile-normalized components (subject sharpness, noise, exposure, face quality, optional IQA/aesthetic), with weights *learned from the user's own keep/reject history*, which already sits in the `picks` table.
- **D. Selection v2**: facility-location (quality × coverage) greedy over DINOv2 or SigLIP embeddings. It runs under a partition-matroid constraint (per-chunk budgets with an elasticity knob), adds caps on diversity axes (shot scale, face yaw, person count), and suppresses near-duplicates across the whole video and across the dataset.
- **E. Captioning v2**: no reasoning (or a large budget), temperature 0.3–0.5, prompts versioned in the repo, a `captions` table as the source of truth, validation, and an exporter that never rewrites captions.

---

## 2. Findings (severity-ranked)

Severity: **S1** = data loss or wrong dataset output; **S2** = serious functional, performance or security issue; **S3** = quality or robustness; **S4** = minor.

### S1: critical

| # | Finding | Evidence | Fix |
|---|---|---|---|
| C1 | **Exporter overwrites VLM captions.** The caption job writes `{slug}_c{NN}_{pts_ms:07d}.txt/.png` into `finished/per_video/<slug>/` and hard-links into `merged/`. `Exporter.export_video` writes exactly the same file names and unconditionally writes a *stub* caption: trigger + chunk tags + `zs.tags` + `best_label`. Frames from extract-750 have no `tags`/`best_label` in `zs_scores` (only legacy `_process_chunk` adds them), so the stub is just `restrained_elegance`. Because the merged files are hard links to the same inode, the merged copy is overwritten too. `POST /api/export` without `video_id` already includes `status='exported'`. **Fixing B1 by adding `curating_300` widens the damage.** `autoprocess.py:82` also calls the exporter. | `jobs.py:499-501,623,625-634`; `export.py:120-161,183-189`; `api.py:745`; `scoring.py:135-145` (no tags) | Make captions a DB entity (§7). The exporter must only *copy* existing captions, never synthesize them when a VLM caption exists; write stubs only for frames with no caption, under a different suffix or flag. Until then, remove `exported` from the export-all query and skip `.txt` files that already exist. Add a regression test. |
| C2 | **Chunk save or suggest destroys curation.** `save_user_chunks` and `generate_balanced_chunks` do `DELETE FROM chunks WHERE video_id=?`. `frames.chunk_id`, `picks.chunk_id` and `exports.frame_id` are all `ON DELETE CASCADE`, and `PRAGMA foreign_keys=ON` is set per connection. Any `PUT /chunks` (including GAP-report F2's proposed "save before extract") or "Suggest" on a curated or captioned video deletes every frame, pick and export row. Captions on disk survive, but the DB links do not; reconcile re-links them only if timestamps still match a frame. | `autochunk.py:165-166,208-209`; `db.py:43,52,64,92`; `api.py:289-299,334-342`; extract-750 auto-chunks at `jobs.py:169-172` | Upsert chunks by `id` and delete only removed ids. If time ranges change, mark them `dirty=1` and keep frames until the next extraction. Ask for confirmation before any destructive re-chunk once picks exist. |
| C3 | **Truncated captions from layer 1.** Gemma gets `reasoning.effort` (default `high`) with `max_tokens` hard-capped at 300. OpenRouter documents that `high` allocates about 80% of `max_tokens` to reasoning, that the limit covers reasoning plus visible output, and that `exclude:true` only hides the reasoning [D]. The retry fires only when content is *empty*. A partial answer with `finish_reason=length` passes validation (only a warning when under 40 estimated tokens). The prompt asks for 120–180 words. | `jobs.py:434,438,566`; `openrouter.py:207-216,315-327,254-259`; `api.py:130,135` | Default `reasoning` off for captioning (or `effort: low` with `max_tokens` ≥ 1,200). Reject `finish_reason == "length"` and retry. Validate the word count against the mode's range. Re-audit existing captions (word-count histogram; cut-off sentences without final punctuation). |
| C4 | **Cancellation corrupts state.** (a) extract-750 only checks the flag in the sampling loop and then `break`s into scoring and commit. That runs `DELETE FROM frames WHERE video_id` with partial candidates and sets job `finished` and video `curating_300`. (b) The caption job's cancelled items return `None`, then the job is set `finished` and the video `exported`. (c) A job cancelled while still queued is overwritten to `running`, because the start never checks the flag. (d) `cancel_job` marks even finished jobs `cancelled`, and `_cancel_flags` is never cleared. | `jobs.py:195-197,301-337` (a); `jobs.py:496-497,545-546,684-691` (b); `jobs.py:151-154,400-403` (c); `jobs.py:69-74` (d) | Check the flag between stages and *before* any destructive write. On cancel: no DB mutation, job `cancelled`, video status restored. Start-of-job: if cancelled, return. Use a `threading.Event` per job and clear it at the end. |

### S2: high

| # | Finding | Evidence | Fix |
|---|---|---|---|
| H1 | **Runner-ups are stored at ≤720 px**, and swapping or promoting one does not re-decode, so the caption job snaps and exports the 720p PNG. Every human correction in Curate produces a low-resolution training image. | `jobs.py:286-290` (runners get `item["img_bgr"]`, ≤720 from `decode.py:82-88` with `max_dim=720` at `jobs.py:210`); `api.py:409-434`; `jobs.py:540-555` | When a frame becomes `keep`/`star`, or lazily at caption/export time, re-decode the native frame (`decode_exact_frames`) if `full_path` dims are below the video dims. Record `native=1` per frame. |
| H2 | **Runner ranks are all 0.** `reduce_750_to_300` never sets `rank` on runners, so `swap-frame` without `pick_frame_id` (`ORDER BY p.rank ASC LIMIT 1`) returns an arbitrary runner, not the best or most similar. | `scoring.py:338-342`; `jobs.py:330`; `api.py:419-427` | Rank runners by score, or better, pick the replacement as `argmax_j sim(drop, j) · q_j` within the chunk (§6). |
| H3 | **Reconcile counts rejected picks.** `pick_count` counts `role='pick'` regardless of `state`. Exports exist only for keep/star, so any rejection gives `export_count < pick_count` and the video is "healed" from `exported` to `curating_300` on every restart or scan. The per-chunk `is_fully_captioned` and `caption_progress_pct` have the same problem. | `reconcile.py:123-126,144-148,181-194` | Count `role='pick' AND state IN ('keep','star')` (or all keep/star regardless of role, mirroring the caption query at `jobs.py:458`). |
| H4 | **No per-video job mutex.** Two extract-750 runs, or extract-750 plus a caption job on the same video, can run in parallel (4 workers). Extract deletes frames, which cascades to exports, while the caption job inserts exports for deleted `frame_id`s, causing FK errors and lost links. `scripts/start_three_extractions.py` fires three jobs at once. | `jobs.py:38,126-149,375-398,661-672` | Reject or queue a submit if a `queued/running` job exists for the video (HTTP 409). Serialize decode-heavy jobs (1–2 workers) separately from network-bound caption jobs. |
| H5 | **Memory blow-up.** Candidates per chunk are `max(15, round(750·b))` with `max_samples = 2·chunk_n` and `fps ≥ 1`, so a long video yields about 2×750 = **~1,500 candidates** (short videos about 1,125). All are held as ≤1280×720 BGR arrays (~2.8 MB each, about 4.1 GB). Then *all* 300 native frames are held in `exact_frames_map` (1080p 6.2 MB each, ~1.9 GB; 4K 24.9 MB each, ~7.5 GB) before writing. With 4 concurrent workers, WSL's default memory cap (50% of host) is easily exceeded. | `jobs.py:199-222,263-267,38`; `decode.py:81-89` | Stream: compute metrics and embeddings per chunk and keep only a 384–512 px copy in RAM. Write native picks to disk as soon as they are decoded (callback in `decode_exact_frames`). Limit decode workers to 1–2. |
| H6 | **SSE dispatch is not thread-safe.** Worker threads call `asyncio.Queue.put_nowait` on queues owned by the event loop. That is not thread-safe and does not wake the loop [A: documented asyncio behaviour], so events can lag or be lost. The queues are also unbounded per client. | `jobs.py:43-51,53-67` | Capture `loop = asyncio.get_running_loop()` in `sse_stream` and use `loop.call_soon_threadsafe(q.put_nowait, payload)`. Bound the queue with `maxsize` and drop the oldest progress events. Add a heartbeat comment every 15 s. |
| H7 | **Security.** (a) `serve_spa` returns `STATIC_DIR / full_path` without containment. A raw `..` path (for example `curl --path-as-is`, or `%2e%2e`) can probably read `.env` or `~/.cache/.../state.db` [A: Starlette does not normalize `path` params]. (b) `CORS allow_origins=["*"]` with `allow_credentials=True` means any website in the user's browser can call `localhost:8000`: start paid caption jobs, `PUT /api/settings`, read settings including `openrouter_api_key` (stored in `DEFAULT_USER_SETTINGS`). AGENTS.md even suggests binding `0.0.0.0`. | `api.py:63-69,871-875,775-787`; `config.py:87` | `resolved = (STATIC_DIR / p).resolve(); if not resolved.is_relative_to(STATIC_DIR.resolve()): 404`. Restrict CORS to `http://localhost:4200` (dev proxy) or drop it, since same-origin is served by FastAPI. Never return API keys from `GET /settings`. Bind to `127.0.0.1`. |
| H8 | **No hard gates or intra-chunk dedup in the 750 flow.** `hard_filter_check` (black, white, clipping, sharpness floor) and `filter_and_rank_tier1` (pHash ≤4 dedup) are only used by legacy `_process_chunk`. Black frames, fades and title cards in extract-750 are only down-weighted. | `jobs.py:230-247` vs `metrics.py:71-162`, `jobs.py:900-905` | Apply gates and dedup before embedding (this also saves CLIP time). |
| H9 | **Settings are ignored.** In the 750 flow, `target_candidates`, `target_curated` (hard-coded 750/300), `category_quotas`, `mmr_lambda`/`mmr_alpha`, `openrouter_concurrency` (hard-coded semaphore of 4) and `openrouter_max_dim` (hard-coded 512) are unused. `watermark_rect`/`letterbox` are stored but never applied to crop, scoring or export. | `jobs.py:175,246,490`; `openrouter.py:279`; `config.py:76-99`; `api.py:240-245` (only writer); `grep` shows no reader | Wire them through, or delete them from the settings UI to avoid false affordances. Applying letterbox and watermark crop *before* metrics and embedding also improves quality scores. |

### S3: medium

| # | Finding | Evidence |
|---|---|---|
| M1 | **Category quotas are uncalibrated and order-dependent.** Semantic scores are `sigmoid(10·(cos_pos − cos_neg))` on CLIP B/32. Cosine gaps between such probes are typically a few hundredths [A], so scores cluster near 0.5 and the 0.45 thresholds fire for most frames. `multiple_persons` and `one_person` are not mutually exclusive, so a frame can count toward both. `cat_bonus` can reach 0.75 against a composite in [0,1]. Global counters are updated chunk by chunk, so early chunks consume quotas and later chunks get `need_*=0.4`. `compute_distribution_stats` therefore reports unreliable percentages. | `scoring.py:113-121,126-133,240-291,165-174` |
| M2 | **Cross-chunk dedup is weak.** Picks are ordered by chunk; each pick is compared only with the next 19 picks (mostly the same or adjacent chunk). The loop `break`s after the first duplicate. The replacement is `runners_by_chunk[cid].pop(0)`: the first runner *in sampling order*, not the best, and never re-checked for duplication. The demoted pick is not returned to `runners_by_chunk`. | `scoring.py:344-379` |
| M3 | **Embeddings make diversity weak.** CLIP ViT-B/32 int8, 224 px, **centre crop of the short side**, which drops about 44% of a 16:9 frame's width (people at the edges are invisible to scoring). Raw cosine between frames of the same video sits in a narrow high band [A], so the MMR penalty `0.35·max_sim` varies by only about 0.05–0.1 while quality varies by about 0.3. Diversity barely influences the outcome. | `classifier.py:21-30,38`; `scoring.py:295-309` |
| M4 | **The sharpness metric is resolution- and noise-biased.** Frames are decoded at ≤720 px and metrics run on a 512 px downscale. That hides motion blur and compression on 1080p/4K sources, and grain or noise inflates Laplacian variance (noisy dark frames look "sharp"). The absolute normalization (`/350`) is not comparable across videos. Exposure is a global-luma heuristic, not subject exposure. | `jobs.py:210`; `metrics.py:29-45`; `scoring.py:92-111` |
| M5 | **Budget leakage.** If a chunk pool is smaller than its quota, the shortfall is not redistributed, so the total falls silently below 300. Rounding is absorbed by the largest chunk only. | `scoring.py:205-221,248` |
| M6 | **Auto-chunk and cuts.** Cuts come from 8×8×8 histogram correlation between *consecutive keyframes* (`skip_frame=NONKEY`). Cut times are quantized to GOP positions (±1–10 s), and camera motion across a GOP yields false cuts [A]. The `elif curr_len > max_scene_s` branch is unreachable (it sits after `>= min_scene_s`), so long scenes are never force-split before the 15-chunk merge. Each scene probe re-opens and seeks the container. | `indexer.py:180-207`; `autochunk.py:60-67,84-103` |
| M7 | **The caption job is brittle.** `asyncio.gather` without `return_exceptions`: one timeout, 5xx or triple refusal fails the whole job. There is no retry on 5xx/timeouts (only 429). The PNG is written before captioning. `manifest.jsonl` gets duplicates on every rerun (both caption job and exporter append). The caption path ignores `picks.crop` and `max_edge`, which the exporter applies, so the two "export" paths produce different images. | `jobs.py:552-555,658-660,680`; `openrouter.py:302-346`; `export.py:98-118,191-193` |
| M8 | **Status lifecycle gaps.** `auto_process` and `extract_video` errors leave the video in `extracting`. `/api/videos/next` can hand out a video that is currently `extracting`. `PATCH /videos` accepts any status. `VIDEO_STATUSES` lacks `chunked`/`broken` and is never used for validation. `POST .../candidates/{id}/state` accepts any state and inserts `role='candidate'`, a third role that the curation queries ignore. `swap-frame` does not check that both frames belong to the video or chunk. Extract-750 sets `chunked` mid-job when it auto-chunks. B1 is confirmed at `api.py:745` (but see C1 before fixing). | `jobs.py:764-772,852-860`; `api.py:211-217,237-239,409-434,624-635`; `config.py:11-14`; `autochunk.py:186` |
| M9 | **The disk cache grows without bound.** Each extraction writes about 1,500 PNGs and 1,500 webp files and never deletes earlier ones; timestamps differ between runs, so stale files accumulate. Estimate: about 2.5 GB per 1080p video and about 6 GB per 4K video [A] in the WSL ext4 VHDX. | `jobs.py:251-297` |
| M10 | **Prompts are not reproducible.** The system prompt appends text read *per request* from `/mnt/c/Users/Patri/.gemini/config/skills/...`, truncated mid-document at fixed character counts. That means a 9p read per frame and a prompt that is not under version control. The user message is concept and bondage-specific in *all* modes and conflicts with the character and style word ranges (80–140 / 75–130 vs 120–180). | `openrouter.py:100-135,144-175,197-201` |

### S4: low

- `stream_video` returns 416 when `end >= size`; RFC 7233 says to clamp `end` to `size-1`. `streaming.py:70`. The README claims "lightweight proxies", but none exist, so HEVC/MKV may not play in the browser. [V]
- **B2 is partly stale.** `swap-frame` now returns `is_pick` and `is_captioned` (`api.py:460-467`) but no caption stats. `PUT /chunks` returns raw rows without the caption stats (`api.py:296`, `autochunk.py:236`). **B3 is confirmed**: `chunk_pos` (curation-300, swap) vs `chunk_position`/`chunk_start` (`/candidates`, `/chunks/{id}/candidates`) at `api.py:365,587-598,671-682`. [V]
- Thread oversubscription: ONNX `intra_op_num_threads=min(8,cpu)`, PyAV `thread_type=AUTO`, and 4 workers. `classifier.py:42`, `decode.py:59`, `jobs.py:38`. [V]
- Reconcile does one write transaction per caption file on every startup and scan (thousands of commits). `reconcile.py:51-105`. [V]
- `scripts/curate_dataset.py` dedups only *within* a video (`is_duplicate(cand, accepted_by_video[vid])`), so near-identical sets across different videos (re-uploads, same studio set) pass. Its rounding-balance loop makes one pass only. `curate_dataset.py:198-206,269,303`. [V]
- Test gaps: no tests for cancel, export-overwrite, chunk-save cascade, reconcile with rejects, SSE threading, or path traversal. [V by test names]

---

## 3. Performance

### 3.1 Current data flow per video (extract-750) [V]

1. `get_or_build_filmstrip`: decode *keyframes only*, full-res `to_ndarray(bgr24)` per keyframe, histogram cuts and 160 px thumbnails.
2. `generate_balanced_chunks` (if no chunks): one container open and seek per scene probe.
3. Per chunk, `sample_chunk_frames`: seek to the chunk start and **decode every frame sequentially** to the chunk end. Chunks cover the video minus 15 s at each end, so this is effectively a **full decode**. Each sampled frame is converted to full-res BGR and then `cv2.resize`d to 720 px.
4. Tier-1 metrics on about 1,500 frames, then CLIP B/32 int8 on CPU, then scoring and reduction.
5. `decode_exact_frames` per chunk (one container per chunk, one seek per 2.5 s cluster) for 300 native picks.
6. Writes 1,500 webp files and 1,500 PNGs (runners 720p, picks native), then a single DB transaction.

### 3.2 Where time goes: 1-hour video (estimates, CPU aarch64, ~8–12 cores) [A]

| Stage | 1080p H.264 30 fps | 4K HEVC 30 fps | Notes |
|---|---|---|---|
| Filmstrip + cuts (keyframes only) | 0.5–1.5 min | 2–5 min | full-res BGR conversion per keyframe is wasteful |
| Auto-chunk probes | 0.5–2 min | 1–4 min | one seek/open per candidate scene (up to ~140) |
| **Sampling (full sequential decode)** | **4–8 min** (≈108k frames at 250–500 fps) | **20–50 min** (40–90 fps) | **dominant cost** |
| Tier-1 metrics (≈1,500) | ~10 s | ~10 s | |
| CLIP B/32 int8 (≈1,500) | 0.5–1 min | 0.5–1 min | |
| Exact native decode, 300 picks | 1–3 min | 3–8 min | seek + decode up to a GOP per cluster |
| PNG/webp writes (≈1,500) | 1–2 min | 2–5 min | PNG encode of native 4K is ~100–200 ms |
| **Extract total** | **≈8–17 min** | **≈30–75 min** | |
| Captioning 300 frames, concurrency 4 | 6–20 min | same | network-bound; with reasoning `high` it is slower and wastes tokens |

Calibrate with `scripts/bench.py` in WSL. It already measures all-frames vs `NONREF` vs `NONKEY` decode throughput, tier-1 and CLIP latency, and writes `~/.cache/captioning_pipeline/bench_results.json`. Those results were not available to this review.

### 3.3 Recommendations

1. **Decode fewer frames, cheaper.**
   - Set `stream.codec_context.skip_frame = "NONREF"` during sampling and analysis. It skips non-reference B-frames, typically 1.5–3× faster [A], and at 1–4 fps sampling you lose nothing.
   - Use `frame.reformat(width=w, height=h, format="bgr24"|"gray")` (swscale in C) instead of a full-res `to_ndarray` followed by `cv2.resize`. Never materialize 4K arrays except for final picks.
   - Decode the video **once** for analysis. Today the filmstrip pass, the probes and the sampling pass each decode separately, and every re-extract after a chunk edit repeats the full decode.
2. **Cache an analysis artefact per video** (`~/.cache/.../analysis/<video_id>.npz`). Store per decoded frame: pts, a 48×27 RGB thumbnail (the TransNetV2 input size), luma mean and std, frame-difference motion energy, and a cheap sharpness value at about 320 px on the frames at your sampling rate. Re-chunking and re-extracting then reuse the cache: the extract job only needs targeted decodes for the chosen windows.
3. **Proxy.** For 4K/HEVC, the analysis pass can also write a 540p short-GOP H.264 proxy (PyAV encode) for UI scrubbing. This also fixes browser playback of HEVC/MKV. It does not speed up the *first* analysis.
4. **Hardware decode.** It is not available on WSL/ARM [A]. On an NVIDIA box, PyAV ≥14 exposes `hwaccel` (verify against the pinned `av 19.0.1`), or use torchcodec or PyNvVideoCodec with CUDA. That would make 4K decode roughly 5–10× faster.
5. **Threading model.** Use one decode-and-score worker (or two on 12+ cores) and set ONNX `intra_op_num_threads` to about cores/2. Put caption jobs on a separate asyncio-native executor; they are network-bound.
6. **Memory.** Stream per chunk and keep only 512 px copies plus embeddings in RAM. Write native picks to disk as they are decoded. Do not store native PNGs for runners; decode them on promotion (fixes H1 as well).
7. **Disk.** Store runner previews only (webp 384–512 px). Keep native PNG only for keep/star frames, or use lossless WebP for the cache (smaller than PNG). Delete `candidates/<video_id>` contents before re-extracting.
8. **SQLite.** The patterns are mostly fine: WAL, a single writer lock, one transaction per commit. Batch reconcile writes into one transaction and throttle progress writes (for example at most 2 Hz). Add `ix_picks_chunk_role(chunk_id, role)`, plus `ix_frames_video(video_id)` if row counts grow (the `UNIQUE(video_id,t)` index already covers `video_id` prefix lookups).

---

## 4. Proposed extraction process

The goal is to find, per shot, a few *distinct, sharp, well-exposed moments*, without decoding the video more than once and without relying on fixed timestamps.

```
Stage A  Analysis pass (once per video, cached; ~1x decode, NONREF, swscale downscale)
  per decoded frame:  pts, thumb48x27 (RGB), luma mean/std, |diff| motion energy
  per sampled frame (e.g. 4 fps): gray@320 -> sharpness (Laplacian var + Tenengrad), noise sigma
  shot boundaries:    TransNetV2 on the 48x27 stream  (CPU OK: ~100M FLOPs/frame-window [A])
                      fallback: PySceneDetect AdaptiveDetector / ContentDetector on cached thumbs
                      also mark gradual transitions (fades/dissolves) as "transition zones"
  outputs: shots[], transition_zones[], per-frame signals  ->  analysis/<video_id>.npz
           filmstrip thumbs + cuts[] for the UI come from the same pass (frame-accurate cuts)

Stage B  Candidate windows (fast, no decode)
  for each chunk, intersect with shots; drop the first/last 0.3 s of each shot and transition zones
  split each shot into windows of W s (W adapts so that #windows ~ 3x chunk budget; W in [0.75, 3] s)
  in each window choose t* = argmax( z(sharpness) - a*z(motion) - b*[luma out of range] )
     -> "sharpness peak within window" instead of fixed-interval timestamps
  pre-dedup: dHash on thumb (Hamming <= 6 within the shot) -> keep higher-scoring frame

Stage C  Targeted decode of candidates (seek to keyframe, decode to t*, NONREF off for exactness)
  decode at <= 1024 px (reformat), apply letterbox/watermark crop (videos.letterbox / watermark_rect)

Stage D  Gates (cheap first, all ONNX/OpenCV, ARM64-friendly)
  G1 technical: luma 10..245, clipping < 0.3, sharpness >= chunk p20, not in transition zone
  G2 subject:   person present (detector) according to chunk.rule; face found if dataset needs faces
  G3 identity (character LoRA only): face embedding cos-sim to the character's reference set >= tau
  G4 junk:      text/title-card/watermark-heavy frames (EAST/DB text detector in OpenCV dnn) unless masked

Stage E  Scoring (section 5) -> Selection (section 6) -> native decode of picks only
```

**Library choices and wheel status on Linux aarch64:**

| Need | CPU/ARM64 default | Wheel/compat note | Optional GPU upgrade |
|---|---|---|---|
| Shot boundaries | TransNetV2 (PyTorch port or ONNX export); fallback PySceneDetect | PySceneDetect is pure Python + OpenCV, fine. A TransNetV2 PyTorch port runs on CPU torch aarch64 [A, verify package] | same, on CUDA |
| Face detect + landmarks | **OpenCV YuNet** (`cv2.FaceDetectorYN`, in opencv-python-headless ≥4.5.4; ONNX model from opencv_zoo) | no new pip dependency; ~5–15 ms at 640 px on CPU [A] | SCRFD/RetinaFace (InsightFace) |
| Face recognition / identity | **OpenCV SFace** (`cv2.FaceRecognizerSF`) | no new dependency | ArcFace/AdaFace ONNX via onnxruntime. Do not use the `insightface` pip package on aarch64 (compiles from source) [A]. InsightFace model weights are non-commercial; fine for personal use, note the licence. |
| Person detection / count | RTMDet-tiny or YOLOX-nano ONNX (Apache-2.0) via onnxruntime | onnxruntime has aarch64 wheels [V uv.lock lists onnxruntime] | larger RTMDet; Ultralytics YOLO is AGPL |
| Pose (optional diversity axis) | RTMPose-t ONNX or MediaPipe Pose | MediaPipe aarch64 Linux wheels: verify | ViTPose |
| Text detection | OpenCV `TextDetectionModel_DB` (ONNX) | no new dependency | PaddleOCR det |
| Avoid | `decord` (no aarch64 wheels [A]), anything TensorFlow-only | | |

---

## 5. Proposed quality model

### 5.1 Problems with the current definition [V]

- Composite = `0.35·min(1, lap/350) + 0.25·quality + 0.18·max(person probes) + 0.12·max(tool probes) + 0.10·face_probe`, plus a category bonus up to +0.75 in selection. That is absolute (not per-video), dominated by semantics, and the semantics are uncalibrated (M1).
- Sharpness is a global top-3-tile Laplacian on 512 px. That measures *some* texture in the frame, not whether the *subject or face* is sharp, and it rewards noise and grain.
- Exposure is distance of global mean luma from 128 (with a "chiaroscuro" special case), which penalizes legitimate low-key looks and ignores subject exposure.
- There is no face quality, no compression or noise measure, and no aesthetic/IQA signal.

### 5.2 Recommended pipeline: gates, then normalized components, then learned weights

**Gates (binary; a failure moves the frame to a "rejected" bucket with a reason, visible in the UI):** G1–G4 as in §4. Thresholds should be *relative* (chunk percentiles) where possible, with absolute floors for black or white frames.

**Components (each mapped to [0,1] by percentile within the chunk, blended with the video-level percentile):**

| Component | Computation (CPU-friendly default) | Why |
|---|---|---|
| `q_sharp_subject` | Laplacian variance **and** Tenengrad on the face crop (if a face is present), else on the person box, else the centre 60%, at ≥768 px. Divide by a noise estimate (median absolute deviation of the high-pass in flat regions) to get an SNR-like value. | subject focus and motion blur, robust to grain |
| `q_motion` | motion energy at t from the analysis cache (lower is better); peaks inside windows already favour still moments | motion blur proxy |
| `q_exposure` | subject-region luma within a tolerant band, highlight/shadow clipping *in the subject box*, global clipping penalty | allows low-key scenes; punishes a crushed subject |
| `q_noise_comp` | noise sigma plus a blockiness metric (8×8 boundary discontinuity) | compressed web sources |
| `q_face` (if a face is required or present) | face short side in px at export resolution (saturating at ~160 px), \|yaw\| and \|pitch\| from YuNet 5-point landmarks (penalty beyond ~45° yaw unless profile shots are wanted for diversity), eye-openness (landmark-based EAR needs 6-point eyes, so use MediaPipe FaceMesh or skip), face sharpness, and the **SFace/ArcFace embedding norm** as a cheap FIQA proxy (MagFace-style). CR-FIQA / SER-FIQ are better but heavier and need torch. | the main driver of character LoRA quality |
| `q_iqa` (optional) | pyiqa **TOPIQ-NR** or **MUSIQ** (CPU ~0.1–0.4 s/img [A]), run only on gate survivors (~400–600 per video) | perceptual technical quality |
| `q_aes` (optional, low weight) | LAION aesthetic v2 (CLIP ViT-L/14 + MLP; heavy on CPU) or Aesthetic Predictor v2.5 (SigLIP). On ARM CPU, consider skipping it or using the CLIP-IQA+ "good photo" probe | aesthetics matter less than fidelity for character LoRAs |
| `q_sem` | rule relevance with **SigLIP 2 (base)** zero-shot, calibrated with a per-video softmax over mutually exclusive classes ({solo, multiple, none}) plus a detector person count instead of CLIP probes | fixes M1 |

Q-Align (7B) and DOVER (video-level) are GPU-only or video-level and not worth it for this setup. Use DOVER only if you want to rank whole *videos or shots* on an NVIDIA box. [A]

**Score:**

```
S = Π gates · ( w_sharp·q_sharp_subject + w_mot·(1-q_motion) + w_exp·q_exposure
              + w_nc·q_noise_comp + w_face·q_face + w_iqa·q_iqa + w_aes·q_aes + w_sem·q_sem )
each q = 0.7·pct_in_chunk(q) + 0.3·pct_in_video(q)     # chunk-relative, but bad chunks stay bad-ish
```

Start weights (character LoRA): sharp 0.25, face 0.25, exposure 0.12, motion 0.08, noise/compression 0.10, IQA 0.10, sem 0.10, aes 0.0–0.05.

**Learn the weights from your own curation.** `picks.state` already records keep/reject/star for thousands of frames [V schema `db.py:51-56`]. Log every human action (keep, reject, star, swap: dropped and replacement) into a small `curation_events` table. Then fit a logistic regression (or LightGBM) from the components to P(keep). Use its coefficients as weights and report AUC per release. This is cheap, personal and measurable, and it replaces guesswork about weights.

**Persist** components and gate reasons in a `frame_features` table (§6.3) so the UI can explain "why rejected" and "why picked".

---

## 6. Proposed diversity selection algorithm

### 6.1 Problems with the current approach [V]

- MMR per chunk on raw CLIP-B/32 cosine (centre-cropped), with a weak spread of similarity values (M3).
- Category quotas are order-dependent and uncalibrated (M1).
- Cross-chunk dedup only reaches about 20 neighbours and picks a poor replacement (M2).
- There is no notion of *coverage*: MMR avoids duplicates but does not make sure each distinct look, pose or setting gets a representative.
- Nothing works across videos (`curate_dataset.py` dedups per video only).

### 6.2 Recommendation: quality-weighted facility location under budget constraints

- **Embedding:** **DINOv2-S/14** (21M params; strong for composition, pose and near-duplicates; ONNX export works on CPU) on the *letterboxed full frame* (pad, do not crop). Optionally concatenate a SigLIP-2 embedding for semantics such as outfit or setting. Whiten per video: subtract the video mean, then L2-normalize. This spreads the cosine range so that thresholds mean something.
- **Similarity:** `sim(i,j) = max(0, cos(e_i,e_j))^γ · (1 − β·[different shot]) + τ_t·exp(−|t_i−t_j|/6 s)·[same shot]`, with γ around 2 to sharpen. Temporal similarity applies within a shot only.
- **Objective (monotone submodular):**

  `F(S) = Σ_{i∈V} ω_i · max_{j∈S} sim(i,j)  +  λ · Σ_{j∈S} q_j`

  Here ω_i is a "representativeness" weight (the quality q_i of the frame being covered, so good-looking regions of the candidate space matter more), and λ is the quality-vs-coverage knob. Greedy is (1−1/e)-optimal under a cardinality constraint and ½-optimal under a matroid constraint, and lazy greedy makes it fast (n ≈ 600 after gating, so an n×n similarity matrix of 360k floats is trivial).
- **Constraints:** partition matroid over chunks (`|S ∩ chunk_c| ≤ cap_c`), plus soft axis caps (shot scale close/medium/wide, face yaw bins, person count). Apply the caps as a multiplicative penalty on marginal gain once a bin exceeds its share; that keeps the objective tractable.
- **Budget elasticity ε ∈ [0,1]:** each chunk gets a guaranteed floor `⌊(1−ε)·k_c⌋` and a cap `⌈(1+ε)·k_c⌉`. The rest floats globally to wherever marginal gain is highest. ε=0 reproduces today's strict per-chunk quotas; ε=0.3 lets visually rich chunks take more.
- **Hard near-duplicate suppression:** never add j if `max_{s∈S} cos_dino(j,s) > θ_dup` (around 0.92 after whitening [A, tune]) or `dHash(j,s) ≤ 6`. This applies across the *whole video*, and optionally against a **global dataset index** of already-exported frames from other videos (numpy matrix in `~/.cache/.../dataset_index.npz`). That catches re-uploads and identical sets.
- **Alternatives considered:** k-center greedy (pure coverage, ignores quality, sensitive to outliers); DPP (elegant quality × diversity, but kernel tuning and sampling are fiddlier and give no explicit budget control); k-means or HDBSCAN then best-per-cluster (simple and explainable, a good fallback; HDBSCAN handles a variable number of looks, but cluster count vs budget is awkward). Facility location gives explicit budgets, a quality knob and good explainability ("this pick covers 14 similar candidates").

### 6.3 Pseudocode (fits the existing DB)

```python
# inputs: frames rows for video (after gates) with emb_dino, q (score v2), chunk_id, t, shot_id, axes
def select_video(frames, chunks, K=300, lam=0.5, eps=0.2, theta_dup=0.92,
                 axis_caps={"shot_scale": {"close": .40, "medium": .45, "wide": .30},
                            "face_yaw": {"frontal": .60, "three_q": .45, "profile": .25}},
                 global_index=None, theta_global=0.95):
    E = whiten_l2([f.emb_dino for f in frames])          # per-video mean removal
    q = np.array([f.q for f in frames])
    Sim = np.clip(E @ E.T, 0, None) ** 2
    same_shot = shot_ids[:, None] == shot_ids[None, :]
    Sim += 0.25 * same_shot * np.exp(-abs(t[:, None] - t[None, :]) / 6.0)
    omega = q                                             # weight of being covered
    k_c = chunk_quotas(chunks, K)                         # round(K*budget_pct/100), largest-remainder
    floor = {c: int((1-eps)*k) for c, k in k_c.items()}
    cap   = {c: int(np.ceil((1+eps)*k)) for c, k in k_c.items()}

    # optional cross-dataset suppression
    if global_index is not None:
        blocked = (E @ global_index.T).max(1) > theta_global
    best_cover = np.zeros(len(frames))                    # current max sim of each i to S
    S, count_c, count_axis = [], Counter(), Counter()

    def gain(j):
        cov = (omega * np.maximum(0, Sim[:, j] - best_cover)).sum()
        g = cov + lam * q[j] * omega.sum() / len(frames)  # scale-match the two terms
        for ax, caps in axis_caps.items():                # soft caps
            b = frames[j].axes[ax]
            if count_axis[(ax, b)] >= caps[b] * K: g *= 0.3
        return g

    # phase 1: satisfy floors per chunk (round-robin over chunks, lazy greedy inside chunk)
    # phase 2: global lazy greedy until |S| == K respecting caps
    heap = [(-gain(j), j) for j in candidates if not blocked[j]]
    while len(S) < K and heap:
        _, j = heappop(heap); gj = gain(j)                # lazy re-evaluation
        if heap and gj < -heap[0][0]: heappush(heap, (-gj, j)); continue
        c = frames[j].chunk_id
        if count_c[c] >= cap[c] or phase1_blocks(c, count_c, floor): continue
        if S and (E[j] @ E[S].T).max() > theta_dup: continue   # hard near-dup
        S.append(j); count_c[c] += 1; update_axes(count_axis, frames[j])
        best_cover = np.maximum(best_cover, Sim[:, j])
    # runner-ups: rank remaining by marginal gain given S (store as picks.rank) -> fixes H2
    # replacement for a dropped pick d: argmax_j Sim[d, j] * q[j] among runners in same chunk
    return S, ranked_runners
```

**DB additions (additive migrations in `db.py`, same style as the existing `ALTER TABLE`):**

- `frame_features(frame_id PK → frames ON DELETE CASCADE, shot_id INT, q REAL, q_components JSON, gates JSON, face JSON /*box, yaw, pitch, size_px, emb_norm, id_sim*/, persons INT, shot_scale TEXT, emb_dino BLOB)`
- `picks.reason JSON` (marginal gain, covered count, bin) and `picks.native INT`
- `curation_events(id, frame_id, video_id, action, replaced_by, at)` as the training signal for §5.2
- `settings.selection = {lam, eps, theta_dup, theta_global, axis_caps, profile: "character"|"concept"}`

### 6.4 UI knobs (Curate toolbar, per video; defaults in Settings)

- **Quality ↔ Diversity** slider (λ) and **Budget strictness** slider (ε): "strict per-chunk" through "global".
- **Near-duplicate strictness** (θ_dup) and a toggle **"Suppress duplicates across dataset"**.
- **Axis targets**: shot scale (close/medium/wide %), face angle (frontal/¾/profile %), people (solo/multi %). The person mix replaces the hard-coded 50/28/22 quotas and depends on the dataset profile.
- **Profile**: Character (identity gate on, solo-first, face weight high) vs Concept (multi-person allowed, equipment weight).
- **Explainability**: "why" tooltip per pick (score components, covered N similar), and a gate-reason filter for rejected candidates. "Replace" picks the most similar runner (fixes H2).
- **Re-run selection only**: select v2 runs in about a second from stored features, so sliders can re-select live without re-extracting.

### 6.5 Character vs concept: a product decision that needs a human

The stated goal is "LoRA of specific characters". The current defaults target a *concept* dataset: `openrouter_mode="concept"`, a single trigger `restrained_elegance`, a target of 50% multi-person frames, and a prompt that describes the subject's demographics to decouple identity [V `config.py:79-96`, `openrouter.py:163-173`]. For a character LoRA, multi-person frames without an identity gate or crop leak other people's identities into the trigger. Recommendation: a per-video or per-chunk `profile` and `character_id` (the RAG taxonomy already has `CHARACTERS` presets), an identity gate, and solo or cropped frames preferred.

---

## 7. Captioning

**Verified state:** a three-layer cascade (chunk model or Gemma, then DeepSeek, then Grok) only for refusals; 512 px JPEG q85; temperature 0.8; `max_tokens` ≤300; reasoning `high` for Gemma; trigger enforced at token 0 by prepending; sludge stripped; refusal detection by phrase list. Files go straight to `finished/`, and the DB stores only `exports` paths, not caption text [V `jobs.py:400-721`, `openrouter.py`].

Recommendations:

1. **Fix C3 first.** Turn reasoning off for captioning (captions don't benefit much from hidden reasoning [A]), or use `effort: low` with `max_tokens` around 1,200. Treat `finish_reason == "length"` as a failure and retry once with no reasoning.
2. **Determinism and consistency.** Temperature 0.3–0.5, `top_p` 0.9. Version prompts as files in the repo (`src/pipeline/prompts/{character,concept,style}.md`) and store `prompt_version` (hash) and `model` per caption. Stop reading `~/.gemini/...` at runtime (M10). Make the user message mode-specific and consistent with the system prompt's word ranges.
3. **Captions table** `captions(frame_id PK, text, model, layer, prompt_version, mode, trigger, warnings JSON, finish_reason, tokens_in, tokens_out, cost, created_at)`. The exporter and RAG read from it; the filesystem `.txt` is an export artefact. This removes C1 at the root and makes reconcile simpler.
4. **Trigger handling.** Use a per-profile trigger: character → `<char_trigger> <class_noun>` as the subject reference, never describing immutable identity traits. For multi-person frames, instruct "refer to the subject on the left/right as `<trigger>`", using the detected face box position as a hint, or crop. Validate: trigger appears exactly once and at token 0, and no second subject is described with the trigger.
5. **Context injection.** Pass chunk `notes`/`tags` as a short "scene context" hint so vocabulary stays consistent within a scene. Normalize synonyms after captioning using the taxonomy in `pipeline/rag/taxonomy.py`, so captions and RAG share one vocabulary.
6. **Validation gate** before writing: word count in range per mode; ends with sentence punctuation (truncation check); banned identity descriptors in character mode (eye or nose shape, ethnicity); sludge; refusal residue; near-duplicate captions within a chunk (token Jaccard > 0.8 means regenerate at a slightly higher temperature or keep with a warning). Show warnings in Curate and persist them.
7. **Robustness.** Use `gather(..., return_exceptions=True)`. Retry 5xx and timeouts with exponential backoff and jitter (3 attempts). If all layers refuse or fail, mark the frame `caption_failed` and keep going. Respect `openrouter_concurrency`. Write the PNG only after the caption succeeds. Rewrite the manifest per video instead of appending.
8. **Image input.** 512 px is cheap, but rope and knot detail is often below what 512 px resolves; test 768 px on 20 frames per model and compare. Apply `crop` and `max_edge` consistently in a single shared `render_export_image()` used by both the caption job and the exporter.
9. **Cost estimate** [A]: system prompt about 350 tokens plus skill context up to about 7.5k characters (≈1.9k tokens), plus image tokens (model-dependent, roughly 250–1,000 at 512 px), plus output ≤300 (plus reasoning). That is about 0.7–1.2M input tokens per 300-frame video. Replacing the raw skill dump with a compact glossary (≤400 tokens) roughly halves input cost. Prompt caching (provider-dependent) helps further because the system prompt is identical across frames.
10. **Optional cheaper hybrid:** run a local tagger (WD-tagger / JoyTag ONNX, CPU) first and give its tags to the VLM as hints. This improves recall of equipment terms, and the cheaper layer-1 model is more likely to succeed.

---

## 8. Phased roadmap

| Phase | Content | Effort | Exit criteria |
|---|---|---|---|
| **0: Stop the bleeding** | C1 (exporter must not overwrite captions; fix B1 only after C1), C2 (chunk upsert), C3 (reasoning/max_tokens, reject `length`), C4 (cancel), H3 (reconcile keep/star), H6 (SSE threadsafe), H7 (path containment, CORS, key not returned, bind 127.0.0.1). One regression test each. Audit existing captions for truncation and stub overwrites. | 1–1.5 days | tests green in WSL; no destructive path without explicit confirmation |
| **1: Correctness and resources** | H1 (native re-decode on promote), H2 (runner ranking and similarity-based replace), H4 (per-video job lock, separate decode and caption executors), H5 (streaming, 1–2 decode workers), H8 (gates and dedup in the 750 flow), H9 (wire settings or remove them; apply letterbox/watermark crop), M5, M7, M8, M9. Run `bench.py` and record the baseline. | 2–3 days | 1-hour 1080p extract with peak RSS < 2.5 GB; timings recorded |
| **2: Extraction v2** | Analysis pass and cache (NONREF, swscale, npz); TransNetV2 or PySceneDetect cuts (frame-accurate filmstrip cuts, fixes M6); window-peak sampling; dHash pre-dedup; YuNet face, SFace identity gate, person detector; `frame_features` table. | 3–5 days | re-extract after a chunk edit < 2 min on a 1-hour video; cut precision spot-checked |
| **3: Quality v2** | Component metrics (§5); percentile normalization; `curation_events` logging; weight fitting script with AUC report; optional TOPIQ/MUSIQ via pyiqa (CPU torch aarch64). | 2–4 days | AUC of P(keep) vs the current composite on held-out curated videos |
| **4: Selection v2** | DINOv2-S ONNX embeddings (letterboxed); facility-location selection with ε/λ/θ and axis caps; cross-dataset index; UI knobs and "why" tooltips; live re-select endpoint `POST /videos/{id}/reselect`. Update `api-contract`. | 3–5 days | human reject rate on auto-picks drops (track via `curation_events`); fewer near-duplicates in the final dataset (measure max pairwise sim) |
| **5: Captioning v2** | `captions` table; prompts in the repo plus version hash; validation gate; profile-based trigger handling; robust retries; shared image renderer; cost tracking. | 2–3 days | 0 truncated captions; per-video cost logged; exporter reads the DB |
| **6 (optional, NVIDIA box)** | Hardware decode; SigLIP-2/DINOv2-B on GPU; aesthetic v2.5; CR-FIQA; DOVER for shot ranking. | 2–3 days | 4K 1-hour extract < 10 min |

---

## 9. What is verified vs assumed

- **Verified by reading code:** every file:line finding in §2; the data flow in §3.1; candidate counts (~1,125–1,500) derived from `jobs.py:199-211` and `decode.py:51-55`; schema and cascade behaviour; settings that are not used; scoring formulas and thresholds; caption payload construction.
- **Verified by docs fetched this session:** OpenRouter reasoning effort allocates about 80% of `max_tokens` for `high`, the limit covers reasoning plus output, and `exclude` only hides the reasoning (https://openrouter.ai/docs/use-cases/reasoning-tokens).
- **Assumed or estimated (benchmark before relying on them):** all timings and GB figures in §3.2 and M9; CLIP cosine ranges (M1, M3); Starlette path-traversal exploitability (H7a); asyncio cross-thread wake-up failure mode (H6); model latencies on ARM CPU; wheel availability for the TransNetV2 port, MediaPipe aarch64 and PyAV hwaccel; quality gains from the proposed models. Nothing was executed.
