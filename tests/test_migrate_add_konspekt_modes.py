"""Регрессии миграции К0: режим результата в transcripts и konspekty."""

import sqlite3

import pytest


def _legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE teachers (id INTEGER PRIMARY KEY, name TEXT, telegram_user_id INTEGER UNIQUE);
            CREATE TABLE transcripts (
                id TEXT PRIMARY KEY,
                teacher_id INTEGER REFERENCES teachers(id),
                source TEXT,
                text TEXT NOT NULL,
                duration_seconds INTEGER,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE konspekty (
                id TEXT PRIMARY KEY,
                teacher_id INTEGER REFERENCES teachers(id),
                transcript_id TEXT REFERENCES transcripts(id),
                tema TEXT,
                content_json TEXT NOT NULL,
                docx_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO teachers (id, name, telegram_user_id) VALUES (1, 'Учитель', 1);
            INSERT INTO transcripts (id, teacher_id, source, text) VALUES ('tr1', 1, 'audio', 'старый текст');
            INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json)
            VALUES ('k1', 1, 'tr1', 'Старая тема', '{}');
            """
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_migration_backfills_modes_and_is_idempotent(tmp_path):
    from scripts.migrate_add_konspekt_modes import migrate

    path = _legacy_db(tmp_path)
    assert migrate(db_path=path) is True
    assert migrate(db_path=path) is False

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        transcript = conn.execute("SELECT mode FROM transcripts WHERE id = 'tr1'").fetchone()
        konspekt = conn.execute("SELECT mode FROM konspekty WHERE id = 'k1'").fetchone()
    finally:
        conn.close()

    assert transcript["mode"] == "student"
    assert konspekt["mode"] == "student"


@pytest.mark.parametrize("table", ["transcripts", "konspekty"])
def test_migration_mode_is_required_and_checked(tmp_path, table):
    from scripts.migrate_add_konspekt_modes import migrate

    path = _legacy_db(tmp_path)
    migrate(db_path=path)
    conn = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE {table} SET mode = 'unknown'")
    finally:
        conn.close()
