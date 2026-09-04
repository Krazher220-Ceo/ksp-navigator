"""
tests/migrations/test_migrate_add_sverka_task_type.py — тесты миграции блока У4.

Ловушка, которую эти тесты обязаны поймать: миграция трогает реальную
таблицу tasks через DROP+RENAME — любая ошибка здесь означает потерю
данных учителя. Проверяем сохранность строк, не только "не упало".
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_sverka_task_type import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_migrated(db_path):
    """storage/schema.sql уже обновлён (блок У4) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()
    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу ДО блока У4 — tasks без 'sverka_tetradi' в CHECK."""
    path = tmp_path / "old.db"
    conn = connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp','transcribe','generate_konspekt')),
                status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',
                payload TEXT,
                result TEXT,
                error TEXT,
                telegram_chat_id INTEGER,
                retries INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
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


def test_old_schema_rejects_new_type_before_migration(tmp_path):
    path = _old_schema_db(tmp_path)
    with pytest.raises(Exception):
        execute(
            "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t1', 'sverka_tetradi', 1)",
            db_path=path,
        )


def test_migration_allows_new_type_and_preserves_existing_rows(tmp_path):
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO tasks (id, type, status, telegram_chat_id) VALUES ('old-1', 'generate_ksp', 'done', 42)",
        db_path=path,
    )

    applied = migrate(db_path=path)
    assert applied is True

    old_row = query("SELECT * FROM tasks WHERE id = 'old-1'", db_path=path)
    assert old_row[0]["type"] == "generate_ksp"
    assert old_row[0]["status"] == "done"
    assert old_row[0]["telegram_chat_id"] == 42

    new_id = execute(
        "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('sv-1', 'sverka_tetradi', 43)",
        db_path=path,
    )
    new_row = query("SELECT * FROM tasks WHERE id = 'sv-1'", db_path=path)
    assert new_row[0]["type"] == "sverka_tetradi"


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False


def test_status_index_survives_migration(tmp_path):
    """DROP TABLE уносит с собой индексы старой tasks — миграция обязана
    пересоздать idx_tasks_status, иначе очередь молча теряет индекс на
    боевом масштабе (не упадёт, но станет медленнее с каждой неделей)."""
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)
    conn = connect(path)
    try:
        indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        conn.close()
    assert "idx_tasks_status" in indexes
