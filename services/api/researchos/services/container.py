"""Immutable bundle of registries and singletons built once at startup (services/wiring.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..kernel.config import Settings
from .approval.subjects import SubjectRegistry
from .content.blobs import BlobStore
from .content.docs import DocKindRegistry
from .execution.templates import TemplateRegistry
from .extensions import Extensions
from .gateway.gateway import Gateway
from .gateway.keystore import SecretStore
from .gateway.tools import ToolRegistry
from .identity.ratelimit import FailureLimiter
from .orchestration.registry import KindRegistry
from .orchestration.triggers import Trigger


@dataclass(frozen=True)
class Services:
    settings: Settings
    kinds: KindRegistry
    subjects: SubjectRegistry
    doc_kinds: DocKindRegistry
    keystore: SecretStore
    migration_sources: dict[str, Path]
    gateway: Gateway
    blobs: BlobStore
    tools: ToolRegistry
    templates: TemplateRegistry
    extensions: Extensions
    login_limiter: FailureLimiter = field(default_factory=FailureLimiter)
    plugins: tuple[str, ...] = ()
    triggers: tuple[Trigger, ...] = ()  # K2 time triggers, run by the worker every settings.trigger_interval_s
