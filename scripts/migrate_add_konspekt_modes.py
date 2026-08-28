"""
scripts/migrate_add_konspekt_modes.py — миграция блока К0.

Добавляет обязательный режим обработки в transcripts и konspekty.
Существующие записи считаются ученическими, как работал продукт до К0.
Повторный запуск безопасен; новые зависимости не используются.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402


_TABLES = ("transcripts", "konspekty")


def _has_mode_column(conn, table: str) -> bool:
    return any(row["name"] == "mode" for row in conn.execute(f"PRAGMA table_info({table})").fetchall())


def migrate(db_path=None) -> bool:
    """Добавляет отсутствующие поля; True означает, что схема изменилась."""
    conn = connect(db_path)
    changed = False
    try:
        for table in _TABLES:
            if _has_mode_column(conn, table):
                continue
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN mode TEXT NOT NULL DEFAULT 'student' "
                "CHECK(mode IN ('student', 'teacher'))"
            )
            changed = True
        conn.commit()
        return changed
    finally:
        conn.close()


def main() -> None:
    if migrate():
        print("ОК: режим обработки добавлен в transcripts и konspekty")
    else:
        print("Пропущено: режим обработки уже есть в обеих таблицах")


if __name__ == "__main__":
    main()
