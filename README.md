# Captioning Pipeline

A high-throughput video processing, frame extraction, quality scoring, curation, and VLM captioning pipeline designed for building high-quality LoRA and generative model training datasets.

---

## Features

- **Video Ingestion & Indexing**: Scans directories, extracts metadata, builds lightweight proxies, and indexes videos into SQLite.
- **Scene Chunking & Budgeting**: Auto-segments long videos into balanced chunks with per-scene frame sampling budgets.
- **Frame Extraction & Metric Scoring**: Fast PyAV video decoding with sharpness, motion, blur, and aesthetic metrics.
- **Quality Control & Tagging**: ONNX-based classifier / tagger integration (WD14 / Danbooru) for automated content filtering.
- **Vision-Language Captioning**: Batch and streaming caption generation via OpenRouter API with configurable VLM models.
- **Interactive Web UI**: FastAPI + static Web UI for human curation, frame rating, rejecting bad samples, and inspecting captions.
- **Dataset Export**: Exports curated frames and paired caption text directly into AI-Toolkit format for LoRA training.

---

## Architecture & Directory Layout

```
captioning_pipeline/
├── src/
│   ├── pipeline/               # Core pipeline modules
│   │   ├── api.py              # FastAPI server & endpoints
│   │   ├── autochunk.py        # Video chunking & scene budgeting
│   │   ├── autoprocess.py      # Automated processing pipeline
│   │   ├── classifier.py       # Frame classification
│   │   ├── config.py           # Settings & environment configuration
│   │   ├── db.py               # SQLite storage & transactions
│   │   ├── decode.py           # Video frame decoding via PyAV
│   │   ├── export.py           # LoRA dataset export
│   │   ├── indexer.py          # Video library scanner
│   │   ├── jobs.py             # Job queue & status tracking
│   │   ├── metrics.py          # Sharpness, motion & visual metrics
│   │   ├── openrouter.py       # OpenRouter VLM integration
│   │   ├── scoring.py          # Frame scoring & selection
│   │   ├── select.py           # Frame sampling algorithms
│   │   ├── streaming.py        # Video proxy & streaming
│   │   └── tagger.py           # ONNX tagging integration
│   └── web/                    # Curation web interface
│       ├── app.js
│       ├── index.html
│       └── styles.css
├── scripts/                    # CLI & automation scripts
│   ├── env.sh                  # WSL environment definitions
│   ├── run.sh                  # Start FastAPI server
│   ├── setup.sh                # Install dependencies with uv
│   └── test.sh                 # Run pytest suite
├── tests/                      # Pytest test suite
├── pyproject.toml              # Project dependencies & package metadata
└── run.bat                     # Windows WSL launcher helper
```

---

## Prerequisites

- **WSL 2 (Ubuntu)**: All processing runs inside WSL 2 Linux.
- **Python 3.12**: Managed via `uv`.
- **OpenRouter API Key** (optional, for VLM captioning).

---

## Getting Started

### 1. Setup Environment

Inside WSL 2:
```bash
bash scripts/setup.sh
```

### 2. Configure API Keys

Copy the sample environment file:
```bash
cp .env.example .env
```
Edit `.env` and set your `OPENROUTER_API_KEY`.

### 3. Start the Web UI & API Server

```bash
bash scripts/run.sh
```
Or double-click `run.bat` from Windows.

Navigate to `http://localhost:8000` in your web browser.

### 4. Running Tests

```bash
bash scripts/test.sh
```
