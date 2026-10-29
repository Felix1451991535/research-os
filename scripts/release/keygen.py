"""Generate an Ed25519 update-signing key (ADR-228) OUTSIDE the repo: writes the private key as PEM (mode 0600, never
overwritten) and prints only the key id and the public half to paste into update_signing.TRUSTED_KEYS.

Usage: python scripts/release/keygen.py <private key path>   (e.g. ~/.config/researchos/keys/update-release.pem)"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from researchos.services.ops.update_signing import key_id, public_b64

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT  # noqa: E402


def write_key(path: Path) -> tuple[str, str]:
    """Create the key file; returns (key id, public base64)."""
    path = path.expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("signing keys must be stored outside the repo")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite an existing key
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key_id(key.public_key()), public_b64(key.public_key())


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        kid, pub = write_key(Path(argv[0]))
    except (ValueError, FileExistsError) as e:
        print(f"keygen: {e}", file=sys.stderr)
        return 1
    print(f'"{kid}": "{pub}",')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
