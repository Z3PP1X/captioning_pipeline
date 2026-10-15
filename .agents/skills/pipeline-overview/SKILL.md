---
name: pipeline-overview
description: Orientation for the captioning_pipeline repo - pipeline stages (index, chunk, extract 750, score, curate 300, caption, export), Krea RAG prompt tool, caption/state reconciliation, module map, SQLite model, jobs/SSE, video status lifecycle, Angular web build/serve flow, and how to run/test under the mandatory WSL setup. Use at the start of any task in this repo and before touching src/pipeline, scripts/, tests/ or the web UI.
---

# Captioning pipeline - overview

Builds LoRA training datasets from long videos: a human splits each video into scene **chunks**, the pipeline samples and scores candidate frames, the human curates the best 300, a VLM captions them, and they are exported as PNG + caption pairs. A second tool (RAG prompt director) mines the finished captions to generate Krea image prompts.

Backend: Python 3.12, FastAPI, SQLite, PyAV/OpenCV, ONNX (CLIP), OpenRouter VLMs. Frontend: Angular 22 + Tailwind v4 + Spartan in `src/web` (legacy `src/web/app.js` remains in the tree).

## Environment rules (AGENTS.md - mandatory)

- Everything runs in **WSL 2 Ubuntu**; never run python/uv/node natively on Windows. Use `scripts/`:
  - `bash scripts/setup.sh` (uv sync) - `bash scripts/run.sh` (uvicorn :8000 `--reload`, API + whatever frontend is already built) - `bash scripts/test.sh [pytest args]`.
- Venv `~/.venvs/captioning_pipeline`, caches/SQLite/previews/filmstrips/RAG index in `~/.cache/captioning_pipeline` (ext4). Only source and `data/finished` live on `/mnt/c`.
- `scripts/env.sh` hard-codes `/mnt/c/Users/Patri/...`; on other machines override `PROJECT_ROOT`, `CP_VIDEO_DIR`, `CP_FINISHED_DIR`.
- Config: `Settings` (env prefix `CP_`: `VIDEO_DIR, CACHE_DIR, FINISHED_DIR, MIN_DURATION_S`; derived: `project_root` = `finished_dir/../..`, `curated_dir` = `<project_root>/data/curated_2500`, `openrouter_api_key` from env `OPENROUTER_API_KEY`) + UI-editable `DEFAULT_USER_SETTINGS` in the `settings` table.

## Web build / run flow

