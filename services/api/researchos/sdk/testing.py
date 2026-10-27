"""Offline test doubles for plugin authors (no network, no host DB). They implement the same contracts as the
host and enforce the same rules (declared hosts only, hard-identifier dedupe, idempotency, outputs confined)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

from ..contracts.datasources import BibRecord
from ..contracts.exporters import ArtifactRef, CitationEntry, MetricValue
from ..contracts.http import HttpDenied, HttpResponse

Responder = HttpResponse | Callable[[str], HttpResponse]


class FakeHttp:
    """Serves recorded responses by URL prefix. Requests to hosts outside `allowed_hosts` raise HttpDenied,
    exactly like the host's fetch service; unmatched URLs raise AssertionError (record a fixture)."""

    def __init__(self, routes: Mapping[str, Responder], allowed_hosts: tuple[str, ...]) -> None:
        self.routes, self.allowed_hosts = dict(routes), allowed_hosts
        self.calls: list[str] = []

    def get(
        self, url: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        full = url + ("?" + urlencode(sorted(params.items())) if params else "")
        host = urlsplit(url).hostname or ""
        if host not in self.allowed_hosts:
            raise HttpDenied(f"host {host!r} not declared by this plugin")
        self.calls.append(full)
        for prefix, resp in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if full.startswith(prefix):
                return resp(full) if callable(resp) else resp
        raise AssertionError(f"no recorded fixture for {full}")


def json_response(url: str, body: str, status: int = 200) -> HttpResponse:
    return HttpResponse(status, {"content-type": "application/json"}, body.encode(), url)


HARD_IDS = ("doi", "pmid", "arxiv", "zotero")  # zotero value is library-scoped: "users/<id>/<key>"


@dataclass
class InMemoryKnowledgeWriter:
    """Mirrors the host KnowledgeWriter rules: hard-identifier dedupe (DOI > PMID > arXiv), idempotency keys."""

    works: dict[str, BibRecord] = field(default_factory=dict)
    by_id: dict[tuple[str, str], str] = field(default_factory=dict)
    by_key: dict[str, Any] = field(default_factory=dict)
    originals: dict[str, tuple[str, bytes, str | None]] = field(default_factory=dict)

    def upsert_work(self, record: BibRecord, *, idempotency_key: str) -> tuple[str, bool]:
        if idempotency_key in self.by_key:
            return self.by_key[idempotency_key][0], False
        ids = [(s, v) for s, v in record.identifiers if s in HARD_IDS]
        existing = next((self.by_id[i] for i in ids if i in self.by_id), None)
        wid, created = (existing, False) if existing else (f"work_{len(self.works) + 1}", True)
        if created:
            self.works[wid] = record
        for i in ids:
            self.by_id.setdefault(i, wid)
        self.by_key[idempotency_key] = (wid, created)
        return wid, created

    def attach_original(
        self, work_id: str, data: bytes, filename: str, *, license: str | None, idempotency_key: str
    ) -> str:
        if idempotency_key in self.by_key:
            return str(self.by_key[idempotency_key])
        vid = f"ver_{len(self.originals) + 1}"
        self.originals[vid] = (work_id, data, license)
        self.by_key[idempotency_key] = vid
        return vid


@dataclass
class FakeExportContext:
    out_dir: Path
    citations: Mapping[str, CitationEntry] = field(default_factory=dict)
    metrics: Mapping[tuple[str, str, str], MetricValue] = field(default_factory=dict)
    artifacts: Mapping[str, ArtifactRef] = field(default_factory=dict)
    mode: Literal["draft", "final"] = "final"

    def citation(self, citekey: str) -> CitationEntry | None:
        return self.citations.get(citekey)

    def metric(self, run_id: str, name: str, split: str) -> MetricValue | None:
        return self.metrics.get((run_id, name, split))

    def artifact(self, artifact_id: str) -> ArtifactRef | None:
        return self.artifacts.get(artifact_id)


@dataclass
class FakeLocalRead:
    """LocalReadContext confined to one directory (the test's stand-in for an owner file root)."""

    root: Path

    def _inside(self, raw: str) -> Path:
        p = Path(raw)
        if not p.is_absolute() or not p.resolve().is_relative_to(self.root.resolve()) or p.is_symlink():
            raise PermissionError(f"{raw!r} is not inside the test root")
        if not p.exists():
            raise FileNotFoundError(raw)
        return p.resolve()

    def list_files(self, directory: str, *, suffixes: tuple[str, ...] = (), limit: int = 10_000) -> list[str]:
        base = self._inside(directory)
        wanted = tuple(s.lower() for s in suffixes)
        out = sorted(str(p) for p in base.rglob("*")
                     if p.is_file() and not p.is_symlink() and (not wanted or p.name.lower().endswith(wanted)))
        if len(out) > limit:
            raise ValueError(f"more than {limit} files below {base}")
        return out

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        p = self._inside(path)
        if p.stat().st_size > max_bytes:
            raise ValueError(f"{p} is larger than {max_bytes} bytes")
        return p.read_bytes()


@dataclass
class FakeBuildContext:
    out_dir: Path
    http: FakeHttp
    cancelled: bool = False
    local: FakeLocalRead | None = None  # core 1.5: set for providers that declare files:read
    events: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    def cancel_requested(self) -> bool:
        return self.cancelled

    def progress(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        self.events.append((stage, done, total))


@dataclass
class FakeToolContext:
    run_id: str = "run_test"
    trace_id: str | None = "trace_test"
    http: Any = None
