"""Authenticity of the update feed (ADR-228): `latest.json` is signed with Ed25519; the detached signature is the
release asset `latest.json.sig`, one line `ed25519 <key id> <base64 signature>`. The public halves of the release key
and of a backup key are compiled in (TRUSTED_KEYS); a feed is offered only if one of them verified the exact bytes.
Private keys never enter the repo (scripts/release/keygen.py writes them outside it; the release box keeps them)."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIG_SUFFIX = ".sig"
SCHEME = "ed25519"
MAX_SIG_BYTES = 1024
# key id -> raw public key (base64). Rotation: add the new key, release, then drop the old one (ADR-228).
TRUSTED_KEYS: dict[str, str] = {
    "c0cc7a08357a5357": "chbeSwV6PM10hw0lmY6Uq1nSUqCf3tUeZjKyUE/9srM=",  # release key (2026-10-28)
    "25d265ce416b381e": "rJ4MMn5DUWPmRWKtBM/JJ32VSeti7w/iVBxa4I+opm0=",  # backup key (2026-10-28), unused so far
}


class SignatureError(Exception):
    """`unsigned` (no / unreadable signature) or `bad_signature` (wrong key or tampered bytes)."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def key_id(public: Ed25519PublicKey) -> str:
    return hashlib.sha256(public.public_bytes_raw()).hexdigest()[:16]


def public_b64(public: Ed25519PublicKey) -> str:
    return base64.b64encode(public.public_bytes_raw()).decode("ascii")


def load_private(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def sign(data: bytes, private: Ed25519PrivateKey) -> str:
    """The `latest.json.sig` text for `data`."""
    sig = base64.b64encode(private.sign(data)).decode("ascii")
    return f"{SCHEME} {key_id(private.public_key())} {sig}\n"


def verify(data: bytes, sig_text: bytes | None, trusted: Mapping[str, str] = TRUSTED_KEYS) -> str:
    """Key id that signed `data`; raises SignatureError."""
    if not sig_text or len(sig_text) > MAX_SIG_BYTES:
        raise SignatureError("unsigned")
    try:
        scheme, kid, sig = sig_text.decode("ascii").split()
        raw_sig = base64.b64decode(sig, validate=True)
    except (UnicodeDecodeError, ValueError, binascii.Error) as e:
        raise SignatureError("unsigned") from e
    if scheme != SCHEME or kid not in trusted:
        raise SignatureError("bad_signature")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(trusted[kid])).verify(raw_sig, data)
    except (InvalidSignature, ValueError, binascii.Error) as e:
        raise SignatureError("bad_signature") from e
    return kid
