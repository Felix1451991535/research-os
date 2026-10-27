"""Process-start hardening shared by the API and the worker (HARDENING #5, #17)."""
from __future__ import annotations

import logging

from ..platform.env import scrub_process_env
from .container import Services

log = logging.getLogger("researchos")


def harden_process(svc: Services) -> None:
    svc.keystore.capture_env()
    removed = scrub_process_env(svc.settings.data_dir / "home")
    if removed:
        log.info("environment scrubbed: %d inherited variables removed", len(removed))
