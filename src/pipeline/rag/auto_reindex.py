"""R6: incremental RAG reindex after caption jobs.

``JobManager._run_openrouter_caption_job`` calls ``notify_caption_job_finished(self, ...)`` at the end of
its success path. The API registers a handler for *its* JobManager (``register``), so job managers built by
tests or scripts never trigger an index update. The handler decides (user setting ``rag_auto_reindex``) and
queues ``RagIndexJob.request_incremental`` in the background; nothing here blocks or fails the caption job.
"""
from __future__ import annotations

import logging
import threading
import weakref
from typing import Any, Callable

logger = logging.getLogger(__name__)

Handler = Callable[..., Any]

_lock = threading.Lock()
_handlers: "weakref.WeakKeyDictionary[Any, Handler]" = weakref.WeakKeyDictionary()


def register(manager: Any, handler: Handler) -> None:
    """Call ``handler(job_id=, video_id=, captioned=)`` when ``manager`` finishes a caption job."""
    with _lock:
        _handlers[manager] = handler


def unregister(manager: Any) -> None:
    with _lock:
        _handlers.pop(manager, None)


def notify_caption_job_finished(manager: Any, *, job_id: int, video_id: str, captioned: int = 0) -> bool:
    """Hook for the caption job's success path. Never raises; True when a handler ran."""
    with _lock:
        handler = _handlers.get(manager)
    if handler is None:
        return False
    try:
        handler(job_id=job_id, video_id=video_id, captioned=captioned)
        return True
    except Exception:  # the caption job already finished; a reindex problem must not change that
        logger.exception("RAG auto-reindex hook failed after caption job %s", job_id)
        return False
