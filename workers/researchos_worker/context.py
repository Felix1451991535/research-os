"""TaskContext implementation handed to handlers; written by the handler thread, read by the lease loop."""
from __future__ import annotations

import threading
from typing import Any


class WorkerContext:
    def __init__(self, task_id: str, payload: dict[str, Any], run_id: str = "") -> None:
        self.task_id = task_id
        self.run_id = run_id
        self.payload = payload
        self._cancel = threading.Event()
        self._lost = threading.Event()
        self._lock = threading.Lock()
        self._stage: str | None = None
        self._progress: dict[str, Any] | None = None

    # --- handler side ---
    def progress(self, stage: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._stage, self._progress = stage, data or {}

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def check_lease(self) -> bool:
        return not self._lost.is_set()

    # --- lease-loop side ---
    def take_progress(self) -> tuple[str | None, dict[str, Any] | None]:
        with self._lock:
            out = (self._stage, self._progress)
            self._progress = None
            return out

    def request_cancel(self) -> None:
        self._cancel.set()

    def mark_lost(self) -> None:
        self._lost.set()
