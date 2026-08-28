"""
scripts/migrate_add_template_selections.py — миграция блока И1.

Создаёт общую для Web API и Telegram-бота таблицу одноразового выбора
шаблона. Не меняет существующие строки и безопасна при повторном запуске.
Опирается только на core.db и актуальную storage/schema.sql.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402


def migrate(db_path=None) -> bool:
    """Возвращает True, только если таблица создана этим запуском."""
    conn = connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'template_selections'"
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "CREATE TABLE template_selections ("
            "telegram_user_id INTEGER PRIMARY KEY, "
            "template_id INTEGER NOT NULL REFERENCES templates(id), "
            "selected_at TIMESTAMP NOT NULL)"
        )
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> None:
    if migrate():
        print("ОК: таблица template_selections создана")
    else:
        print("Пропущено: таблица template_selections уже существует")


if __name__ == "__main__":
    main()
