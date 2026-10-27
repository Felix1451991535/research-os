"""PhysioNet access through the host HttpContext (or an owner's local copy via ctx.local, LocalCopy): chunked
(HTTP Range) downloads that stay under the host response-size cap, per-file verification against the upstream
SHA256SUMS.txt, and license detection from the upstream LICENSE.txt. Nothing is invented: a missing checksum list
means "unverified", an unrecognised license means None (the provider then refuses to build unless the owner states
the license explicitly)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from researchos.contracts.http import HttpContext, HttpResponse

HOST = "physionet.org"
CHUNK_BYTES = 4_000_000  # below the host's default 5 MB response cap
_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")


class UpstreamError(RuntimeError):
    """Upstream file missing, unexpected HTTP status, or checksum mismatch. The build must fail."""


def _header(resp: HttpResponse, name: str) -> str | None:
    want = name.lower()
    return next((v for k, v in resp.headers.items() if k.lower() == want), None)


def _check_host(url: str) -> None:
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname != HOST:
        raise UpstreamError(f"refusing non-PhysioNet URL {url}")


def _content_range(resp: HttpResponse, start: int) -> int:
    m = _RANGE.fullmatch((_header(resp, "content-range") or "").strip())
    if not m or int(m[1]) != start or m[3] == "*":
        raise UpstreamError(f"{resp.url}: invalid Content-Range for a ranged download")
    return int(m[3])


def fetch_bytes(http: HttpContext, url: str, chunk: int = CHUNK_BYTES) -> bytes | None:
    """Whole file via Range requests (or one 200 response). Returns None on HTTP 404; raises on other errors."""
    _check_host(url)
    resp = http.get(url, headers={"Range": f"bytes=0-{chunk - 1}"})
    if resp.status == 404:
        return None
    if resp.status == 200:
        return resp.body
    if resp.status != 206:
        raise UpstreamError(f"{url}: HTTP {resp.status}")
    total = _content_range(resp, 0)
    parts, got = [resp.body], len(resp.body)
    while got < total:
        r = http.get(url, headers={"Range": f"bytes={got}-{min(got + chunk, total) - 1}"})
        if r.status != 206 or _content_range(r, got) != total or not r.body:
            raise UpstreamError(f"{url}: ranged download failed at byte {got} (HTTP {r.status})")
        parts.append(r.body)
        got += len(r.body)
    data = b"".join(parts)
    if len(data) != total:
        raise UpstreamError(f"{url}: received {len(data)} bytes, Content-Range declared {total}")
    return data


def parse_sha256sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


_LICENSES = (
    (re.compile(r"open data commons attribution license", re.I), "ODC-By-1.0"),
    (re.compile(r"open data commons open database license", re.I), "ODbL-1.0"),
    (re.compile(r"creative commons attribution 4\.0 international", re.I), "CC-BY-4.0"),
    (re.compile(r"cc0 1\.0 universal|creative commons zero", re.I), "CC0-1.0"),
)


def detect_license(text: str) -> str | None:
    flat = " ".join(text.split())
    hits = {spdx for rx, spdx in _LICENSES if rx.search(flat)}
    return hits.pop() if len(hits) == 1 else None


def _aggregate(entries: Mapping[str, str]) -> str:
    return hashlib.sha256("".join(f"{v}  {k}\n" for k, v in sorted(entries.items())).encode()).hexdigest()


@dataclass
class Upstream:
    """One PhysioNet project version. Every downloaded file is hashed; if SHA256SUMS.txt lists it, it must match."""

    http: HttpContext
    base: str  # e.g. https://physionet.org/files/mitdb/1.0.0/
    expected: dict[str, str] | None = None
    observed: dict[str, str] = field(default_factory=dict)

    def load_checksums(self) -> None:
        raw = fetch_bytes(self.http, self.base + "SHA256SUMS.txt")
        self.expected = parse_sha256sums(raw.decode("utf-8", errors="replace")) if raw is not None else None

    def get(self, path: str) -> bytes:
        data = fetch_bytes(self.http, self.base + path)
        if data is None:
            raise UpstreamError(f"{self.base}{path}: not found (HTTP 404)")
        sha = hashlib.sha256(data).hexdigest()
        exp = (self.expected or {}).get(path)
        if exp is not None and exp != sha:
            raise UpstreamError(
                f"checksum mismatch for {self.base}{path}: upstream SHA256SUMS.txt says {exp}, downloaded {sha}; "
                "the download is corrupt or upstream changed - build aborted"
            )
        self.observed[path] = sha
        return data

    def get_text(self, path: str) -> str:
        return self.get(path).decode("utf-8-sig", errors="strict")

    def license(self, settings: Mapping[str, Any]) -> tuple[str, str]:
        """(license id, where it came from). Raises when neither upstream nor the owner states a license."""
        override = settings.get("license_override")
        if override:
            return str(override), "owner setting license_override"
        raw = fetch_bytes(self.http, self.base + "LICENSE.txt")
        lic = detect_license(raw.decode("utf-8", errors="replace")) if raw is not None else None
        if lic is None:
            raise UpstreamError(
                f"could not determine the dataset license from {self.base}LICENSE.txt; "
                "set license_override after checking the PhysioNet project page"
            )
        return lic, self.base + "LICENSE.txt"

    def checksum_summary(self) -> tuple[str | None, str, dict[str, Any]]:
        """(expected aggregate | None, observed aggregate, provenance details)."""
        exp = self.expected or {}
        unverified = sorted(p for p in self.observed if p not in exp)
        expected = None if self.expected is None or unverified else _aggregate({p: exp[p] for p in self.observed})
        details = {
            "upstream_checksums": self.base + "SHA256SUMS.txt" if self.expected is not None else None,
            "files_verified": len(self.observed) - len(unverified) if self.expected is not None else 0,
            "files_unverified": unverified if self.expected is not None else sorted(self.observed),
            "aggregate": "sha256 over sorted '<sha256>  <path>' lines of the upstream files used",
        }
        return expected, _aggregate(self.observed), details


MAX_LOCAL_BYTES = 256_000_000


@dataclass
class LocalCopy(Upstream):
    """An owner's local copy of one PhysioNet project version (core 1.5 read-only `ctx.local`, files:read): the same
    interface as Upstream, read from disk instead of downloaded (ADR-189). A SHA256SUMS.txt in the directory is
    used when present; otherwise every file is reported as unverified. The licence must be stated by the owner
    (`license_override`) unless the copy carries a recognisable LICENSE.txt - nothing is assumed from the name."""

    local: Any = None  # LocalReadContext
    upstream: str | None = None  # project URL whose public SHA256SUMS.txt verifies the copy when it has none
    checksums_from: str | None = None

    def _read(self, path: str) -> bytes | None:
        try:
            return bytes(self.local.read_bytes(self.base + path, max_bytes=MAX_LOCAL_BYTES))
        except FileNotFoundError:
            return None

    def load_checksums(self) -> None:
        raw, self.checksums_from = self._read("SHA256SUMS.txt"), self.base + "SHA256SUMS.txt"
        if raw is None and self.upstream:
            try:  # public, no credentials; any failure leaves the copy honestly unverified
                raw, self.checksums_from = (
                    fetch_bytes(self.http, self.upstream + "SHA256SUMS.txt"),
                    (self.upstream + "SHA256SUMS.txt"),
                )
            except Exception:  # noqa: BLE001 - network denied/unreachable: unverified, never assumed
                raw = None
        self.expected = parse_sha256sums(raw.decode("utf-8", errors="replace")) if raw is not None else None

    def checksum_summary(self) -> tuple[str | None, str, dict[str, Any]]:
        expected, observed, details = super().checksum_summary()
        if self.expected is not None:
            details["upstream_checksums"] = self.checksums_from
        return expected, observed, details

    def get(self, path: str) -> bytes:
        data = self._read(path)
        if data is None:
            raise UpstreamError(f"{path}: not in the local copy")
        sha = hashlib.sha256(data).hexdigest()
        exp = (self.expected or {}).get(path)
        if exp is not None and exp != sha:
            raise UpstreamError(f"checksum mismatch for local {path}: SHA256SUMS.txt says {exp}, file has {sha}")
        self.observed[path] = sha
        return data

    def license(self, settings: Mapping[str, Any]) -> tuple[str, str]:
        override = settings.get("license_override")
        if override:
            return str(override), "owner setting license_override (local copy)"
        raw = self._read("LICENSE.txt")
        lic = detect_license(raw.decode("utf-8", errors="replace")) if raw is not None else None
        if lic is None:
            raise UpstreamError(
                "the local copy has no recognisable LICENSE.txt; set license_override after "
                "checking the PhysioNet project page"
            )
        return lic, "local LICENSE.txt"
