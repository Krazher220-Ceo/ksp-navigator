"""
scripts/migrate_add_transcribe_task_type.py — миграция БД для блока К2.1
(PLAN_STAGE2.md): два новых типа задачи 'transcribe' и 'generate_konspekt'
в CHECK-ограничении tasks.type (были 'parse_ksp', 'generate_ksp',
'generate_ktp'), плюс новые таблицы transcripts и konspekty (блок К2.2).

Форма повторяет scripts/migrate_add_generate_ktp_task_type.py дословно
(план прямо требует не изобретать свою, а повторить): CHECK нельзя
поменять через ALTER TABLE — CREATE новой tasks + COPY + DROP + RENAME,
внутри одной транзакции. transcripts/konspekty — новые таблицы, для них
как в блоках М6/М7 достаточно CREATE TABLE IF NOT EXISTS, без пересборки.

Идемпотентна: проверяет реальное определение tasks в sqlite_master (для
CHECK) и список таблиц (для transcripts/konspekty) — повторный запуск
безопасен.

Запуск (бот на это время лучше остановить — миграция берёт эксклюзивную
блокировку на запись):
    python scripts/migrate_add_transcribe_task_type.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import transaction  # noqa: E402

_NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    source TEXT,
    text TEXT NOT NULL,
    duration_seconds INTEGER,
    language TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS konspekty (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    transcript_id TEXT REFERENCES transcripts(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    tema TEXT,
    content_json TEXT NOT NULL,
    docx_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transcripts_teacher ON transcripts(teacher_id);
CREATE INDEX IF NOT EXISTS idx_konspekty_teacher ON konspekty(teacher_id);
"""


def check_type_migration_needed(conn) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None:
        raise RuntimeError("таблица tasks не найдена — сначала примени storage/schema.sql")
    return "transcribe" not in row["sql"]


def check_tables_migration_needed(conn) -> bool:
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return not {"transcripts", "konspekty"}.issubset(tables)


def migration_needed(conn) -> bool:
    return check_type_migration_needed(conn) or check_tables_migration_needed(conn)


def migrate(db_path=None) -> bool:
    """Возвращает True, если хоть одна из двух подмиграций реально
    применена сейчас; False — если обе уже были применены раньше."""
    with transaction(db_path) as conn:
        applied = False

        if check_type_migration_needed(conn):
            conn.execute(
                "CREATE TABLE tasks_new ("
                "    id TEXT PRIMARY KEY,"
                "    type TEXT CHECK(type IN ("
                "        'parse_ksp','generate_ksp','generate_ktp','transcribe','generate_konspekt'"
                "    )),"
                "    status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',"
                "    payload TEXT,"
                "    result TEXT,"
                "    error TEXT,"
                "    telegram_chat_id INTEGER,"
                "    retries INTEGER DEFAULT 0,"
                "    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                "    updated_at TIMESTAMP"
                ")"
            )
            conn.execute(
                "INSERT INTO tasks_new "
                "(id, type, status, payload, result, error, telegram_chat_id, retries, created_at, updated_at) "
                "SELECT id, type, status, payload, result, error, telegram_chat_id, retries, created_at, updated_at "
                "FROM tasks"
            )
            conn.execute("DROP TABLE tasks")
            conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
            # DROP TABLE уносит с собой все индексы старой tasks — пересоздаём.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            applied = True

        if check_tables_migration_needed(conn):
            conn.executescript(_NEW_TABLES_SQL)
            applied = True

    return applied


def main() -> None:
    applied = migrate()
    if applied:
        print(
            "ОК: миграция применена — tasks.type разрешает 'transcribe'/'generate_konspekt', "
            "таблицы transcripts и konspekty созданы"
        )
    else:
        print("Пропущено: миграция уже была применена раньше, менять нечего")


if __name__ == "__main__":
    main()
