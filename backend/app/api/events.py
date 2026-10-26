"""Real-Time Telemetry Stream Manager for Server-Sent Events (SSE) and WebSockets (Section 5.7)."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import threading
import time
from typing import Any, AsyncIterator


class EventBus:
    """Thread-safe event broadcaster supporting both SSE (`GET /api/v1/events/stream`) and WebSockets (`/api/v1/ws`)."""

    def __init__(self, max_history: int = 100) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)
        self._job_counter: int = 0

    def next_job_id(self) -> int:
        with self._lock:
            self._job_counter += 1
            return self._job_counter

    def publish(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "type": event_type,
            "timestamp": round(time.time(), 3),
            "data": data,
        }
        with self._lock:
            self._history.append(payload)
            subs = list(self._subscribers)

        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._safe_put, queue, payload)
            except RuntimeError:
                pass
        return payload

    @staticmethod
    def _safe_put(queue: asyncio.Queue[dict[str, Any]], payload: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        return items[-max(1, limit) :]

    async def sse_stream(
        self,
        *,
        heartbeat_interval: float = 15.0,
        max_events: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield Server-Sent Events formatted lines with periodic `: keepalive` heartbeats."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        sub_entry = (loop, queue)
        with self._lock:
            self._subscribers.append(sub_entry)

        emitted = 0
        try:
            # Emit initial connected event so SSE clients immediately confirm connection
            init_evt = {
                "type": "connected",
                "timestamp": round(time.time(), 3),
                "data": {"status": "streaming"},
            }
            yield f"data: {json.dumps(init_evt)}\n\n"
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return

            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                    yield f"data: {json.dumps(evt)}\n\n"
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    if max_events is not None and emitted >= max_events:
                        return
        finally:
            with self._lock:
                if sub_entry in self._subscribers:
                    self._subscribers.remove(sub_entry)


_DEFAULT_EVENT_BUS = EventBus()


def get_event_bus() -> EventBus:
    return _DEFAULT_EVENT_BUS
