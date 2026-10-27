"""Host facts: GPU (nvidia-smi), LAN addresses, power, sleep inhibition, trash."""
from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import psutil

from . import IS_WINDOWS
from .process import hidden_window_kwargs


def nvidia_smi(fields: list[str], timeout: float = 5.0) -> list[dict[str, str]] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    kw: dict[str, Any] = hidden_window_kwargs()
    if IS_WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined,unused-ignore]
    try:
        out = subprocess.run([exe, f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=timeout, encoding="utf-8", **kw)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    res = []
    for line in out.stdout.strip().splitlines():
        vals = [v.strip() for v in line.split(",")]
        if len(vals) == len(fields):
            res.append(dict(zip(fields, vals, strict=True)))
    return res


def gpu_info() -> dict[str, Any]:
    q = nvidia_smi(["name", "memory.total", "memory.used", "driver_version", "temperature.gpu"])
    if q is None:
        return {"detected": False, "reason": "nvidia-smi not found or failed"}
    return {"detected": bool(q), "gpus": q}


def lan_addresses() -> list[str]:
    """Best-effort private IPv4 addresses (LAN / Tailscale 100.64/10)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # UDP connect sends no packet
            found.append(s.getsockname()[0])
    except OSError:
        pass
    for addrs in psutil.net_if_addrs().values():
        found += [a.address for a in addrs if a.family == socket.AF_INET]
    out: list[str] = []
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    for ip in found:
        a = ipaddress.ip_address(ip)
        if (a.is_private or a in cgnat) and not a.is_loopback and ip not in out:
            out.append(ip)
    return out


def power_status() -> dict[str, Any]:
    b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if b is None:
        return {"battery": None}
    return {"battery": round(b.percent), "plugged": b.power_plugged}


def inhibit_sleep(on: bool) -> bool:
    """Keep the host awake during runs. Windows only in v1; Linux returns False (systemd-inhibit optional)."""
    if IS_WINDOWS:
        from .windows import inhibit_sleep as win_inhibit
        win_inhibit(on)
        return True
    return False


def move_to_trash(path: Path) -> None:
    from send2trash import send2trash
    send2trash(str(path))
