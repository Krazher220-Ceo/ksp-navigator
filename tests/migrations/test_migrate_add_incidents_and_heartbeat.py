"""
tests/migrations/test_migrate_add_incidents_and_heartbeat.py — тесты миграции блока М7.
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_incidents_and_heartbeat import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_has_both_tables(db_path):
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()
    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    path = tmp_path / "old.db"
    conn = connect(path)
    try:
        conn.executescript("CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT);")
        conn.commit()
    finally:
        conn.close()
    return path


def test_old_schema_needs_migration(tmp_path):
    path = _old_schema_db(tmp_path)
    conn = connect(path)
    try:
        assert migration_needed(conn) is True
    finally:
        conn.close()


def test_migration_creates_both_tables_and_they_work(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True

    execute(
        "INSERT INTO incidents (started_at, reason) VALUES (datetime('now'), 'dns_fail')", db_path=path
    )
    execute("INSERT INTO worker_heartbeat (id) VALUES (1)", db_path=path)

    assert len(query("SELECT * FROM incidents", db_path=path)) == 1
    assert len(query("SELECT * FROM worker_heartbeat", db_path=path)) == 1


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False


def test_partial_migration_missing_only_worker_heartbeat_still_needed(tmp_path):
    """Если incidents уже есть (например, при ручном вмешательстве), а
    worker_heartbeat нет — миграция всё равно должна сработать."""
    path = _old_schema_db(tmp_path)
    conn = connect(path)
    try:
        conn.execute(
            "CREATE TABLE incidents (id INTEGER PRIMARY KEY, started_at TIMESTAMP NOT NULL, "
            "ended_at TIMESTAMP, reason TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0)"
        )
        conn.commit()
    finally:
        conn.close()

    assert migration_needed(connect(path)) is True
    assert migrate(db_path=path) is True
    tables = {row["name"] for row in query("SELECT name FROM sqlite_master WHERE type='table'", db_path=path)}
    assert "worker_heartbeat" in tables
