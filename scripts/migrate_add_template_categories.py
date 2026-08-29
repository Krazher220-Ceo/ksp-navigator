"""Добавляет категории шаблонов в существующую SQLite-базу."""

import sqlite3
import sys
from pathlib import Path


def migrate(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(templates)")}
        if "category" not in columns:
            conn.execute("ALTER TABLE templates ADD COLUMN category TEXT NOT NULL DEFAULT 'personal'")
        conn.execute("UPDATE templates SET category = 'official' WHERE is_official = 1")
        conn.execute("UPDATE templates SET category = 'sample' WHERE is_builtin = 1 AND is_official = 0")
        conn.execute("UPDATE templates SET category = 'personal' WHERE is_builtin = 0")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate(Path(sys.argv[1]))
