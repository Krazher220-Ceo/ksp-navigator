"""
tests/test_migrate_add_generate_ktp_task_type.py — тесты миграции блока Р4.3.

Ловушка, которую эти тесты обязаны поймать: миграция трогает реальную
таблицу через DROP+RENAME — любая ошибка здесь означает потерю данных
учителя. Проверяем именно сохранность строк, не только "не упало".
"""

from pathlib import Path

import pytest

from core.db import execute, init_db, query
from scripts.migrate_add_generate_ktp_task_type import migrate, migration_needed
from core.db import connect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_allows_generate_ktp(db_path):
    """storage/schema.sql уже обновлён (блок Р4.3) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()

    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу, созданную ДО блока Р4.3 — с двумя типами задач в
    CHECK, как было раньше. Не переиспользует storage/schema.sql (он уже
    обновлён), пишет старое определение явно."""
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
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                type TEXT CHECK(type IN ('parse_ksp','generate_ksp')),
                status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',
                payload TEXT, result TEXT, error TEXT,
                telegram_chat_id INTEGER, retries INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP
            );
            CREATE INDEX idx_tasks_status ON tasks(status);
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


def test_migration_rejects_generate_ktp_before_applying(tmp_path):
    path = _old_schema_db(tmp_path)
    with pytest.raises(Exception):  # IntegrityError на CHECK
        execute(
            "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t1', 'generate_ktp', 1)",
            db_path=path,
        )


def test_migration_preserves_all_existing_rows_exactly(tmp_path):
    """Главная проверка: ни одна строка, ни одно поле не теряется и не
    искажается миграцией DROP+RENAME."""
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO tasks (id, type, status, payload, result, error, "
        "telegram_chat_id, retries) VALUES "
        "('t1', 'parse_ksp', 'done', '{\"a\":1}', '{\"ok\":true}', NULL, 111, 0)",
        db_path=path,
    )
    execute(
        "INSERT INTO tasks (id, type, status, payload, telegram_chat_id, retries) "
        "VALUES ('t2', 'generate_ksp', 'failed', '{\"b\":2}', 222, 3)",
        db_path=path,
    )
    before = {r["id"]: dict(r) for r in query("SELECT * FROM tasks ORDER BY id", db_path=path)}

    applied = migrate(db_path=path)
    assert applied is True

    after = {r["id"]: dict(r) for r in query("SELECT * FROM tasks ORDER BY id", db_path=path)}
    assert set(after.keys()) == {"t1", "t2"}
    for task_id in before:
        for key in ("id", "type", "status", "payload", "result", "error", "telegram_chat_id", "retries"):
            assert after[task_id][key] == before[task_id][key], f"{task_id}.{key} изменилось"


def test_migration_allows_generate_ktp_after_applying(tmp_path):
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)

    new_id = execute(
        "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t3', 'generate_ktp', 1)",
        db_path=path,
    )
    rows = query("SELECT type FROM tasks WHERE id = 't3'", db_path=path)
    assert rows[0]["type"] == "generate_ktp"


def test_migration_recreates_status_index(tmp_path):
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_status'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    execute("INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t1', 'parse_ksp', 1)", db_path=path)

    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False  # второй запуск — нечего делать

    rows = query("SELECT id FROM tasks", db_path=path)
    assert [r["id"] for r in rows] == ["t1"]  # не задвоилось


def test_migration_preserves_foreign_keys_and_other_tables(tmp_path):
    """Миграция трогает только tasks — остальные таблицы (и данные в них)
    не затрагиваются вообще."""
    path = _old_schema_db(tmp_path)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=path)
    migrate(db_path=path)
    rows = query("SELECT name FROM teachers WHERE id = 1", db_path=path)
    assert rows[0]["name"] == "Т"
