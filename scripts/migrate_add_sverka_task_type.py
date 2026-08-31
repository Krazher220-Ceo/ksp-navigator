"""
scripts/migrate_add_sverka_task_type.py — миграция БД для блока У4
(PLAN.md): новый тип задачи 'sverka_tetradi' в CHECK-ограничении
tasks.type (сверка тетради ученика — OCR фото + LLM-сравнение с
расшифровкой урока, оба вызова к LLM, как и остальные типы задач).

Форма повторяет scripts/migrate_add_transcribe_task_type.py дословно
(план прямо требует не изобретать свою, а повторить): CHECK нельзя
поменять через ALTER TABLE в SQLite — CREATE новой tasks + COPY + DROP +
RENAME, внутри одной транзакции.

⚠️ Эта миграция — только для SQLite (резервный режим DB_BACKEND=sqlite
и тесты). В проде база — Supabase, а RPC ksp_execute_sql намеренно не
пропускает DDL (грабля 2.13, PLAN.md). Готовый SQL для Supabase SQL
Editor — в отчёте по блоку У4.

Идемпотентна: проверяет реальное определение tasks в sqlite_master,
повторный запуск безопасен.

Запуск (бот на это время лучше остановить — миграция берёт эксклюзивную
блокировку на запись):
    python scripts/migrate_add_sverka_task_type.py
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
    return "sverka_tetradi" not in row["sql"]


def migrate(db_path=None) -> bool:
    """Возвращает True, если миграция реально применена сейчас; False —
    если тип уже был разрешён раньше."""
    with transaction(db_path) as conn:
        if not migration_needed(conn):
            return False

        conn.execute(
            "CREATE TABLE tasks_new ("
            "    id TEXT PRIMARY KEY,"
            "    type TEXT CHECK(type IN ("
            "        'parse_ksp','generate_ksp','generate_ktp','transcribe',"
            "        'generate_konspekt','sverka_tetradi'"
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

    return True


def main() -> None:
    applied = migrate()
    if applied:
        print("ОК: миграция применена — tasks.type разрешает 'sverka_tetradi'")
    else:
        print("Пропущено: миграция уже была применена раньше, менять нечего")


if __name__ == "__main__":
    main()
