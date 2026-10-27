"""Platform adapter (ARCHITECTURE §9). All OS-specific behaviour lives in this package.

Windows code paths are written against documented Win32 APIs but are NOT RUN on the dev box (Linux);
scripts/selftest.ps1 exercises them on the owner's machine.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
