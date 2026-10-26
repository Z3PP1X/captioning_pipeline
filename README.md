# Krea 2 LoRA Dataset Curation & Captioning Studio

A **local, self-hosted, fully dockerized End-to-End Dataset Curation, VLM Captioning & RAG Studio** engineered for preparing **Krea 2 LoRA** training datasets across two specialized domains: **Character Training** and **Style / Concept Training**.

---

## Key Architectural Highlights

1. **Zero Local Machine Learning Models in Core (`Phase 1` Non-ML Architecture)**:
   - **Stage 1 — Video & Folder Ingestion**: Supports both pre-sorted image folders (`.png`, `.jpg`, `.jpeg`, `.webp` + sidecar `.txt`) and high-speed **FFmpeg** video extraction (`select='gt(scene,0.15)'`, I-Frame `select='eq(pict_type,I)'`, and window-peak interval sampling).
   - **Stage 2 — Mathematical Quality Scoring ($Q \in [0, 1]$)**: Combines 4x4 tile top-3 Laplacian variance $\sigma^2(\nabla^2 I)$ (with Immerkaer sensor noise subtraction) and central-60% **Tenengrad Sobel gradient energy** ($G_x^2 + G_y^2$), chiaroscuro-aware exposure & clipping analysis ($<5$ shadow / $>250$ highlight), and 256-bin **Shannon Entropy** $H(I) = -\sum p_i \log_2(p_i)$ (~151 FPS).
   - **Stage 3 — $\mathcal{O}(N \log N)$ Deduplication & Best-Representative Selection**: Computes 64-bit DCT **pHash**, 64-bit gradient **dHash**, and 24-bin L2-normalized **HSV color histograms** (~670 FPS), grouping near-duplicates via a **Burkhard-Keller Metric Tree (`BKTree`)** and **4-Band 16-Bit Multi-Index Prefix Buckets** (**23x faster** and **726x fewer distance evaluations** at $N=5000$ than naive $\mathcal{O}(N^2)$) and automatically selecting $i^* = \arg\max_{i \in C_k} Q(I_i)$.
2. **Future-Proof ML-Plugin Architecture (`Strategy Pattern`)**:
   - Clean abstract interfaces [`BaseTaggerPlugin`](file:///home/z3pp1x/code/captioning_pipeline/backend/app/modules/curation/interfaces.py) and [`DatasetStratifier`](file:///home/z3pp1x/code/captioning_pipeline/backend/app/modules/curation/interfaces.py).
   - Ships with [`RuleBasedHeuristicPlugin`](file:///home/z3pp1x/code/captioning_pipeline/backend/app/modules/curation/rule_tagger.py) and [`RuleBasedStratifier`](file:///home/z3pp1x/code/captioning_pipeline/backend/app/modules/curation/stratifier.py) out of the box, ready for drop-in local Vision-ML taggers (WD14, CLIP, SigLIP) later.
3. **OpenRouter 3-Tier Cascade & Deterministic `RejectionDetector`**:
   - Configurable **Primary Model** $\rightarrow$ **Fallback Model 1** $\rightarrow$ **Fallback Model 2** with persistent API key storage, `max_tokens`, and `temperature` $\in [0.0, 2.0]$.
   - Versionable Master Skills for **Krea 2 Character Training** (identity decoupling onto `trigger_word`) and **Krea 2 Concept / Style Training** (content-style separation).
   - Every response is audited by [`RejectionDetector`](file:///home/z3pp1x/code/captioning_pipeline/backend/app/modules/captioning/rejection_guard.py) (empty/min-length $<15$, normalized first-200-char refusal regex, meta-talk/question detection) with automatic cascade escalation and `"Needs Manual Caption"` fallback status.
4. **Standardized Dataset Exporter & Local Non-ML LoRA-RAG Pipeline**:
   - Exports normalized `.png`/`.jpg` images (Lanczos-4 clamping, multiple-of-16 latent snapping), paired `.txt` captions, `metadata.jsonl`, `dataset_card.json`, and downloadable `.zip` archives.
   - Indexes curated datasets into **SQLite FTS5 (BM25)** + **TF-IDF / TruncatedSVD (128-D LSA)** / optional OpenRouter Embeddings with Reciprocal Rank Fusion (RRF) and MMR to power the **LoRA Prompt Studio**.
5. **Docker & WSL2 Bind-Mount Optimization**:
   - Separates host media bind-mounts (`/data/input:ro`, `/data/exports:rw`) from the native Linux ext4 Docker volume (`pipeline_appdata:/var/lib/captioning_pipeline`) for SQLite WAL, thumbnails, and extracted frames to eliminate the Windows `/mnt/c` 9P bottleneck.

---

## Quickstart (Docker Compose & WSL2)

```bash
cp .env.example .env
# Edit HOST_MEDIA_ROOT in .env to point to your WSL2 / Windows media folder
docker compose up --build -d
```

- **Angular 22 HITL Studio UI**: `http://localhost:4200`
- **FastAPI Backend & OpenAPI Docs**: `http://localhost:8000/docs`

---

## Documentation & Benchmarks

- [Architecture Blueprint (`ARCHITECTURE.md`)](file:///home/z3pp1x/code/captioning_pipeline/ARCHITECTURE.md)
- [Classical Vision & $\mathcal{O}(N \log N)$ Clustering Benchmark Report (`docs/BENCHMARK_REPORT.md`)](file:///home/z3pp1x/code/captioning_pipeline/docs/BENCHMARK_REPORT.md)
- [HITL UI/UX Design Specification (`docs/HITL_UX_DESIGN.md`)](file:///home/z3pp1x/code/captioning_pipeline/docs/HITL_UX_DESIGN.md)
