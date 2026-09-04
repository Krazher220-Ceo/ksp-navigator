"""Проверки безопасного dry-run переноса SQLite в Supabase."""

import importlib.util
from pathlib import Path

from core.db import execute, init_db


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "migrate_sqlite_to_supabase.py"

spec = importlib.util.spec_from_file_location("migrate_sqlite_to_supabase", SCRIPT_PATH)
migration = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(migration)


def test_dry_run_reads_existing_tables_and_treats_old_optional_table_as_empty(tmp_path):
    db_path = tmp_path / "app.db"
    init_db(db_path=db_path)
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (?, ?, ?, ?)",
        (1, "Учитель", "физика", 123),
        db_path=db_path,
    )

    # Имитируем старую локальную БД до появления admin_access.
    from core.db import connect

    conn = connect(db_path)
    try:
        conn.execute("DROP TABLE admin_access")
        conn.commit()
    finally:
        conn.close()

    counts = migration.migrate(db_path, tmp_path / "backup", apply=False)

    assert counts["teachers"] == (1, -1)
    assert counts["admin_access"] == (0, -1)
    assert not (tmp_path / "backup").exists()
