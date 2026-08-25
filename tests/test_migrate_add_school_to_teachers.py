"""
tests/test_migrate_add_school_to_teachers.py — тесты миграции блока Р9.
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_school_to_teachers import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_has_school_column(db_path):
    """storage/schema.sql уже обновлён (блок Р9) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()

    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу, созданную ДО блока Р9 — teachers без school."""
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


def test_migration_preserves_existing_teacher_rows(tmp_path):
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Дмитрий Александрович', 'физика', 999)",
        db_path=path,
    )

    applied = migrate(db_path=path)
    assert applied is True

    rows = query("SELECT * FROM teachers WHERE id = 1", db_path=path)
    assert rows[0]["name"] == "Дмитрий Александрович"
    assert rows[0]["subject"] == "физика"
    assert rows[0]["telegram_user_id"] == 999
    assert rows[0]["school"] is None  # новая колонка, старые строки — NULL


def test_migration_allows_setting_school_after_applying(tmp_path):
    path = _old_schema_db(tmp_path)
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 1)", db_path=path)
    migrate(db_path=path)

    execute("UPDATE teachers SET school = ? WHERE id = 1", ("КГУ «Гимназия №27», Костанай",), db_path=path)
    rows = query("SELECT school FROM teachers WHERE id = 1", db_path=path)
    assert rows[0]["school"] == "КГУ «Гимназия №27», Костанай"


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 1)", db_path=path)

    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False  # второй запуск — нечего делать, не падает на "duplicate column"

    rows = query("SELECT id FROM teachers", db_path=path)
    assert [r["id"] for r in rows] == [1]
