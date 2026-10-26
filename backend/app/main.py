"""FastAPI Application Factory for Captioning Pipeline & LoRA Dataset Studio (`backend.app.main`)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import router as api_v1_router
from backend.app.config import get_app_settings
from backend.app.db.session import DatabaseManager, get_db, reset_default_db


def create_app(db_path: Path | str | None = None) -> FastAPI:
    """Create and configure the modular FastAPI application."""
    if db_path is not None:
        reset_default_db(DatabaseManager(db_path=db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_app_settings().ensure_dirs()
        get_db()
        yield

    application = FastAPI(
        title="Captioning Pipeline & LoRA Dataset Studio API",
        version="2.0.0",
        description="Zero-Local-ML Video & Image Dataset Curation, 3-Tier OpenRouter Cascade, Rejection-Guard, and Local FTS5/LSA LoRA-RAG Studio",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Canonical v1 API mount + backward-compatible /api alias
    application.include_router(api_v1_router, prefix="/api/v1")
    application.include_router(api_v1_router, prefix="/api")

    return application


app = create_app()
