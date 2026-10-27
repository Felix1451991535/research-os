"""Private-network HTTPS for iPad Safari (baseline §3: service worker needs a secure context).

Option A (preferred if Tailscale is used): `tailscale cert <machine>.<tailnet>.ts.net` -> publicly trusted cert.
Option B (LAN only): this module creates a local CA + server certificate. Install ca.crt on the iPad
(Settings > Profile Downloaded > Install, then Settings > General > About > Certificate Trust Settings > enable).
The CA private key stays in <data>/config/tls and never leaves the host.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import sys
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# iOS rejects TLS server certs valid for more than 825 days; keep well under.
SERVER_DAYS = 397
CA_DAYS = 3650


def _write_key(path: Path, key: Any) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    if sys.platform != "win32":
        os.chmod(path, 0o600)


def _load_or_create_ca(tls_dir: Path) -> tuple[Any, x509.Certificate]:
    ca_key_p, ca_crt_p = tls_dir / "ca.key", tls_dir / "ca.crt"
    if ca_key_p.is_file() and ca_crt_p.is_file():
        key = serialization.load_pem_private_key(ca_key_p.read_bytes(), None)
        crt = x509.load_pem_x509_certificate(ca_crt_p.read_bytes())
        return key, crt
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Research OS Local CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Research OS (this PC only)")])
    now = dt.datetime.now(dt.UTC)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=CA_DAYS))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                        content_commitment=False, key_encipherment=False, data_encipherment=False,
                                        key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
           .sign(key, hashes.SHA256()))
    tls_dir.mkdir(parents=True, exist_ok=True)
    _write_key(ca_key_p, key)
    ca_crt_p.write_bytes(crt.public_bytes(serialization.Encoding.PEM))
    return key, crt


def make_server_cert(tls_dir: Path, hosts: list[str]) -> dict[str, Any]:
    if not hosts:
        raise ValueError("at least one hostname or IP required")
    ca_key, ca_crt = _load_or_create_ca(tls_dir)
    key = ec.generate_private_key(ec.SECP256R1())
    sans: list[x509.GeneralName] = []
    for h in hosts:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))
    now = dt.datetime.now(dt.UTC)
    crt = (x509.CertificateBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
           .issuer_name(ca_crt.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
           .add_extension(x509.SubjectAlternativeName(sans), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    key_p, crt_p = tls_dir / "server.key", tls_dir / "server.crt"
    _write_key(key_p, key)
    crt_p.write_bytes(crt.public_bytes(serialization.Encoding.PEM) + ca_crt.public_bytes(serialization.Encoding.PEM))
    return {"cert": str(crt_p), "key": str(key_p), "ca_cert": str(tls_dir / "ca.crt"), "hosts": hosts,
            "not_after": crt.not_valid_after_utc.isoformat()}


def inspect_cert(cert_file: str) -> dict[str, Any]:
    """Return SAN hosts and validity window of the first cert in a PEM file."""
    crt = x509.load_pem_x509_certificate(Path(cert_file).read_bytes())
    hosts: list[str] = []
    try:
        san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        hosts = [str(v) for v in san.get_values_for_type(x509.DNSName)] + \
                [str(v) for v in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass
    now = dt.datetime.now(dt.UTC)
    return {"hosts": hosts, "not_after": crt.not_valid_after_utc.isoformat(),
            "expired": crt.not_valid_after_utc <= now,
            "days_left": (crt.not_valid_after_utc - now).days,
            "validity_days": (crt.not_valid_after_utc - crt.not_valid_before_utc).days}