- `package.json` / `angular.json` at repo root; Angular `sourceRoot` = `src/web`, build output `src/web/dist` (gitignored, along with `node_modules/`, `.angular/`). `proxy.conf.json` proxies `/api` + `/static` to :8000.
- `scripts/build_web.sh`: `ng build --configuration development` using `./node_modules/.bin/ng`, sets `NG_CLI_ANALYTICS=false`, writes `~/.angular-config.json`, and `NODE_OPTIONS=--require scripts/patch_os.js` (stubs `os.networkInterfaces()` with dummy interfaces so Node/Angular don't crash on WSL network enumeration).
- `scripts/start_web.sh`: `ng serve` on :4200 with the proxy. `scripts/start_all.sh`: build the frontend, then source `env.sh` and start uvicorn on :8000 (no `--reload`).
- `api.py` serving: static dir = `src/web/dist/browser` (or `src/web/dist`) if it has `index.html`, else `src/web`; mounted at `/static`, plus catch-all `GET /{path}` returning the file or `index.html` (SPA fallback). Angular must therefore be built before FastAPI serves it; unknown `/api/...` GETs return HTML, not 404.
- Note: scripts run `npm`-less (`node_modules/.bin/ng`); `npm install` must be done in WSL.

## Pipeline stages and video status

| Stage | Trigger | Result / status |
| --- | --- | --- |
| Index | startup + `POST /api/videos/scan` | `videos` rows, `new`; `broken=1` flagged |
| Reconcile | startup + scan + `POST /api/videos/sync-captions` (`reconcile.py`) | caption `.txt` files in `finished/per_video/*/` matched to frames (`{slug}_c{NN}_{pts_ms}.txt`, +-50 ms) -> `exports` rows; stuck statuses healed |
| Filmstrip + cuts | `GET .../filmstrip` | webp thumbs + `cuts[]` |
| Chunking (human) | `PUT /chunks`, `POST /chunks/suggest` | `chunks`, status `chunked` |
| Extract + score 750 | `POST .../extract-750` -> job `extract_750` | `extracting_750` -> `curating_300`; `frames` + `picks` |
| Curate 300 (human) | picks PATCH / swap-frame / keep_all / reject_all | pick `state`/`role` |
| Caption | `POST .../caption-openrouter` -> job `openrouter_caption` | `captioning` -> `exported`; per-chunk `caption_model` used as layer-1 model when `use_chunk_models` |
| Export | `POST /api/export` | PNG (multiples of 16) + caption txt, `exported` |
| Auto-process | `/auto-process`, `scripts/batch_auto_process.py` | job `auto_process`, ends `exported` |

Status handling: startup no longer blindly resets `extracting*` to `chunked`; orphaned running/queued jobs are marked `error`, then `reconcile_captions_and_state` recomputes each video that has no active job: `exported` if exports >= picks > 0; `curating_300` if picks exist; `chunked` if frames or chunks exist; `new` if it was in a transitional state with nothing; `skipped/ignored/broken` untouched. Failed extract/caption jobs reset `extracting_750`/`captioning` to `curating_300` (has picks) or `chunked`. Other statuses present: `chunking`, `extracting`, `curating` (legacy per-chunk/auto-process).

## Domain vocabulary

- **Chunk/scene**: time range with `budget_pct` (sums to 100, max 15 per video), `k` (>=5), `rule`, `tags`, `notes`, `caption_model` (new column, default `google/gemma-4-31b-it`; added by safe `ALTER TABLE` migration in `db.py`). `get_video_scenes_caption_stats` adds `picks_count/exports_count/captioned_count/total_frames/is_fully_captioned/caption_progress_pct`.
- **Candidate pool** ~750 frames scored on 7 dimensions (`scoring.py`: tier-1 metrics + CLIP zero-shot `zs_scores`); **pick** = curated 300 with per-chunk quota `round(300*budget/100)`; **runner_up** ~450. Pick `state`: `keep|reject|star|candidate`. Distribution quotas: multiple ~50%, one person 25-30%, tools/equipment 20-25%, face>=50% prioritised.
- **Caption cascade**: layer 1 (Gemma, or the chunk's model) -> DeepSeek -> Grok only for refused frames; frames <=512 px; modes `concept|character|style`.
- **RAG prompt director** (`pipeline/rag`): `RagCaptionIndexer` scans curated captions in `curated_dir` (`data/curated_2500`, split subdirs of `*.txt` + images), segments them into taxonomy categories (`equipment, surroundings, position, camera_lighting`), embeds them (`rag_index.sqlite` + `rag_embeddings.npz` in the cache dir; build with `python scripts/index_rag.py [--limit --batch-size]`); `RagRetriever.retrieve` = tag-match score + dense similarity; `RagPromptGenerator.generate` (OpenRouter or local Ollama URL) produces three archetypes A (tags), B (short anchor), C (dense narrative) with the trigger at token 0, adult-only constraint, character presets (`sara`, `lene`); `linter.lint_prompt` strips weights/sludge/preambles and checks token budgets. `taxonomy.py` holds `TAXONOMY_CATEGORIES`, `CURATED_PRESETS`, `CHARACTERS`. Endpoints under `/api/rag/*` (see `api-contract`).

## Module map (`src/pipeline`)

`api.py` routes + SPA serving - `db.py` (JSON cols auto-decoded) - `face.py` (YuNet + SFace face recognition) - `jobs.py` JobManager + SSE - `reconcile.py` - `autochunk.py` / `autoprocess.py` - `indexer.py` - `decode.py` (PyAV) - `metrics.py` - `classifier.py` / `tagger.py` - `scoring.py` / `select.py` - `openrouter.py` - `export.py` - `streaming.py` (HTTP 206) - `rag/` (`indexer, retriever, generator, linter, taxonomy`). Tables: `videos, chunks, frames, picks, persons, jobs, exports, settings` (+ separate `rag_index.sqlite`).

Frontend map (`src/web/app`): `core/{models,services,utils}` (api, chunk, curate, dataset, hotkey, job-event, prompt-rag, settings, video services), `features/{shell,studio,curate,dataset,modals,prompt-clicker}` (+ `shared/ui` Spartan helm). See `angular-migration`, `studio-ui-spec`.

## Scripts (beyond setup/run/test)

Ops/diagnostics (all talk to the DB or a running server; read before running, several are one-off for specific videos): `check_*`, `db_stats.py`, `inspect_*`, `verify_api_live.py`, `test_sse.py`, `test_web_serve.py`, `run_reconcile.py`, `index_rag.py`, `curate_dataset.py`, `finish_captioning.py`, `replace_trigger.py`, `start_three_extractions.py` and the older `start_*`/`requeue_*`/`cancel_job.py`. Do not run job-starting scripts against the user's library without being asked.

## Working agreements

- Tests: `bash scripts/test.sh` (`tests/test_curation_api.py`, `test_rag.py`, `test_openrouter.py`, `test_scoring.py`, ...). API tests use `TestClient` with dedicated fixture video ids - never mutate library videos.
- API changes must be reflected in `api-contract` and the Angular models.
- Never commit `.env`, `*.db`, extracted frames, `data/finished`, `node_modules`, `src/web/dist`.
