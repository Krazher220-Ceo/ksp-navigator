"""
scripts/migrate_add_classes.py — миграция БД для блока У1 (PLAN.md):
три новые таблицы classes, students, class_members (класс и ученики,
MASTER.md 0.9 п.2).

Как и usage_daily/incidents/worker_heartbeat раньше — это НОВЫЕ таблицы,
не изменение существующих: CREATE TABLE IF NOT EXISTS применяется
напрямую, без пересборки. Идемпотентна: проверяет реальный список
таблиц, повторный запуск безопасен.

⚠️ Эта миграция — только для SQLite (резервный режим DB_BACKEND=sqlite
и тесты). В проде база — Supabase, а RPC ksp_execute_sql намеренно не
пропускает DDL (грабля 2.13, PLAN.md) — там же три таблицы применяются
вручную через SQL Editor, готовый SQL для этого лежит в отчёте по блоку
У1 и продублирован в storage/schema_supabase.sql.

Запуск:
    python scripts/migrate_add_classes.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    name TEXT NOT NULL,
    subject TEXT,
    invite_code TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    telegram_id INTEGER NOT NULL,
    name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS class_members (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_classes_invite_code ON classes(invite_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_telegram_id ON students(telegram_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_class_members_unique ON class_members(class_id, student_id);
CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_class_members_student ON class_members(student_id);
"""

_NEW_TABLES = {"classes", "students", "class_members"}


def migration_needed(conn) -> bool:
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return not _NEW_TABLES.issubset(tables)


def migrate(db_path=None) -> bool:
    """Возвращает True, если миграция реально применена сейчас; False —
    если все три таблицы уже существовали (повторный запуск безопасен)."""
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
        print("ОК: миграция применена — таблицы classes, students, class_members созданы")
    else:
        print("Пропущено: таблицы уже существовали, менять нечего")


if __name__ == "__main__":
    main()
