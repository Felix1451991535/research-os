"""K2 time triggers. A trigger inspects the DB at `now` and enqueues tasks with slot-based idempotency keys, so
repeated ticks, several workers or a restart never duplicate a run, and a laptop that slept for days catches up
with ONE run per schedule (missed slots are not replayed). Triggers only enqueue; the work runs as normal tasks."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime

from .registry import KindRegistry

log = logging.getLogger("researchos.triggers")
Trigger = Callable[[sqlite3.Connection, KindRegistry, datetime], int]


def run_triggers(conn: sqlite3.Connection, kinds: KindRegistry, triggers: tuple[Trigger, ...], now: datetime) -> int:
    n = 0
    for t in triggers:
        try:
            n += t(conn, kinds, now)
        except Exception:  # one broken trigger never stops the worker or the other triggers
            log.exception("trigger %s failed", getattr(t, "__name__", t))
    return n
