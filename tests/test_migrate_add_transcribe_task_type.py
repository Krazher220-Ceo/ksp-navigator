"""
tests/test_migrate_add_transcribe_task_type.py — тесты миграции блока К2.1.

Ловушка, которую эти тесты обязаны поймать: миграция трогает реальную
таблицу tasks через DROP+RENAME — любая ошибка здесь означает потерю
данных учителя. Проверяем сохранность строк, не только "не упало".
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_transcribe_task_type import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_migrated(db_path):
    """storage/schema.sql уже обновлён (блок К2.1/К2.2) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()
    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу ДО блока К2 — tasks без 'transcribe'/'generate_konspekt'
    в CHECK, без transcripts/konspekty вовсе. ktp_entries включена — в
    реальной продакшн-базе она есть всегда (блок Б1), а transcripts/konspekty
    ссылаются на неё внешним ключом; без неё INSERT падал бы на
    "no such table: main.ktp_entries" из-за PRAGMA foreign_keys=ON,
    даже когда конкретное значение ktp_entry_id — NULL."""
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
            CREATE TABLE ktp_entries (
                id INTEGER PRIMARY KEY,
                teacher_id INTEGER REFERENCES teachers(id),
                topic TEXT
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp')),
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


def test_migration_rejects_new_types_before_applying(tmp_path):
    path = _old_schema_db(tmp_path)
    with pytest.raises(Exception):  # IntegrityError на CHECK
        execute(
            "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t1', 'transcribe', 1)",
            db_path=path,
        )


def test_migration_allows_new_types_after_applying(tmp_path):
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)
    execute(
        "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t1', 'transcribe', 1)", db_path=path
    )
    execute(
        "INSERT INTO tasks (id, type, telegram_chat_id) VALUES ('t2', 'generate_konspekt', 1)", db_path=path
    )
    rows = query("SELECT type FROM tasks ORDER BY id", db_path=path)
    assert [r["type"] for r in rows] == ["transcribe", "generate_konspekt"]


def test_migration_preserves_all_existing_rows_exactly(tmp_path):
    """Главная проверка: ни одна строка, ни одно поле не теряется и не
    искажается миграцией DROP+RENAME (та же ловушка, что у Р4.3)."""
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO tasks (id, type, status, payload, result, error, "
        "telegram_chat_id, retries) VALUES "
        "('t1', 'parse_ksp', 'done', '{\"a\":1}', '{\"ok\":true}', NULL, 111, 0)",
        db_path=path,
    )
    execute(
        "INSERT INTO tasks (id, type, status, telegram_chat_id, retries) "
        "VALUES ('t2', 'generate_ksp', 'failed', 222, 3)",
        db_path=path,
    )

    migrate(db_path=path)

    rows = {r["id"]: dict(r) for r in query("SELECT * FROM tasks ORDER BY id", db_path=path)}
    assert rows["t1"]["type"] == "parse_ksp"
    assert rows["t1"]["status"] == "done"
    assert rows["t1"]["payload"] == '{"a":1}'
    assert rows["t1"]["result"] == '{"ok":true}'
    assert rows["t1"]["telegram_chat_id"] == 111
    assert rows["t2"]["type"] == "generate_ksp"
    assert rows["t2"]["status"] == "failed"
    assert rows["t2"]["retries"] == 3
    assert rows["t2"]["telegram_chat_id"] == 222


def test_migration_recreates_status_index(tmp_path):
    """DROP TABLE уносит с собой индексы старой tasks — миграция обязана
    пересоздать idx_tasks_status, иначе запросы по status замедлятся
    молча, без явной ошибки."""
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)
    conn = connect(path)
    try:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(tasks)").fetchall()}
    finally:
        conn.close()
    assert "idx_tasks_status" in indexes


def test_migration_creates_transcripts_and_konspekty_tables(tmp_path):
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)

    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 1)",
        db_path=path,
    )
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr1', 1, 'audio', 'текст урока', 2700, 'ru')",
        db_path=path,
    )
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json) "
        "VALUES ('k1', 1, 'tr1', 'Тема', '{}')",
        db_path=path,
    )

    transcripts = query("SELECT * FROM transcripts", db_path=path)
    konspekty = query("SELECT * FROM konspekty", db_path=path)
    assert len(transcripts) == 1
    assert transcripts[0]["duration_seconds"] == 2700
    assert len(konspekty) == 1
    assert konspekty[0]["transcript_id"] == "tr1"


def test_konspekty_requires_content_json_not_null(tmp_path):
    """content_json NOT NULL — конспект без содержимого бессмысленен,
    схема должна отклонить такую вставку, не молчаливо принять NULL."""
    path = _old_schema_db(tmp_path)
    migrate(db_path=path)
    execute("INSERT INTO teachers (id, name, telegram_user_id) VALUES (1, 'Т', 1)", db_path=path)
    with pytest.raises(Exception):
        execute(
            "INSERT INTO konspekty (id, teacher_id, tema, content_json) VALUES ('k1', 1, 'Тема', NULL)",
            db_path=path,
        )


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False

    rows = query("SELECT id FROM tasks", db_path=path)
    assert rows == []  # ничего не задваивается, таблица пустая как была


def test_partial_migration_type_done_tables_missing_still_needed(tmp_path):
    """Если CHECK уже расширен вручную, а transcripts/konspekty ещё нет —
    миграция всё равно должна их создать (обе подмиграции независимы)."""
    path = _old_schema_db(tmp_path)
    conn = connect(path)
    try:
        conn.executescript(
            """
            DROP TABLE tasks;
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                type TEXT CHECK(type IN (
                    'parse_ksp','generate_ksp','generate_ktp','transcribe','generate_konspekt'
                )),
                status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',
                payload TEXT, result TEXT, error TEXT,
                telegram_chat_id INTEGER, retries INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    assert migration_needed(connect(path)) is True
    assert migrate(db_path=path) is True
    tables = {row["name"] for row in query("SELECT name FROM sqlite_master WHERE type='table'", db_path=path)}
    assert {"transcripts", "konspekty"}.issubset(tables)
