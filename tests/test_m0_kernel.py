"""M0 K1 kernel: migrations, append-only events, consumers, idempotency TTL, backup/restore."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from researchos.kernel import backup, migrate
from researchos.kernel.config import Settings
from researchos.kernel.db import connect, tx
from researchos.kernel.events import consumer_advance, consumer_position, emit
from researchos.kernel.idempotency import purge_expired
from researchos.services.container import Services


def test_migrations_current_wal_fk(conn: sqlite3.Connection, svc: Services) -> None:
    assert migrate.pending(conn, svc.migration_sources) == []
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_plugin_namespace_migration(tmp_path: Path, conn: sqlite3.Connection) -> None:
    d = tmp_path / "ns"
    d.mkdir()
    (d / "0001_init.sql").write_text("CREATE TABLE p_demo_items (id TEXT PRIMARY KEY);\n")
    assert migrate.migrate(conn, {"demo": d}) == ["demo:1"]
    assert migrate.current_version(conn, "demo") == 1
    with pytest.raises(migrate.PendingMigrations):
        (d / "0002_more.sql").write_text("CREATE TABLE p_demo_more (id TEXT);\n")
        migrate.require_current(conn, {"demo": d})


def test_pre_migration_backup(tmp_path: Path, conn: sqlite3.Connection, settings: Settings) -> None:
    d = tmp_path / "ns2"
    d.mkdir()
    (d / "0001_x.sql").write_text("CREATE TABLE p_x (id TEXT);\n")
    migrate.migrate(conn, {"x": d}, settings.backups_dir)
    assert list(settings.backups_dir.glob("pre-migrate-*.db"))


def test_events_append_only(conn: sqlite3.Connection) -> None:
    with tx(conn):
        seq = emit(conn, "test.event")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET kind='x' WHERE seq=?", (seq,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events WHERE seq=?", (seq,))


def test_event_in_same_transaction_as_change(conn: sqlite3.Connection) -> None:
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(RuntimeError), tx(conn):
        emit(conn, "test.rolled_back")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_consumer_cursor_monotonic(conn: sqlite3.Connection) -> None:
    with tx(conn):
        consumer_advance(conn, "c", 5)
        consumer_advance(conn, "c", 3)
    assert consumer_position(conn, "c") == 5


def test_idempotency_purge(conn: sqlite3.Connection) -> None:
    with tx(conn):
        conn.execute("INSERT INTO idempotency VALUES ('k','s','h',200,'{}','2000-01-01T00:00:00+00:00')")
        conn.execute("INSERT INTO idempotency VALUES ('k2','s','h',200,'{}','2999-01-01T00:00:00+00:00')")
        assert purge_expired(conn, 7) == 1


def test_backup_restore_excludes_active_runs(settings: Settings, conn: sqlite3.Connection, tmp_path: Path) -> None:
    (settings.raw_dir / "a.pdf").write_bytes(b"%PDF-1.4 original")
    with tx(conn):
        conn.execute("INSERT INTO workers VALUES ('w','h',1,'v','t','t',NULL)")
        conn.execute("INSERT INTO tasks(id,kind,idempotency_key,trace_id,state,payload,payload_hash,resource_class,"
                     "created_at,updated_at) VALUES ('t1','k','i','tr','running','{}','h','cpu','t','t')")
        conn.execute("INSERT INTO runs(id,task_id,attempt,lease_generation,worker_id,trace_id,state,started_at) "
                     "VALUES ('run_live','t1',1,1,'w','tr','running','t')")
    live = settings.runs_dir / "run_live"
    live.mkdir()
    (live / "partial.log").write_text("half")
    done = settings.runs_dir / "run_done"
    done.mkdir()
    (done / "metrics.jsonl").write_text("{}")
    res = backup.create_backup(settings, conn, "t")
    b = Path(res["path"])
    assert (b / "runs/run_done/metrics.jsonl").is_file() and not (b / "runs/run_live").exists()
    assert not (b / "config/secrets.env").exists()
    out = backup.restore_backup(b, tmp_path / "restored")
    assert out["ok"], out
    c2 = connect(tmp_path / "restored/db/researchos.db")
    assert c2.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    c2.close()
    # tamper -> verification fails
    (b / "raw/a.pdf").write_bytes(b"tampered")
    assert not backup.restore_backup(b, tmp_path / "restored2")["ok"]
    assert not backup.restore_backup(b, tmp_path / "restored")["ok"]  # non-empty target refused
