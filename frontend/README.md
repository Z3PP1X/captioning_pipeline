# LoRA Dataset & Captioning Studio — Angular 22 Frontend (`frontend/` & `src/web/`)

The Angular 22 frontend is built with **Standalone Components**, **Zoneless Signals** (`signal`, `computed`, `effect`), **`ChangeDetectionStrategy.OnPush`**, and **TailwindCSS v4**.

## Directory Structure

- `src/web/app/app.component.ts` — 5-Step Guided Workflow Stepper shell, Active Dataset Selector, Live SSE Progress Bar, Keyboard Cheat-Sheet (`?`), and System Settings trigger.
- `src/web/app/models/studio.models.ts` — Canonical TypeScript DTOs for `/api/v1/*`.
- `src/web/app/services/studio-api.service.ts` — Reactive HTTP & Real-Time SSE/WebSocket Telemetry Service.
- `src/web/app/pages/step1-ingest/step1-ingest.component.ts` — Step 1: Dual-Path Ingest (`image_folder` & `video` via FFmpeg scene/iframe/interval + pHash clustering).
- `src/web/app/pages/step2-triage/step2-triage.component.ts` — Step 2: Zero-Friction Keyboard-First Triage (`Arrow` keys, `Space`, `X`, `S`, `1–9` quick-tags, `R` re-caption, `C` Near-Duplicate Cluster comparison drawer).
- `src/web/app/pages/step3-tagging/step3-tagging.component.ts` — Step 3: Rule-Based CV Auto-Tagger (`BaseTaggerPlugin`), Category Quick-Tagger, Visual Distribution Histograms & MMR Auto-Stratification.
- `src/web/app/pages/step4-caption/step4-caption.component.ts` — Step 4: Live Split-View Caption Editor, Triggerword Highlighting, Word-Level Diff View, `Needs Manual Caption` queue, and Rejection-Guard Audit Inspector.
- `src/web/app/pages/step5-export-rag/step5-export-rag.component.ts` — Step 5: Standardized Dataset Exporter (`.png`/`.jpg`, multiple-of-16 grid snap, `metadata.jsonl`, `dataset_card.json`, `.zip`) & Local Non-ML LoRA Prompt Studio (Hybrid FTS5 BM25 + TF-IDF/SVD 128-D LSA RAG).
- `src/web/app/pages/settings-skills/settings-skills-modal.component.ts` — System Settings & Versionable Krea 2 Master Skill Manager.
