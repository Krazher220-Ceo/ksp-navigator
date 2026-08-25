"""
scripts/migrate_add_school_to_teachers.py — миграция БД для блока Р9
(PLAN_STAGE1_EXT.md): поле "school" в таблице teachers, чтобы наименование
организации образования сохранялось в профиле педагога и подставлялось в
шапку КСП автоматически, а не оставалось прочерком.

Проще, чем миграция Р4.3 (scripts/migrate_add_generate_ktp_task_type.py):
добавление колонки — аддитивное изменение, SQLite поддерживает его
напрямую через ALTER TABLE ... ADD COLUMN, пересборка таблицы
(CREATE + COPY + DROP + RENAME) не нужна — она нужна была там для
CHECK-ограничения, которое ALTER TABLE менять не умеет вовсе.

Идемпотентна: проверяет реальный список колонок таблицы (PRAGMA
table_info), если "school" уже есть — ничего не делает.

Запуск:
    python scripts/migrate_add_school_to_teachers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402


def migration_needed(conn) -> bool:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(teachers)").fetchall()}
    if "id" not in columns:
        raise RuntimeError("таблица teachers не найдена — сначала примени storage/schema.sql")
    return "school" not in columns


def migrate(db_path=None) -> bool:
    """Возвращает True, если миграция реально применена сейчас; False —
    если она уже была применена раньше (повторный запуск безопасен)."""
    conn = connect(db_path)
    try:
        if not migration_needed(conn):
            return False
        conn.execute("ALTER TABLE teachers ADD COLUMN school TEXT")
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> None:
    applied = migrate()
    if applied:
        print("ОК: миграция применена — teachers.school добавлен")
    else:
        print("Пропущено: миграция уже была применена раньше, менять нечего")


if __name__ == "__main__":
    main()
