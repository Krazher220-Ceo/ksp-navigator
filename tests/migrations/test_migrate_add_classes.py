"""
tests/migrations/test_migrate_add_classes.py — тесты миграции блока У1: таблицы
classes, students, class_members.
"""

from pathlib import Path

import pytest

from core.db import connect, execute, init_db, query
from scripts.migrate_add_classes import migrate, migration_needed

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def test_fresh_schema_already_has_class_tables(db_path):
    """storage/schema.sql уже обновлён (блок У1) — свежая база не
    нуждается в миграции вообще."""
    conn = connect(db_path)
    try:
        assert migration_needed(conn) is False
    finally:
        conn.close()
    assert migrate(db_path=db_path) is False


def _old_schema_db(tmp_path) -> Path:
    """Имитирует базу, созданную ДО блока У1 — без classes/students/
    class_members, но с teachers, чтобы проверить, что миграция не
    трогает существующие таблицы и данные."""
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


def test_migration_partial_schema_still_needs_migration(tmp_path):
    """Только одна из трёх таблиц — миграция всё равно нужна (issubset,
    не пересечение): не считать частично мигрированную базу готовой."""
    path = _old_schema_db(tmp_path)
    conn = connect(path)
    try:
        conn.executescript("CREATE TABLE classes (id INTEGER PRIMARY KEY);")
        conn.commit()
    finally:
        conn.close()

    conn = connect(path)
    try:
        assert migration_needed(conn) is True
    finally:
        conn.close()


def test_migration_creates_tables_and_preserves_existing_data(tmp_path):
    path = _old_schema_db(tmp_path)
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Дмитрий Александрович', 'физика', 999)",
        db_path=path,
    )

    applied = migrate(db_path=path)
    assert applied is True

    rows = query("SELECT * FROM teachers WHERE id = 1", db_path=path)
    assert rows[0]["name"] == "Дмитрий Александрович"

    class_id = execute(
        "INSERT INTO classes (teacher_id, name, subject, invite_code) VALUES (1, '10 А', 'физика', 'AB23CD')",
        db_path=path,
    )
    student_id = execute(
        "INSERT INTO students (telegram_id, name) VALUES (555, 'Ученик Тестов')",
        db_path=path,
    )
    execute(
        "INSERT INTO class_members (class_id, student_id) VALUES (?, ?)",
        (class_id, student_id),
        db_path=path,
    )

    members = query(
        "SELECT s.name FROM class_members cm "
        "JOIN students s ON s.id = cm.student_id "
        "WHERE cm.class_id = ?",
        (class_id,),
        db_path=path,
    )
    assert [dict(row) for row in members] == [{"name": "Ученик Тестов"}]


def test_migration_is_idempotent(tmp_path):
    path = _old_schema_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False  # второй запуск ничего не делает, не падает


def test_invite_code_must_be_unique(db_path):
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Тест', 'физика', 1)",
        db_path=db_path,
    )
    execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (1, '10 А', 'AB23CD')",
        db_path=db_path,
    )
    with pytest.raises(Exception):
        execute(
            "INSERT INTO classes (teacher_id, name, invite_code) VALUES (1, '10 Б', 'AB23CD')",
            db_path=db_path,
        )


def test_student_can_join_multiple_classes(db_path):
    """Ученик может состоять в нескольких классах — намеренное свойство
    схемы (например, разные учителя одного предмета), см. комментарий в
    storage/schema.sql."""
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Тест', 'физика', 1)",
        db_path=db_path,
    )
    class_a = execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (1, '10 А', 'AAA111')",
        db_path=db_path,
    )
    class_b = execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (1, '10 Б', 'BBB222')",
        db_path=db_path,
    )
    student_id = execute(
        "INSERT INTO students (telegram_id, name) VALUES (777, 'Ученик')",
        db_path=db_path,
    )
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_a, student_id), db_path=db_path)
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_b, student_id), db_path=db_path)

    rows = query("SELECT class_id FROM class_members WHERE student_id = ? ORDER BY class_id", (student_id,), db_path=db_path)
    assert [row["class_id"] for row in rows] == [class_a, class_b]


def test_student_cannot_join_same_class_twice(db_path):
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Тест', 'физика', 1)",
        db_path=db_path,
    )
    class_id = execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (1, '10 А', 'AAA111')",
        db_path=db_path,
    )
    student_id = execute("INSERT INTO students (telegram_id, name) VALUES (777, 'Ученик')", db_path=db_path)
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id), db_path=db_path)

    with pytest.raises(Exception):
        execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id), db_path=db_path)
