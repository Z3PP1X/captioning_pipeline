# Captioning Pipeline

A high-throughput video processing, frame extraction, quality scoring, curation, and VLM captioning pipeline designed for building high-quality LoRA and generative model training datasets.

---

## Features

- **Video Ingestion & Indexing**: Scans directories, extracts metadata, builds lightweight proxies, and indexes videos into SQLite.
- **Scene Chunking & Budgeting**: Auto-segments long videos into balanced chunks with per-scene frame sampling budgets.
- **Frame Extraction & Metric Scoring**: Fast PyAV video decoding with sharpness, motion, blur, and aesthetic metrics.
- **Quality Control & ONNX Classification**: Built-in ONNX Runtime (CPU) with `Xenova/clip-vit-base-patch32` for automated zero-shot visual scoring and MMR diversification.
- **Vision-Language Captioning**: Batch and streaming caption generation via OpenRouter API targeting Google Gemma 4 31B IT (with fallback cascade to DeepSeek and Grok).
- **Modern Angular Studio UI**: Angular 22 + Tailwind v4 + Spartan UI interface with keyboard shortcuts, lightbox comparison, candidate swapper, pipeline board, and Prompt Director.
- **Dataset Export**: Exports curated frames and paired caption text directly into AI-Toolkit format for LoRA training.
- **Full Docker & Docker Compose Support**: Production multi-stage Docker build with embedded ONNX models and host volume mounts.

---

## Docker Quickstart (Recommended)

The easiest way to run the entire application (Angular frontend + FastAPI backend + FFmpeg + ONNX models) is via Docker Compose.

### 1. Configure Environment

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your settings:
```env
# OpenRouter API Key for VLM batch captioning
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Video source folder on host
# Under WSL 2: /mnt/c/Users/Patri/Videos/_girls
# Under Windows Docker Desktop: C:\Users\Patri\Videos\_girls
VIDEO_DIR=/mnt/c/Users/Patri/Videos/_girls

# Persistent cache & database (state.db, previews, filmstrips)
CACHE_DIR=/home/z3pp1x/.cache/captioning_pipeline

# Finished dataset export destination
FINISHED_DIR=/mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished

# Port for the web interface and API
PORT=8000
```

### 2. Build & Launch

```bash
docker compose build
docker compose up -d
```

The container starts with:
- Video folder mounted read-only (`:ro`).
- Persistent cache mounted for database and preview images.
- Pre-cached ONNX models (`Xenova/clip-vit-base-patch32`) embedded directly into `/app/models` inside the image (no runtime model downloads required).
- Healthcheck monitoring `http://localhost:8000/api/videos`.

Open your browser at `http://localhost:8000`.

### 3. Container Management

```bash
# View live logs
docker compose logs -f

# Check container health status
docker compose ps

# Stop container
docker compose down
```

---

## Database Management & Reset

To reset the SQLite state database:

```bash
# Creates a timestamped backup and initializes a fresh database schema:
scripts/reset_db.sh

# Or also purge cached candidates, previews, and filmstrips:
scripts/reset_db.sh --purge-all
```

---

## Architecture & Directory Layout

```
captioning_pipeline/
├── Dockerfile                  # Multi-stage build (Node 22 Angular + Python 3.12 + FFmpeg + ONNX)
├── docker-compose.yml          # Container configuration with host volume mounts
├── .env.example                # Template environment variables
├── src/
│   ├── pipeline/               # Python backend modules
│   │   ├── api.py              # FastAPI server & REST/SSE endpoints
│   │   ├── autochunk.py        # Video chunking & scene budgeting
│   │   ├── autoprocess.py      # Automated processing pipeline
│   │   ├── classifier.py       # ONNX CLIP classification & embeddings
│   │   ├── config.py           # Settings & environment configuration
│   │   ├── db.py               # SQLite storage & transactions
│   │   ├── decode.py           # Video frame decoding via PyAV
│   │   ├── export.py           # LoRA dataset export
│   │   ├── indexer.py          # Video library scanner
│   │   ├── jobs.py             # Job queue & background workers
│   │   ├── metrics.py          # Sharpness, motion & visual metrics
│   │   ├── openrouter.py       # OpenRouter VLM integration
│   │   ├── rag/                # RAG prompt taxonomy and generator
│   │   ├── scoring.py          # Frame scoring, MMR selection & distribution stats
│   │   ├── select.py           # Frame sampling algorithms
│   │   ├── streaming.py        # Video proxy & streaming
│   │   └── tagger.py           # Tagging integration
│   └── web/                    # Angular 22 frontend
│       ├── app/                # Feature components & services
│       ├── shared/             # Spartan UI (hlm/brain) components
│       └── dist/               # Built static production bundle
├── scripts/                    # CLI & automation scripts
│   ├── download_models.py      # Pre-download ONNX CLIP models
│   ├── reset_db.sh             # Database backup & reset script
│   ├── env.sh                  # WSL environment definitions
│   ├── run.sh                  # Start local FastAPI server
│   ├── setup.sh                # Install dependencies with uv
│   └── test.sh                 # Run pytest suite
├── tests/                      # Pytest backend test suite
└── pyproject.toml              # Project dependencies & package metadata
```

---

## Local Development (WSL 2)

If developing outside Docker inside WSL 2:

```bash
# 1. Setup Python & Node dependencies
bash scripts/setup.sh

# 2. Run backend and frontend tests
bash scripts/test.sh
npm test

# 3. Start local development servers
bash scripts/start_all.sh
```
