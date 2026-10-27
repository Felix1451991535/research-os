"""Test-only platform shim.

On Windows, DatasetProviderConformance.test_build_is_verifiable_and_confined keeps the np.load() NpzFile open
while its TemporaryDirectory is removed, which fails with WinError 32 *after* all assertions have run (the same
happens for the core's own plugin_hello example). We let TemporaryDirectory ignore cleanup errors on Windows only.
No assertion is skipped or altered; on Linux/macOS this file does nothing.
"""

from __future__ import annotations

import sys
import tempfile
from typing import Any

if sys.platform == "win32":
    _Base = tempfile.TemporaryDirectory

    class _LenientTemporaryDirectory(_Base):  # type: ignore[type-arg]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs.setdefault("ignore_cleanup_errors", True)
            super().__init__(*args, **kwargs)

    setattr(tempfile, "TemporaryDirectory", _LenientTemporaryDirectory)  # noqa: B010
