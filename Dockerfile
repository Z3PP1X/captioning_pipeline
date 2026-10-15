# Multi-stage Docker build for Captioning Pipeline
# Stage 1: Build the Angular frontend
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY angular.json tsconfig.json tsconfig.app.json .postcssrc.json components.json ./
COPY src/web/ src/web/

RUN npm run build

# Stage 2: Python runtime with FFmpeg, UV, and pre-cached ONNX models
FROM python:3.12-slim-bookworm AS runtime

# Install system dependencies (FFmpeg for video decode, libgl for OpenCV, curl for healthchecks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    ca-certificates \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

WORKDIR /app

# Install Python dependencies using uv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Copy Python backend code
COPY src/pipeline src/pipeline
RUN uv sync --frozen

# Pre-download and embed the ONNX CLIP vision & text models and tokenizer
COPY scripts/download_models.py scripts/download_models.py
RUN uv run python scripts/download_models.py /app/models

# Copy built frontend assets from Stage 1
COPY --from=frontend-builder /app/src/web/dist /app/src/web/dist

# Set up standard mount directories and symlinks
RUN mkdir -p /videos /cache /finished /app/data /root/.cache && \
    ln -s /cache /root/.cache/captioning_pipeline && \
    mkdir -p /mnt/c/Users/Patri/Videos/_girls /mnt/c/Users/Patri/Documents/captioning_pipeline/data/finished

# Environment configuration defaults
ENV CP_VIDEO_DIR=/videos \
    CP_CACHE_DIR=/cache \
    CP_FINISHED_DIR=/finished \
    CP_MODELS_DIR=/app/models

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/videos || exit 1

CMD ["uvicorn", "pipeline.api:app", "--host", "0.0.0.0", "--port", "8000"]
