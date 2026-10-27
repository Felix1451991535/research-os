"""Host status for the UI: OS, data-dir disk, DB, worker, network, power, GPU (baseline §11: data-dir disk only)."""
from __future__ import annotations

import platform
import shutil
import sqlite3
import sys
from typing import Any

from ...kernel.config import Settings
from ...platform.sysinfo import gpu_info, power_status
from ..orchestration import workers


def disk_info(settings: Settings) -> dict[str, Any]:
    try:
        du = shutil.disk_usage(settings.data_dir)
    except OSError as e:
        return {"path": str(settings.data_dir), "error": str(e)}
    return {"path": str(settings.data_dir), "total_gb": round(du.total / 1e9, 1), "free_gb": round(du.free / 1e9, 1),
            "low": du.free < 5e9}


def db_status(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        ok = conn.execute("PRAGMA quick_check").fetchone()[0]
        ver = conn.execute("SELECT MAX(version) FROM schema_migrations WHERE namespace='core'").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}
    return {"ok": ok == "ok", "quick_check": ok, "schema_version": ver, "journal_mode": mode}


def host_status(conn: sqlite3.Connection, settings: Settings, include_gpu: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
               "python": sys.version.split()[0]},
        "disk": disk_info(settings),
        "db": db_status(conn),
        "worker": workers.status(conn, settings.lease_s),
        "power": power_status(),
        "network": {"host": settings.host, "port": settings.port, "tls": settings.tls_enabled,
                    "loopback_only": settings.is_loopback_bind(), "public_origins": settings.public_origins},
        "low_priv_worker": "configured" if settings.run_as_user else "NOT CONFIGURED",
    }
    if include_gpu:
        out["gpu"] = gpu_info()
    return out
