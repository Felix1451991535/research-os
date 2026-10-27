"""Individual doctor checks. Each appends PASS/WARN/FAIL/NOT RUN results with an actionable hint."""
from __future__ import annotations

import json
import shutil
import socket
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ...kernel.config import REPO_ROOT, Settings


@dataclass
class Report:
    items: list[dict[str, str]] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", hint: str = "") -> None:
        self.items.append({"check": name, "status": status, "detail": detail, "hint": hint})


def check_python(r: Report, s: Settings) -> None:
    ok = (3, 11) <= sys.version_info[:2] < (3, 14)
    r.add("python", "PASS" if ok else "FAIL", sys.version.split()[0], "" if ok else "use Python 3.11-3.13 (3.12)")


def check_storage(r: Report, s: Settings) -> None:
    try:
        s.ensure_dirs()
        probe = s.data_dir / ".write_probe"
        probe.write_text("x")
        probe.unlink()
        r.add("data_dir_writable", "PASS", str(s.data_dir))
    except OSError as e:
        r.add("data_dir_writable", "FAIL", str(e), "create the data dir or fix permissions")
    if sys.platform == "win32" and str(s.data_dir).upper().startswith("C:"):
        r.add("data_not_on_c", "WARN", str(s.data_dir), "baseline §2: research data should not live on C:")
    try:
        free_gb = shutil.disk_usage(s.data_dir).free / 1e9
        r.add("disk_free", "PASS" if free_gb > 20 else ("WARN" if free_gb > 5 else "FAIL"), f"{free_gb:.1f} GB free")
    except OSError as e:
        r.add("disk_free", "FAIL", str(e))


def check_db(r: Report, s: Settings) -> None:
    if not s.db_path.is_file():
        r.add("db_integrity", "NOT RUN", "DB not created yet", "start the API once")
        return
    from ...kernel.db import connect
    from ...kernel.migrate import CORE_DIR, pending
    from ..orchestration.workers import status as worker_status
    try:
        conn = connect(s.db_path)
        qc = conn.execute("PRAGMA quick_check").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        pend = pending(conn, {"core": CORE_DIR})
        ws = worker_status(conn, s.lease_s)
        conn.close()
    except sqlite3.Error as e:
        r.add("db_integrity", "FAIL", str(e), "restore from backup (docs/OPS.md#restore)")
        return
    r.add("db_integrity", "PASS" if qc == "ok" else "FAIL", qc)
    r.add("db_wal", "PASS" if mode == "wal" else "WARN", mode)
    r.add("db_migrations", "PASS" if not pend else "WARN", f"{len(pend)} pending",
          "start the API (auto-backup + migrate) or run `researchos-api migrate`" if pend else "")
    r.add("worker", "PASS" if ws.get("alive") else "WARN", json.dumps(ws),
          "" if ws.get("alive") else "start.ps1 / start.sh starts the worker")


def check_tls(r: Report, s: Settings) -> None:
    if not (s.tls_enabled and Path(s.tls_cert_file).is_file()):
        return
    from .tlscert import inspect_cert
    info = inspect_cert(s.tls_cert_file)
    r.add("tls_cert_valid", "FAIL" if info["expired"] else ("WARN" if info["days_left"] < 14 else "PASS"),
          f"expires {info['not_after']} ({info['days_left']} d)")
    if info["validity_days"] > 825:
        r.add("tls_cert_ios_lifetime", "FAIL", f"{info['validity_days']} days",
              "iOS rejects server certs valid >825 days; regenerate")
    for o in s.public_origins:
        h = urlsplit(o).hostname or ""
        ok = h in info["hosts"]
        r.add(f"tls_san:{h}", "PASS" if ok else "FAIL", f"cert hosts={info['hosts']}",
              "" if ok else f"regenerate cert including {h}")


def check_network(r: Report, s: Settings) -> None:
    probs = s.validate_network()
    r.add("network_config", "FAIL" if probs else "PASS", "; ".join(probs) or f"{s.host}:{s.port} tls={s.tls_enabled}")
    if s.is_loopback_bind():
        r.add("ipad_access", "WARN", "bound to loopback: iPad cannot connect",
              "set host to the LAN/Tailscale IP + TLS + public_origins (docs/OPS.md#ipad)")
    check_tls(r, s)
    for o in s.public_origins:
        u = urlsplit(o)
        if u.scheme != "https":
            r.add(f"origin_https:{o}", "WARN", "iPad service worker/offline cache needs HTTPS (secure context)")
        if u.port and u.port != s.port:
            r.add(f"origin_port:{o}", "WARN", f"origin port {u.port} != listen port {s.port}", "ok only behind a proxy")
    if not s.is_loopback_bind() and s.allow_insecure_lan:
        r.add("insecure_lan", "WARN", "allow_insecure_lan=true: plain HTTP on the network", "configure TLS")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            in_use = sock.connect_ex(("127.0.0.1", s.port)) == 0
        r.add("port", "PASS", f"{s.port} {'in use (server running?)' if in_use else 'free'}")
    except OSError as e:
        r.add("port", "WARN", str(e))


