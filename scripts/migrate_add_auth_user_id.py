"""
scripts/migrate_add_auth_user_id.py — миграция БД для блока Ф4
(FRONTEND_PLAN.md): связь аккаунта Supabase Auth с профилем в системе.

Что делает:
  1. teachers.auth_user_id  — кто вошёл по почте;
  2. teachers.city          — город, его спрашивает экран регистрации;
  3. students.auth_user_id  — то же для ученика;
  4. таблица consents_web   — согласие пришедшего с сайта.

Чего осознанно НЕ делает: не снимает NOT NULL с students.telegram_id.
В SQLite это требует пересборки таблицы (CREATE + COPY + DROP + RENAME),
а в проде используется Postgres, где ограничение снимается одной
строкой. Расходиться локальной и прод-схеме нельзя, поэтому здесь
пересборка сделана явно и только если ограничение действительно стоит.

Идемпотентна: смотрит реальный список колонок (PRAGMA table_info) и
ничего не делает, если всё уже на месте.

⚠️ В проде (Supabase) эта миграция не применяется — RPC ksp_execute_sql
намеренно не пропускает DDL. Готовый SQL для SQL Editor лежит в
REPORT.md, блок Ф4.

Запуск:
    python scripts/migrate_add_auth_user_id.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import connect  # noqa: E402


def _columns(conn, table: str) -> dict[str, dict]:
    return {row["name"]: dict(row) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migration_needed(conn) -> bool:
    учителя = _columns(conn, "teachers")
    if "id" not in учителя:
        raise RuntimeError("таблица teachers не найдена — сначала примени storage/schema.sql")
    ученики = _columns(conn, "students")
    согласия = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'consents_web'"
    ).fetchall()
    return (
        "city" not in учителя
        or "auth_user_id" not in учителя
        or "auth_user_id" not in ученики
        or not согласия
        or bool(ученики.get("telegram_id", {}).get("notnull"))
    )


def migrate(db_path=None) -> bool:
    """True — миграция применена сейчас; False — уже была применена."""
    conn = connect(db_path)
    try:
        if not migration_needed(conn):
            return False

        учителя = _columns(conn, "teachers")
        if "auth_user_id" not in учителя:
            conn.execute("ALTER TABLE teachers ADD COLUMN auth_user_id TEXT")
        if "city" not in учителя:
            conn.execute("ALTER TABLE teachers ADD COLUMN city TEXT")

        ученики = _columns(conn, "students")
        if "auth_user_id" not in ученики:
            conn.execute("ALTER TABLE students ADD COLUMN auth_user_id TEXT")

        # NOT NULL у telegram_id снимается пересборкой таблицы: другого
        # способа у SQLite нет. Данные переносятся целиком, индекс
        # восстанавливается следом.
        ученики = _columns(conn, "students")
        if ученики.get("telegram_id", {}).get("notnull"):
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute(
                "CREATE TABLE students_new ("
                " id INTEGER PRIMARY KEY,"
                " telegram_id INTEGER,"
                " auth_user_id TEXT,"
                " name TEXT,"
                " joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO students_new (id, telegram_id, auth_user_id, name, joined_at) "
                "SELECT id, telegram_id, auth_user_id, name, joined_at FROM students"
            )
            conn.execute("DROP TABLE students")
            conn.execute("ALTER TABLE students_new RENAME TO students")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_telegram_id ON students(telegram_id)"
            )
            conn.execute("PRAGMA foreign_keys = ON")

        conn.execute(
            "CREATE TABLE IF NOT EXISTS consents_web ("
            " auth_user_id TEXT PRIMARY KEY,"
            " given_at TIMESTAMP NOT NULL)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_auth_user ON teachers(auth_user_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_auth_user ON students(auth_user_id)"
        )
        conn.commit()
        return True
    finally:
        conn.close()


if __name__ == "__main__":
    применена = migrate()
    print("миграция применена" if применена else "миграция уже была применена — ничего не делаю")
