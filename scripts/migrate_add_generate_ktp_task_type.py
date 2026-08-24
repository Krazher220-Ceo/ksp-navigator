"""
scripts/migrate_add_generate_ktp_task_type.py — миграция БД для блока Р4.3
(PLAN_STAGE1_EXT.md): третий тип задачи 'generate_ktp' в CHECK-ограничении
tasks.type (были только 'parse_ksp' и 'generate_ksp').

Зачем нужна отдельная миграция, а не просто правка storage/schema.sql:
SQLite не поддерживает ALTER TABLE для изменения CHECK-ограничения
существующей таблицы напрямую. Стандартный безопасный путь — создать новую
таблицу с нужным ограничением, скопировать все строки, удалить старую,
переименовать новую. Всё внутри одной транзакции (core.db.transaction) —
либо применится целиком, либо не применится вовсе, данные не теряются
ни при каком сбое посередине.

Идемпотентна: проверяет реальное определение таблицы в sqlite_master,
если 'generate_ktp' там уже разрешён — ничего не делает.

Запуск (бот на это время лучше остановить — миграция берёт эксклюзивную
блокировку на запись, конкурентная задача очереди могла бы получить
"database is locked"):
    python scripts/migrate_add_generate_ktp_task_type.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import transaction  # noqa: E402


def migration_needed(conn) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    if row is None:
        raise RuntimeError("таблица tasks не найдена — сначала примени storage/schema.sql")
    return "generate_ktp" not in row["sql"]


def migrate(db_path=None) -> bool:
    """Возвращает True, если миграция реально применена сейчас; False —
    если она уже была применена раньше (повторный запуск безопасен)."""
    with transaction(db_path) as conn:
        if not migration_needed(conn):
            return False

        conn.execute(
            "CREATE TABLE tasks_new ("
            "    id TEXT PRIMARY KEY,"
            "    type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp')),"
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

    return True


def main() -> None:
    applied = migrate()
    if applied:
        print("ОК: миграция применена — tasks.type теперь разрешает 'generate_ktp'")
    else:
        print("Пропущено: миграция уже была применена раньше, менять нечего")


if __name__ == "__main__":
    main()
