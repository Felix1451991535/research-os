"""Logging with secret redaction: API keys / bearer tokens never reach log files (baseline §8)."""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),                    # OpenAI / DeepSeek / Anthropic style
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"([0-9a-f]{32}\.[A-Za-z0-9]{10,})"),            # Zhipu id.secret style
    re.compile(r"(?i)(authorization:\s*bearer\s+)([^\s'\"]+)"),
    re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)([^\s'\",}]+)"),
    re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)([^\s'\",}]+)"),
]
_known_secrets: set[str] = set()


def register_secret(value: str) -> None:
    if value and len(value) >= 6:
        _known_secrets.add(value)


def redact(text: str) -> str:
    for s in _known_secrets:
        if s in text:
            text = text.replace(s, "[REDACTED]")
    for p in _PATTERNS:
        text = p.sub(lambda m: m.group(1) + "[REDACTED]", text) if p.groups == 2 else p.sub("[REDACTED]", text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, ()
        return True


def setup_logging(logs_dir: Path | None, name: str = "api", level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    flt = RedactFilter()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(logs_dir / f"{name}.log", maxBytes=5_000_000,
                                                             backupCount=5, encoding="utf-8"))
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(flt)
        root.addHandler(h)
