"""
tests/test_db.py — тесты core/db.py.

Проверяет тонкую обёртку над sqlite3: создание базы во временной папке
(база тестов никогда не трогает storage/app.db), вставку, чтение,
откат транзакции при исключении, а также что применение реальной
storage/schema.sql создаёт все 7 таблиц и переносимо при повторном вызове.
"""

import sqlite3
from pathlib import Path

import pytest

from core.db import connect, execute, executemany, init_db, query, transaction

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

# Небольшая синтетическая схема — тесты обёртки не должны зависеть
# от того, что именно поменяется в реальной схеме проекта в будущих блоках.
DEMO_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
"""


@pytest.fixture
def demo_db(tmp_path):
    db_path = tmp_path / "test.db"
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text(DEMO_SCHEMA, encoding="utf-8")
    init_db(db_path=db_path, schema_path=schema_path)
    return db_path


def test_init_db_creates_file(demo_db):
    assert demo_db.exists()


def test_init_db_idempotent(demo_db):
    schema_path = demo_db.parent / "schema.sql"
    # повторное применение той же схемы не должно падать
    init_db(db_path=demo_db, schema_path=schema_path)


def test_execute_returns_lastrowid_and_query_reads_it_back(demo_db):
    row_id = execute(
        "INSERT INTO demo (name) VALUES (?)", ("Алихан",), db_path=demo_db
    )
    assert row_id is not None

    rows = query("SELECT * FROM demo WHERE id = ?", (row_id,), db_path=demo_db)
    assert len(rows) == 1
    assert rows[0]["name"] == "Алихан"


def test_executemany_inserts_all_rows(demo_db):
    executemany(
        "INSERT INTO demo (name) VALUES (?)",
        [("Первый",), ("Второй",), ("Третий",)],
        db_path=demo_db,
    )
    rows = query("SELECT * FROM demo ORDER BY id", db_path=demo_db)
    assert [r["name"] for r in rows] == ["Первый", "Второй", "Третий"]


def test_transaction_commits_on_success(demo_db):
    with transaction(demo_db) as conn:
        conn.execute("INSERT INTO demo (name) VALUES (?)", ("Успех",))

    rows = query("SELECT * FROM demo WHERE name = ?", ("Успех",), db_path=demo_db)
    assert len(rows) == 1


def test_transaction_rolls_back_on_exception(demo_db):
    with pytest.raises(RuntimeError):
        with transaction(demo_db) as conn:
            conn.execute(
                "INSERT INTO demo (name) VALUES (?)", ("Не должно остаться",)
            )
            raise RuntimeError("искусственная ошибка для проверки отката")

    rows = query(
        "SELECT * FROM demo WHERE name = ?", ("Не должно остаться",), db_path=demo_db
    )
    assert len(rows) == 0


def test_connect_sets_row_factory(demo_db):
    conn = connect(demo_db)
    try:
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_connect_enables_foreign_keys(demo_db):
    conn = connect(demo_db)
    try:
        fk_status = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_status == 1
    finally:
        conn.close()


def test_connect_uses_wal_journal_mode(demo_db):
    conn = connect(demo_db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_real_schema_creates_all_seven_tables(tmp_path):
    db_path = tmp_path / "real_schema.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    conn = connect(db_path)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        conn.close()

    expected = {
        "teachers",
        "style_profiles",
        "templates",
        "curriculum_objectives",
        "ktp_entries",
        "generated_ksp",
        "tasks",
    }
    assert expected <= tables


def test_real_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "real_schema_repeat.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    # повторный запуск на той же базе не должен падать
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)


def test_real_schema_enforces_foreign_keys(tmp_path):
    db_path = tmp_path / "real_schema_fk.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    with pytest.raises(sqlite3.IntegrityError):
        execute(
            "INSERT INTO ktp_entries (teacher_id, lesson_number) VALUES (?, ?)",
            (999, 1),
            db_path=db_path,
        )
