"""
tests/migrations/test_migrate_add_usage_daily_table.py — тесты миграции блока М6.
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_usage_daily_table import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_has_usage_daily_table(db_path):
    """storage/schema.sql уже обновлён (блок М6) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()
    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу, созданную ДО блока М6 — без usage_daily, но с
    teachers, чтобы проверить, что миграция не трогает существующие
    таблицы и данные."""
    path = tmp_path / "old.db"
    conn = connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE teachers (
                id INTEGER PRIMARY KEY,
                name TEXT, subject TEXT, telegram_user_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
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


def test_migration_creates_table_and_preserves_existing_data(tmp_path):
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Дмитрий Александрович', 'физика', 999)",
        db_path=path,
    )

    applied = migrate(db_path=path)
    assert applied is True

    rows = query("SELECT * FROM teachers WHERE id = 1", db_path=path)
    assert rows[0]["name"] == "Дмитрий Александрович"

    execute(
        "INSERT INTO usage_daily (telegram_user_id, day, operation, count, tokens) VALUES (999, '2026-08-26', 'generate_ksp', 1, 1000)",
        db_path=path,
    )
    usage_rows = query("SELECT * FROM usage_daily", db_path=path)
    assert len(usage_rows) == 1
    assert usage_rows[0]["count"] == 1


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False  # второй запуск ничего не делает, не падает
