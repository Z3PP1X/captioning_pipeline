"""Background RAG index build with pollable status (used by /api/rag/status and /api/rag/index).

Full builds and R6 incremental updates share one worker: an incremental request that arrives while a
build is running is queued and runs right after it (see ``request_incremental``).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Callable

from pipeline.classifier import ClipClassifier
from pipeline.config import Settings, get_settings
from pipeline.rag.image_embedder import default_image_embedder
from pipeline.rag.indexer import _UNSET, RagCaptionIndexer, rag_index_paths, resolve_caption_sources

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
        image_embedder: Any = _UNSET,
    ):
        """``image_embedder``: R3 embedder shared with ``GET /api/rag/similar`` (default: SigLIP, else CLIP, for
        the real classifier; the classifier's ``embed_images`` for stand-ins; None = no image index)."""
        self.settings = settings or get_settings()
        self.classifier = classifier
        if image_embedder is _UNSET and classifier is not None:
            image_embedder = default_image_embedder(self.settings, classifier)
        self.image_embedder = image_embedder  # still _UNSET without a classifier: the indexer picks its own
        self.on_finished = on_finished
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending_incremental = False
        self._state: dict[str, Any] = {
            "state": "idle",  # idle | running | done | error
            "mode": None,  # full | incremental (requested mode; result.mode says what actually ran)
            "pending": False,  # an incremental update is queued behind the running build
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
            return {**self._state, "pending": self._pending_incremental}

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
            "updated_at": float(meta["updated_at"]) if meta.get("updated_at") else None,
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
    def start(self, limit: int | None = None, incremental: bool = False) -> bool:
        """Start a build in the background. Returns False if one is already running.

        ``incremental=True`` embeds only new/changed captions (falls back to a full build when the index
        is missing or incompatible); ``limit`` only applies to full builds.
        """
        with self._lock:
            if self._state["state"] == "running":
                return False
            self._state = {
                "state": "running",
                "mode": "incremental" if incremental else "full",
                "pending": False,
                "progress": 0.0,
                "step": "Starting",
                "error": None,
                "result": None,
                "started_at": time.time(),
                "finished_at": None,
            }
        self._thread = threading.Thread(
            target=self._run, args=(None if incremental else limit, incremental), name="rag_index_build", daemon=True
        )
        self._thread.start()
        return True

    def request_incremental(self) -> bool:
        """Incremental update now, or queued behind the running build (one queued update covers any number
        of requests). Used by the auto-reindex hook after caption jobs. True when started or queued."""
        if self.start(incremental=True):
            return True
        with self._lock:
            if self._state["state"] == "running":
                self._pending_incremental = True
                return True
        return self.start(incremental=True)  # the build finished in between

    def _progress(self, frac: float, step: str) -> None:
        with self._lock:
            self._state["progress"] = round(max(0.0, min(1.0, frac)), 3)
            self._state["step"] = step

    def _finish(self, **updates: Any) -> bool:
        """Set the final state and hand over a queued incremental request atomically; True = run it next."""
        with self._lock:
            self._state.update(updates, finished_at=time.time())
            rerun, self._pending_incremental = self._pending_incremental, False
        return rerun

    def _run(self, limit: int | None, incremental: bool = False) -> None:
        rerun = False
        try:
            indexer = RagCaptionIndexer(self.settings, classifier=self.classifier, image_embedder=self.image_embedder)
            result = indexer.build_index(limit=limit, progress_cb=self._progress, incremental=incremental)
            count = result.get("count", result.get("indexed", 0))
            if not count:
                searched = ", ".join(result.get("searched") or []) or "dataset folders"
                rerun = self._finish(
                    state="error",
                    result=result,
                    error=(
                        f"No caption .txt files found and no vlm/imported captions in the captions table "
                        f"(searched: {searched}). Caption and export frames first, optionally run "
                        "scripts/curate_dataset.py."
                    ),
                )
            else:
                if self.on_finished:  # before 'done', so a poller never sees 'done' with a stale retriever
                    self.on_finished(result)
                rerun = self._finish(state="done", progress=1.0, result=result)
        except Exception as err:  # surfaced to the UI via status()
            logger.exception("RAG index build failed")
            rerun = self._finish(state="error", error=f"{type(err).__name__}: {err}")
        if rerun:
            self.start(incremental=True)
