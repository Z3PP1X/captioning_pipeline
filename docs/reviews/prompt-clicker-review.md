# Prompt Director ("clicker tool") review

Date: 2026-10-13. Scope: the RAG-powered Krea Prompt Clicker / Prompt Director (commit `258a2d3`), why it is "not available", what was fixed, a design and workflow review, and a RAG improvement plan.

How this was verified: on macOS (no WSL here) with a throwaway Python 3.12 venv holding the `pyproject.toml` deps, `npm ci` and a production `ng build`, `pytest`, and a uvicorn server pointed at scratch `CP_*` folders with **synthetic** captions (never the user's library, `data/` or DBs). The UI was driven in headless Chrome over CDP: tab opened, index built from the UI, exemplars previewed, prompts generated against a fake Ollama endpoint.

## 1. Root cause

The tab itself was never missing. It is wired in `app.component.ts` and `header.component.ts`, and the component compiled cleanly in a production build. Categories, presets and characters loaded (they are static). The tool was unusable for three reasons, which stack on top of each other:

| # | Cause | Evidence |
| --- | --- | --- |
| R1 | **`/api/rag/retrieve` and `/api/rag/generate` crash with HTTP 500 when the index is missing.** `RagRetriever._ensure_loaded` raised `FileNotFoundError`, and the routes didn't catch it. FastAPI returned `500 text/plain "Internal Server Error"`. `ApiService` can't parse that as JSON, so the UI only showed the red text "Internal Server Error". Nothing pointed to the fix, and there was no way to build the index from the app. (The handover's "404 RAG index not built yet" only applies to `/api/rag/preview/{id}`.) | Probe against the original code: `POST /api/rag/retrieve 500 text/plain Internal Server Error`, same for `/generate`; `GET /api/rag/preview/1 404 {"detail":"RAG index not built yet"}` |
| R2 | **The index can only be built from `data/curated_2500/{train,val}`.** That folder is written only by the ad-hoc `scripts/curate_dataset.py`. The normal pipeline (Export) writes `data/finished/merged/*.png+.txt`. A user who never ran `curate_dataset.py` gets 0 captions from `scripts/index_rag.py`, and in that case `build_index` returned early **without writing any files**. So the index stayed "missing" forever and R1 kept happening. | `indexer.scan_dataset` used `settings.curated_dir` only; `build_index` returned `{"indexed": 0}` before writing |
| R3 | **Generate also returned a bare 500 for ordinary setup problems**: no `OPENROUTER_API_KEY` in the server env (the key saved in the caption dialog / `localStorage` was never sent), Ollama not running, or an OpenRouter 4xx. Selecting "Local Ollama" with no URL crashed with `AttributeError` (`None.strip()`). | `generator.generate` raised `ValueError` / `RuntimeError` / `httpx` errors, none mapped to an HTTP status |

Contributing problems (fixed as well):
- A rebuilt index was never picked up by a running server: the retriever cached the old one forever.
- Embedding rows were matched to caption rows by position, not by id.
- `is_valid` was always `true`: it checked for `"Prohibited"`, but the warning text is `"prohibited"`.
- The JSON-parse fallback hard-coded `restrained_elegance` instead of the configured trigger, and silently returned junk as if it were valid prompts.
- The SPA catch-all returned `index.html` with **200** for unknown `/api/*` routes. A stale backend serving a new frontend therefore fails with a JSON parse error instead of a 404.

Not causes (checked):
- Route registration and order: the `/api/rag/*` routes are declared before the catch-all.
- `/static` mount and dev proxy: `/api` is proxied.
- Build errors: none.
- Response shape: `/generate` returned exactly `{model, trigger, character, archetypes{a,b,c}, rag_context}`, matching the old frontend model.
- Missing Python deps: numpy and onnxruntime are present. **But `httpx` is only in the `dev` dependency group**, while `pipeline.rag.generator` and `pipeline.openrouter` import it at module level. `uv sync` installs `dev` by default, so this works today; `uv sync --no-dev` would break the whole API at import. Recommend moving `httpx` to `[project].dependencies` (not changed here because `pyproject.toml` was out of scope).

On the user's machine, also make sure the UI is a fresh build. `start_all.sh` rebuilds, but `run.sh` alone serves whatever `src/web/dist` contains. If `dist` is missing it serves the raw `src/web` tree, which is a broken UI.

## 2. Changes made

Backend (`src/pipeline/rag/**`, RAG routes in `api.py`, `scripts/index_rag.py`):
- `rag/indexer.py`
  - `resolve_caption_sources()` uses `data/curated_2500/{train,val}` if it has any captions, else falls back to `<finished_dir>/merged`. It reports the folders it searched.
  - Paths are stored relative to the project root when possible, else absolute.
  - Build progress callback.
  - Single-transaction SQLite rewrite plus a `rag_meta` table (source, count, built_at, embedding model).
  - Atomic NPZ write (temp file + `os.replace`).
  - An empty source leaves the existing index untouched.
  - Accepts an injected classifier.
- `rag/retriever.py`
  - `RagIndexNotReady`, `is_ready()`, `count()`, `invalidate()`.
  - Reloads automatically when the NPZ mtime changes.
  - Matches embeddings to captions by id.
  - **MMR re-ranking** (`diversity` = lambda 0.7, pool = 8 x top_k) plus a hard near-duplicate cutoff (cosine > 0.98), so identical captions from neighbouring frames are not returned twice. This was seen in the e2e run and is covered by a test.
- `rag/index_job.py` (new) runs one background index build in a daemon thread and reports `status()` (index meta, source folder + caption count, build state/progress/step/error).
  - It does **not** use `JobManager`: those jobs are video-centric. The frontend `JobEventService` would toast "Extraction Complete" and call the video reload callback with `video_id=None`, and changing `jobs.py` / `job-event.service.ts` was outside this scope. The UI polls `/api/rag/status` every 1 s while a build runs.
- `rag/generator.py`
  - `RagUpstreamError` for OpenRouter/Ollama failures, with readable messages ("Local model at ... is unreachable. Is Ollama running with gemma2:2b pulled?").
  - New response fields `grounded: bool` and `notices: string[]` ("not grounded", "model did not return valid JSON").
  - Fixed the `local_url=None` crash; the fallback now uses the configured trigger.
- `rag/linter.py`: `is_valid` now really turns false when a prohibited term was replaced.
- `api.py`
  - New `GET /api/rag/status` (adds `openrouter_key_configured`) and `POST /api/rag/index` (409 if a build is already running).
  - `/retrieve` returns `{results: [], index_ready: false}` instead of a 500.
  - `/generate` works without an index (ungrounded, flagged). It takes the key from the request `api_key`, else user settings `openrouter_api_key`, else `OPENROUTER_API_KEY`. Errors: 400 for a missing key, 502 for upstream failures.
  - `top_k` is limited to 1..12.
  - The retriever and index job share `jobs.classifier`, so only one CLIP text model sits in memory.
  - The catch-all now returns a JSON 404 for `api/*`. This is a 3-line change outside the RAG block, needed so `/api/rag/status` can't be swallowed.
- `scripts/index_rag.py`: prints the resolved source, shows progress, explains when nothing was found, and exits 1 when nothing was indexed.

Frontend:
- `core/models/prompt-rag.model.ts`: added `RagIndexStatus`, `RagRetrieveResponse`, `grounded`/`notices`, `api_key`, and the optional retrieve-only exemplar fields (`split`, domain texts). It now matches all `/api/rag/*` shapes.
- `core/services/prompt-rag.service.ts`
  - `indexStatus`, `isIndexReady`, `isIndexBuilding`.
  - `loadStatus()`, `buildIndex()`, polling with cleanup.
  - Sends the caption dialog's saved key (`cp_openrouter_api_key`).
  - Refreshes the status after an ungrounded retrieve.
- `features/prompt-clicker/prompt-clicker-view.component.ts` (rewritten; duplicated template blocks replaced by loops, which frees room for the new states at roughly the same file length):
  - Guided index panel (`hlmAlert`): missing with N captions found, plus a **Build index** button and the WSL command; nothing found, with the searched folders; building, with `hlmProgress` and step text; failed, with the error; backend-unavailable state.
  - The header line shows the real count and source (it used to be hard-coded "2,500 curated image captions").
  - Categories, characters and archetypes are data-driven `@for` loops. They used to be 4 copy-pasted category cards, hard-coded Sara/Lene buttons and 3 copy-pasted archetype cards.
  - The recommended format B is listed first.
  - Lucide icons replace all emoji (including the taxonomy `icon` and character `avatar` fields).
  - `hlm-native-select`, `hlmInput`, `hlmTextarea`, `hlmBadge` (`success`/`warning`/`muted`), `hlmAlert`, `hlmProgress`. No `FormsModule`/`ngModel`.
  - Tokens only (removed `bg-black/40`); type scale classes instead of `text-[Npx]`.
  - Page H1 in the signature style; root `select-none` removed so captions and prompts can be selected.
  - "Grounded / Not grounded" badge and notices; preview fallback icon when an image 404s; matched tags show labels (max 4 + "+N").
  - **Ctrl/Cmd+Enter** generates.
  - The Ollama URL input appears when "Local Ollama" is selected (the signal existed but had no UI).
  - Clipboard errors are surfaced; `aria-pressed`/`role=radio`/`aria-live` added.
- `app.component.ts` and `header.component.ts` were **not** changed (the tab was already wired; the header emoji belong to the design track).

Tests: new `tests/test_rag_api.py` (11 tests, temp dirs plus a deterministic fake embedder, no network, no lifespan/library scan). It covers:
- source resolution
- build + retrieve, MMR vs. pure relevance, reload after rebuild, duplicate suppression with back-fill
- an empty source keeping the old index
- status/retrieve/preview without an index
- the build-error message
- build via the API, then retrieve and preview
- the `/generate` shape matching the frontend model
- the 400/502 error mapping
- the JSON 404 for unknown API routes
- linter `is_valid`

Results here:
- `ng build --configuration production`: success, 0 warnings.
- `pytest tests/`: **48 passed, 1 failed**. The failure is `tests/test_phase1.py::test_api_routes`, which asserts that videos exist in `/mnt/c/Users/Patri/Videos/_girls` (not present on this Mac). It fails the same way before these changes and is unrelated.
- E2E in headless Chrome:
  - empty-state panel rendered
  - "Build index" ran 40 synthetic captions through the real CLIP ONNX text model with live progress
  - "Preview exemplars" returned 4 diverse hits with thumbnails
  - Generate (fake Ollama) rendered B/A/C with lint warnings
  - no console errors; light and dark themes both checked

## 3. How to verify on the Windows machine (WSL)

```bash
# from Windows: wsl bash -lc "<command>"; inside WSL:
cd /mnt/c/Users/Patri/Documents/captioning_pipeline
bash scripts/setup.sh                      # uv sync (includes httpx via the dev group)
npm install                                # once, inside WSL
bash scripts/test.sh tests/test_rag.py tests/test_rag_api.py
bash scripts/start_all.sh                  # builds the UI, then serves :8000
```

1. Open `http://localhost:8000` and go to **Prompt Director**. You should see the guided panel: "Found N captions in the exported dataset (finished/merged)" (or the curated split), with **Build index**.
2. Click **Build index** and watch the progress bar. The first run downloads the CLIP text model (`Xenova/clip-vit-base-patch32` quantized) into `~/.cache/captioning_pipeline/models`. On success the header changes to "Grounded in N captions ...".
3. Optional CLI equivalent: `source scripts/env.sh && uv run python scripts/index_rag.py`. A running server reloads the new index automatically.
4. Pick a preset, then **Preview exemplars**. You should get 4 varied exemplars with thumbnails. Then **Generate prompts** or press Ctrl+Enter.
   - With no key configured you get the readable 400 message. Add `OPENROUTER_API_KEY=...` to `.env` (sourced by `env.sh`), or save the key once in the caption dialog.
   - With **Local Ollama**, set the URL. From WSL, an Ollama running on Windows is usually reachable at `http://localhost:11434` with mirrored networking, otherwise at the Windows host IP.
5. API smoke test: `curl -s localhost:8000/api/rag/status | python3 -m json.tool`.

## 4. Design and workflow review

Click budget today (after the fix): preset (1) + Generate (1) + Copy (1) = **3 clicks** to a usable Krea prompt. A custom scene needs about 5-10 tag clicks. That is good for a single prompt; the gaps are in repetition and reuse.

What works: presets as one-click starting points, three archetypes with token estimates and lint warnings, visible grounding exemplars, and character identity as a first-class choice.

Gaps and recommendations, in priority order:
1. **History and favourites.** Every generation is lost on the next click or tab switch (service state only). Persist the last ~50 results with their inputs (tags, description, character, model, trigger, exemplar ids) in SQLite (`rag_history`) via `GET/POST /api/rag/history`, add a star toggle, and allow "restore inputs". This is the biggest workflow win.
2. **Batch variations.** Add a "Generate N" option (3/5/10). Run the call N times with temperature jitter, or with a different MMR seed or exemplar subset per call so variations draw on different exemplars. Show the results as a compact list with per-row copy.
3. **Export prompt lists.** Offer "Copy all B prompts" (newline separated) and download as `.txt`/`.jsonl` (prompt, archetype, inputs, exemplar stems) for Krea batch runs or LoRA validation sets.
4. **Link from the dataset to prompts.** In the Curate lightbox / Dataset view, add "Use as prompt seed". It pre-selects the tags matched from that frame's caption (the indexer already computes `*_tags`) and pins the frame as exemplar 1. The reverse direction also helps: click an exemplar to open it in the lightbox.
5. **Auto-generate on change (opt-in).** A debounced (about 800 ms) auto-preview of exemplars is cheap: retrieval is local, around 10 ms after the model loads. Keep LLM generation explicit, because it costs money.
6. **Per-category quick clear and search.** The equipment category has about 20 chips; add a "clear" link per card and a tag filter box. On narrow screens, collapse categories into an accordion (`hlm-accordion` is vendored).
7. **Shortcuts.** Ctrl/Cmd+Enter is done. Next: `1/2/3` copy archetype B/A/C when the result pane has focus; `R` reset (with confirm). Register them through `HotkeyService` once F1 lands, so the guard and modal handling are shared.
8. **Model list.** Hard-coded (3 options). Reuse `GET /api/openrouter/models` like the caption dialog should (F7), and remember the last choice in user settings.
9. **Editable prompts.** Let the user tweak a prompt inline and re-lint it (`POST /api/rag/lint`, the linter already exists) before copying.
10. Consistency: the header tabs still use emoji (design track), and the app defaults to dark (`index.html`). The view was checked in both themes.

## 5. RAG review and improvement plan

Current pipeline (verified in code):
- **Corpus**: one record per caption `.txt` (now curated split or merged export). No chunking: each caption is one document.
- **Segmentation**: sentence split, then each sentence goes to one of 4 domains by keyword substring hits, in the fixed priority lighting > surroundings > equipment > position.
- **Tags**: taxonomy keyword substring match per domain text (falling back to the full caption).
- **Embeddings**: CLIP ViT-B/32 **text** encoder (quantized ONNX), one 512-d vector per **full caption**.
- **Retrieval**: weighted tag overlap per category (1.5 / 1.0 / 1.2 / 0.8) blended with the dense cosine of the query text (description + selected tag labels). Blend is 0.55/0.45 (tags + text), 0.75/0.25 (tags only), or dense only. Then (new) MMR + duplicate cutoff. Brute-force numpy over all vectors, which is fine at this scale.
- **Generation**: one LLM call (OpenRouter Gemma 4 31B or Ollama gemma2:2b) with a long system prompt, top-4 full captions as context, and JSON mode producing archetypes A/B/C. Then regex lint (strip sludge/brackets/preamble, adult-term guardrail, Token 0 trigger, token budget) using a word-count heuristic.

Weaknesses and concrete fixes:

| # | Issue | Recommendation | Effort |
| --- | --- | --- | --- |
| 1 | **CLIP's text encoder truncates at 77 tokens.** Dense captions are 150-300 words, so the vector encodes only the first ~55 words (mostly subject and pose). Equipment, lighting and optics in later sentences never reach the dense score. | Embed the **4 domain segments separately** (an `(N,4,512)` matrix, about 4x the 2.5k texts, a few minutes on CPU). Score each selected category against its own domain vector, and the free-text description against the max over domains. Or switch to a long-context text embedder (`bge-small-en-v1.5`/`gte-small` ONNX, 512 tokens; `nomic-embed-text` via Ollama). | S-M |
| 2 | Lexical side is only taxonomy keywords, and "substring" matching has false positives: `light` hits "slightly", `room` hits "broom", `arms` hits "harms", `tie` hits "tied/entirely". | Use word-boundary regexes in `segment_caption` and `match_taxonomy_tags`. Add **BM25** (SQLite FTS5 `bm25()` on `full_caption` + domain texts, built into the stdlib sqlite) for the free-text description, and combine with **reciprocal rank fusion** instead of hand-tuned linear weights. | S |
| 3 | No image signal, although every caption has its frame and the pipeline already computes CLIP **image** embeddings for frames. | Store the frame's image embedding (from `state.db` or recomputed) next to the caption. Enables (a) text-to-image retrieval for the description ("what it looks like" rather than "how it was described"), (b) "more like this frame" from the Curate view, and (c) better de-duplication. SigLIP (`google/siglip-base-patch16-224`, ONNX) is a stronger zero-shot text-image model than ViT-B/32. | M |
| 4 | Diversity: neighbouring frames of one video have near-identical captions. | Done: MMR + 0.98 duplicate cutoff. Next: cap exemplars per source video (stem prefix or the `video_id` from the manifest), e.g. at most 2. | S |
| 5 | No metadata filters. | Filter or boost by split (val exemplars for evaluation only), source video, character (exemplars tagged with the same LoRA identity), word count, and caption mode (concept/character/style from the manifest). Add `filters` to the request. | S |
| 6 | Indexing is a manual, full rebuild. | **Incremental reindex on Export / caption-job finish**: upsert by stem, embed only new or changed captions (hash the text), delete missing ones. Hook in `Exporter.export_video` or the `job_finished` path, or trigger `rag_index_job.start()` (debounced). Build status is already exposed. | M |
| 7 | Query embedding not cached; first query pays the ONNX load (about 1-2 s). | Warm the text model in `lifespan` (background thread) and LRU-cache query embeddings by text. | XS |
| 8 | Generation prompt issues. The system prompt demands "Adult subjects only (25+)" while both characters are specified as 21 years old (adults, but the instructions contradict), so the model may rewrite the character's age or ignore the character spec. The generator sends full captions (about 1.2k tokens) rather than the domain excerpts that match the selection. `max_tokens=800` can truncate archetype C plus JSON. | Make the age rule consistent with the character profiles (a product decision: keep a clear adult-only rule and state the characters' adult age explicitly). Pass only the matching domain excerpts plus 1 full caption as a style anchor. Raise `max_tokens` to about 1200. Use the OpenRouter `json_schema` response format, with a single retry on parse failure instead of the silent fallback. | S |
| 9 | Token estimator is a word heuristic. Krea 2 uses a Qwen3-VL text encoder. | Count with the Qwen tokenizer via `tokenizers` (already a dependency) from `Qwen/Qwen3-VL-*` `tokenizer.json`. | XS |
| 10 | No evaluation. | Build a small eval set: about 30 tag selections, each with expected exemplar stems/tags, drawn from the **val** split. Track recall@4 / tag-precision@4 and lint pass rate per change. Add a pytest that runs it on a frozen fixture index. Log generation inputs and outputs (see history) so prompts can be rated later. | M |

Suggested order: 7 + 2 (word boundaries) + 8 (cheap, immediate quality), then 1 (domain embeddings), 6 (incremental reindex), 10 (eval), then 3 (image embeddings / SigLIP).

## 6. Follow-ups outside this scope

- Move `httpx` from the `dev` group to runtime dependencies in `pyproject.toml`.
- Update `.claude/skills/api-contract` (RAG table: new `status`/`index` routes, `/retrieve` `index_ready`, `/generate` `grounded`/`notices`/`api_key` and error codes, catch-all 404 for `api/*`) and `studio-ui-spec` (Prompt Director section).
- `CHARACTERS["lene"]["avatar"]` is a child emoji; it is no longer rendered, but replace or drop the field for consistency with the adult-only guardrail.
- `serve_spa` joins `STATIC_DIR / full_path` without a containment check (path traversal outside `src/web/dist`). Resolve and check `is_relative_to(STATIC_DIR)`.
