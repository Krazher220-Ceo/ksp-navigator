"""
scripts/migrate_add_usage_daily_table.py — миграция БД для блока М6
(PLAN_STAGE2.md): новая таблица usage_daily для дневных лимитов на
аккаунт (core/limits.py).

Проще всех предыдущих миграций проекта: это НОВАЯ таблица, не изменение
существующей — CREATE TABLE IF NOT EXISTS применяется напрямую, без
ALTER TABLE и без пересборки (CREATE + COPY + DROP + RENAME, которая
нужна была миграциям Р4.3/Р9 для CHECK-ограничения и для колонки в уже
населённой таблице). Здесь населённых строк не появится, пока не начнёт
работать core/limits.py.

Идемпотентна: PRAGMA table_info проверяет реальное наличие таблицы,
повторный запуск безопасен.

Запуск:
    python scripts/migrate_add_usage_daily_table.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS usage_daily (
    telegram_user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    operation TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (telegram_user_id, day, operation)
)
"""


def migration_needed(conn) -> bool:
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return "usage_daily" not in tables


def migrate(db_path=None) -> bool:
    """Возвращает True, если миграция реально применена сейчас; False —
    если таблица уже существовала (повторный запуск безопасен)."""
    conn = connect(db_path)
    try:
        if not migration_needed(conn):
            return False
        conn.execute(_CREATE_SQL)
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> None:
    applied = migrate()
    if applied:
        print("ОК: миграция применена — таблица usage_daily создана")
    else:
        print("Пропущено: таблица usage_daily уже существовала, менять нечего")


if __name__ == "__main__":
    main()
