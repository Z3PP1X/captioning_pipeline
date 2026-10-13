"""Background RAG index build with pollable status (used by /api/rag/status and /api/rag/index)."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Callable

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings
from pipeline.rag.indexer import RagCaptionIndexer, rag_index_paths, resolve_caption_sources

logger = logging.getLogger(__name__)


class RagIndexJob:
    """Runs one index build at a time in a daemon thread and exposes its progress.

    Deliberately separate from ``JobManager``: those jobs (and the frontend job pills/toasts)
    are video-centric, while the RAG index spans the whole dataset.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        classifier: ClipClassifier | None = None,
        on_finished: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.settings = settings or get_settings()
        self.classifier = classifier
        self.on_finished = on_finished
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {
            "state": "idle",  # idle | running | done | error
            "progress": 0.0,
            "step": "",
            "error": None,
            "result": None,
            "started_at": None,
            "finished_at": None,
        }

    # ------------------------------------------------------------------ status
    @property
    def running(self) -> bool:
        return self._state["state"] == "running"

    def build_state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def index_meta(self) -> dict[str, Any]:
        """Metadata of the index on disk (source, count, built_at); empty dict when missing."""
        db_path, npz_path = rag_index_paths(self.settings)
        if not db_path.exists() or not npz_path.exists():
            return {}
        try:
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM rag_captions").fetchone()[0]
                meta: dict[str, Any] = {}
                has_meta = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='rag_meta'"
                ).fetchone()
                if has_meta:
                    meta = {k: v for k, v in conn.execute("SELECT key, value FROM rag_meta")}
        except sqlite3.Error as err:
            logger.warning("Cannot read RAG index metadata: %s", err)
            return {}
        built_at = meta.get("built_at")
        return {
            "count": int(count),
            "source": meta.get("source") or None,
            "source_path": meta.get("source_path") or None,
            "built_at": float(built_at) if built_at else npz_path.stat().st_mtime,
            "embedding_model": meta.get("embedding_model") or None,
        }

    def status(self) -> dict[str, Any]:
        """Combined index + build status for the UI."""
        meta = self.index_meta()
        build = self.build_state()
        # Counting caption files walks the dataset folders; skip it while a build is running.
        sources = None if build["state"] == "running" else resolve_caption_sources(self.settings)
        return {
            "ready": bool(meta) and meta.get("count", 0) > 0,
            "indexed": meta.get("count", 0),
            "index": meta or None,
            "sources": None if sources is None else {
                "source": sources["source"],
                "path": sources["path"],
                "caption_files": sources["caption_files"],
                "searched": sources.get("searched", []),
            },
            "build": build,
            "build_command": "source scripts/env.sh && uv run python scripts/index_rag.py",
        }

    # ------------------------------------------------------------------ build
    def start(self, limit: int | None = None) -> bool:
        """Start a build in the background. Returns False if one is already running."""
        with self._lock:
            if self._state["state"] == "running":
                return False
            self._state = {
                "state": "running",
                "progress": 0.0,
                "step": "Starting",
                "error": None,
                "result": None,
                "started_at": time.time(),
                "finished_at": None,
            }
        self._thread = threading.Thread(target=self._run, args=(limit,), name="rag_index_build", daemon=True)
        self._thread.start()
        return True

    def _progress(self, frac: float, step: str) -> None:
        with self._lock:
            self._state["progress"] = round(max(0.0, min(1.0, frac)), 3)
            self._state["step"] = step

    def _run(self, limit: int | None) -> None:
        try:
            indexer = RagCaptionIndexer(self.settings, classifier=self.classifier)
            result = indexer.build_index(limit=limit, progress_cb=self._progress)
            with self._lock:
                if result.get("indexed", 0) == 0:
                    self._state["state"] = "error"
                    searched = ", ".join(result.get("searched") or []) or "dataset folders"
                    self._state["error"] = (
                        f"No caption .txt files found (searched: {searched}). "
                        "Caption and export frames first, optionally run scripts/curate_dataset.py."
                    )
                else:
                    self._state["state"] = "done"
                    self._state["progress"] = 1.0
                self._state["result"] = result
                self._state["finished_at"] = time.time()
            if result.get("indexed", 0) and self.on_finished:
                self.on_finished(result)
        except Exception as err:  # surfaced to the UI via status()
            logger.exception("RAG index build failed")
            with self._lock:
                self._state["state"] = "error"
                self._state["error"] = f"{type(err).__name__}: {err}"
                self._state["finished_at"] = time.time()
