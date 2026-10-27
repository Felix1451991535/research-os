"""`researchos-api` CLI: serve, migrate, setup-token, reset-password, backup, restore, doctor, make-cert, lan-urls,
openapi."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from ..kernel.config import Settings, load_settings
from ..kernel.db import connect
from ..kernel.logsetup import setup_logging
from ..services.hardening import harden_process
from ..services.wiring import build_services
from .factory import create_app, init_storage


def _serve(settings: Settings) -> int:
    import uvicorn

    problems = settings.validate_network()
    if problems:
        for p in problems:
            print("ERROR:", p, file=sys.stderr)
        return 2
    setup_logging(settings.logs_dir, "api")
    svc = build_services(settings)
    harden_process(svc)
    app = create_app(settings, svc)
    kw = {"ssl_certfile": settings.tls_cert_file, "ssl_keyfile": settings.tls_key_file} if settings.tls_enabled else {}
    scheme = "https" if settings.tls_enabled else "http"
    print(f"Research OS listening on {scheme}://{settings.host}:{settings.port}")
    for o in settings.public_origins:
        print(f"  open on desktop + iPad: {o}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, proxy_headers=False, **kw)  # type: ignore[arg-type]
    return 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="researchos-api")
    ap.add_argument("--data-dir")
    sub = ap.add_subparsers(dest="cmd")
    for name, hlp in (("serve", "run the API"), ("migrate", "apply migrations (with pre-migration backup)"),
                      ("setup-token", "print the one-time first-run setup token"), ("openapi", "print OpenAPI JSON"),
                      ("lan-urls", "print candidate LAN URLs")):
        sub.add_parser(name, help=hlp)
    sub.add_parser("reset-password", help="local recovery; revokes all sessions").add_argument("username")
    sub.add_parser("backup").add_argument("--label", default="")
    rs = sub.add_parser("restore", help="restore a backup into a NEW directory and verify")
    rs.add_argument("backup_dir")
    rs.add_argument("target_dir")
    sub.add_parser("doctor").add_argument("--json", action="store_true")
    sub.add_parser("make-cert", help="local CA + server cert for LAN HTTPS (iPad)").add_argument("hosts", nargs="+")
    ev = sub.add_parser("eval", help="run the G3 QA gold set through the real gateway and record certification")
    ev.add_argument("--items", required=True, help="JSONL of owner-verified items (tests/gold/g3_qa.jsonl)")
    ev.add_argument("--budget-cny", type=float, default=2.0, help="hard cost cap for this eval run")
    ov = sub.add_parser("certify-override", help="owner override: allow an uncertified model for cited QA (labelled)")
    ov.add_argument("model_id")
    return ap


def _storage_cmd(cmd: str, a: argparse.Namespace, settings: Settings) -> int:
    svc = build_services(settings)
    init_storage(svc)
    conn = connect(settings.db_path)
    try:
        if cmd == "migrate":
            print("core schema version",
                  conn.execute("SELECT MAX(version) FROM schema_migrations WHERE namespace='core'").fetchone()[0])
        elif cmd == "setup-token":
            from ..services.identity.credentials import ensure_setup_token
            tok = ensure_setup_token(conn, settings.config_dir)
            print(tok if tok else "admin already initialized; no setup token")
        elif cmd == "reset-password":
            from ..services.identity.credentials import reset_admin_password
            pw = getpass.getpass("new password: ")
            if pw != getpass.getpass("repeat: "):
                print("passwords differ", file=sys.stderr)
                return 1
            reset_admin_password(conn, a.username, pw)
            print("password reset; all sessions revoked")
        elif cmd == "eval":
            from ..services.research.evaluation import load_items, run_g3
            rep = run_g3(conn, svc.gateway, load_items(Path(a.items)), budget_cny=a.budget_cny)
            print(json.dumps(rep.__dict__, indent=1, ensure_ascii=False, default=str))
        elif cmd == "certify-override":
            from ..services.gateway import certification
            from ..services.research.qa_prompts import CITED_QA
            certification.record(conn, "owner_override", a.model_id, CITED_QA.hash, {"override": 1.0}, override=True)
            print(f"override recorded for {a.model_id}; answers stay labelled as uncertified")
        elif cmd == "backup":
            from ..kernel.backup import create_backup
            print(json.dumps(create_backup(settings, conn, a.label), indent=1, ensure_ascii=False))
    finally:
        conn.close()
    return 0


def run(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    settings = load_settings(a.data_dir)
    cmd = a.cmd or "serve"
    if cmd == "serve":
        return _serve(settings)
    if cmd == "openapi":
        print(json.dumps(create_app(settings).openapi(), indent=1))
        return 0
    if cmd == "restore":
        from ..kernel.backup import restore_backup
        res = restore_backup(Path(a.backup_dir), Path(a.target_dir))
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if cmd == "doctor":
        from ..services.ops.doctor import run_doctor
        return run_doctor(settings, as_json=a.json)
    if cmd == "make-cert":
        from ..services.ops.tlscert import make_server_cert
        print(json.dumps(make_server_cert(settings.config_dir / "tls", a.hosts), indent=1))
        print("Set tls_cert_file / tls_key_file / public_origins in researchos.toml; install ca.crt on the iPad "
              "(docs/OPS.md#ipad).")
        return 0
    if cmd == "lan-urls":
        from ..platform.sysinfo import lan_addresses
        scheme = "https" if settings.tls_enabled else "http"
        for ip in lan_addresses():
            print(f"{scheme}://{ip}:{settings.port}")
        return 0
    return _storage_cmd(cmd, a, settings)


if __name__ == "__main__":
    sys.exit(run())
