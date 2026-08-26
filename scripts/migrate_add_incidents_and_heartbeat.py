"""
scripts/migrate_add_incidents_and_heartbeat.py — миграция БД для блока М7
(PLAN_STAGE2.md): таблицы incidents (уведомление после восстановления) и
worker_heartbeat (живость воркера очереди).

Обе новые — как и usage_daily в блоке М6, CREATE TABLE IF NOT EXISTS
напрямую, без пересборки существующих таблиц.

Идемпотентна: проверяет реальный список таблиц, повторный запуск
безопасен.

Запуск:
    python scripts/migrate_add_incidents_and_heartbeat.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    reason TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_incidents_unresolved ON incidents(ended_at, notified);
"""


def migration_needed(conn) -> bool:
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return not {"incidents", "worker_heartbeat"}.issubset(tables)


def migrate(db_path=None) -> bool:
    conn = connect(db_path)
    try:
        if not migration_needed(conn):
            return False
        conn.executescript(_CREATE_SQL)
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> None:
    applied = migrate()
    if applied:
        print("ОК: миграция применена — таблицы incidents и worker_heartbeat созданы")
    else:
        print("Пропущено: таблицы уже существовали, менять нечего")


if __name__ == "__main__":
    main()