def check_secrets(r: Report, s: Settings) -> None:
    secrets_file = s.config_dir / "secrets.env"
    try:
        inside_repo = secrets_file.resolve().is_relative_to(REPO_ROOT.resolve())
    except OSError:
        inside_repo = False
    gi = REPO_ROOT / ".gitignore"
    ignored = gi.is_file() and "data/" in gi.read_text(encoding="utf-8")
    if inside_repo and not ignored:
        r.add("secrets_location", "FAIL", "secrets.env inside repo and data/ not git-ignored",
              "add data/ to .gitignore")
    else:
        r.add("secrets_location", "PASS", str(secrets_file))
    from ..gateway.keystore import SecretStore
    conf = [p for p, st in SecretStore(s.config_dir).status().items() if st["configured"]]
    r.add("api_keys", "PASS" if conf else "WARN", f"configured: {conf or 'none'}",
          "" if conf else "AI features show 'not configured' until keys are set in Settings")


def check_web(r: Report, s: Settings) -> None:
    idx = Path(s.web_dist) / "index.html"
    r.add("web_build", "PASS" if idx.is_file() else "FAIL", str(idx),
          "" if idx.is_file() else "cd apps/web && npm ci && npm run build")


def check_compute(r: Report, s: Settings) -> None:
    r.add("low_priv_worker", "PASS" if s.run_as_user else "WARN", s.run_as_user or "NOT CONFIGURED",
          "" if s.run_as_user else "baseline §7: create a low-privilege OS user and set run_as_user "
          "(docs/OPS.md#low-priv-worker); always-on guards still apply")
    from ...platform.sysinfo import gpu_info
    g = gpu_info()
    r.add("gpu_detect", "PASS" if g.get("detected") else "WARN", json.dumps(g)[:300],
          "" if g.get("detected") else "no NVIDIA GPU visible: GPU tasks blocked; CPU path flagged degraded")
    try:
        import torch as t
        r.add("torch", "PASS", f"{t.__version__} cuda={t.version.cuda} available={t.cuda.is_available()}",
              "run the gpu_preflight template for a real check (is_available alone is insufficient)")
    except Exception:
        r.add("torch", "WARN", "torch not installed", "install via scripts/install-torch.ps1 (official selector)")


def check_gpu_preflight(r: Report, s: Settings) -> None:
    """GPU acceptance = the gpu_preflight template actually passed on this machine (is_available alone is not)."""
    if not s.db_path.is_file():
        r.add("gpu_preflight", "NOT RUN", "database not created yet", "start the API once")
        return
    from ...kernel.db import connect
    conn = connect(s.db_path)
    try:
        last = conn.execute(
            "SELECT r.state, r.started_at, t.error_message FROM runs r JOIN tasks t ON t.id=r.task_id "
            "JOIN plan_revisions pr ON pr.plan_id=json_extract(t.payload,'$.plan_id') "
            "AND pr.revision=json_extract(t.payload,'$.revision') "
            "WHERE t.kind='experiment.run' AND json_extract(pr.content,'$.template.id')='gpu_preflight' "
            "ORDER BY r.started_at DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        last = None
    finally:
        conn.close()
    if last is None:
        r.add("gpu_preflight", "NOT RUN", "no gpu_preflight run on this host",
              "create a plan with template gpu_preflight, approve it, run stage 'smoke'")
    elif last[0] == "succeeded":
        r.add("gpu_preflight", "PASS", f"passed at {last[1]}")
    else:
        r.add("gpu_preflight", "FAIL", f"last run {last[0]} at {last[1]}: {last[2] or ''}"[:300],
              "see the run's stderr/preflight.json; GPU tasks stay blocked until it passes")


ALL_CHECKS = (check_python, check_storage, check_db, check_network, check_secrets, check_web, check_compute,
              check_gpu_preflight)
