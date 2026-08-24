#!/usr/bin/env python3
"""
scripts/init_db.py — создание и наполнение базы данных этапа 1.

Применяет storage/schema.sql (идемпотентно, безопасно при повторном
запуске), затем — если цели обучения ещё не загружены — curriculum_seed.sql
(46 целей обучения и 83 записи КТП физики 10 класса).

Ловушка (PLAN_STAGE1.md, Б1.3): curriculum_seed.sql вставляет записи в
ktp_entries со ссылкой teacher_id=1, а в пустой базе такого учителя нет.
Внешний ключ уронит загрузку. Перед применением seed скрипт явно создаёт
учителя-заглушку с id=1.

Флаг --reset удаляет файл базы и пересоздаёт с нуля — с подтверждением
y/N в терминале, если файл уже существует.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402
from core.db import connect  # noqa: E402
from core.db import init_db as apply_schema  # noqa: E402

SCHEMA_PATH = settings.base_dir / "storage" / "schema.sql"
SEED_PATH = settings.base_dir / "curriculum_seed.sql"

SEED_TEACHER_ID = 1
SEED_TEACHER_NAME = "Дмитрий Александрович (сид для разработки)"
SEED_TEACHER_SUBJECT = "физика"


def seed_already_loaded(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM curriculum_objectives").fetchone()
    return row["n"] > 0


def ensure_seed_teacher(conn: sqlite3.Connection) -> None:
    """Создаёт учителя-заглушку с id=1 — на него ссылается ktp_entries
    внутри curriculum_seed.sql. Без него внешний ключ уронит загрузку."""
    exists = conn.execute(
        "SELECT 1 FROM teachers WHERE id = ?", (SEED_TEACHER_ID,)
    ).fetchone()
    if exists:
        return
    conn.execute(
        "INSERT INTO teachers (id, name, subject) VALUES (?, ?, ?)",
        (SEED_TEACHER_ID, SEED_TEACHER_NAME, SEED_TEACHER_SUBJECT),
    )


def load_seed(conn: sqlite3.Connection) -> None:
    if not SEED_PATH.exists():
        print(f"ОШИБКА: не найден {SEED_PATH}", file=sys.stderr)
        raise SystemExit(1)
    ensure_seed_teacher(conn)
    sql = SEED_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def reset_database_files(db_path: Path) -> None:
    """Удаляет файл базы и служебные файлы WAL-режима."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def confirm_reset(db_path: Path) -> bool:
    answer = input(
        f"База {db_path} будет удалена и пересоздана заново. Продолжить? [y/N] "
    ).strip().lower()
    return answer == "y"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создание и наполнение базы данных этапа 1 (МОН РК, приказ №130)."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="удалить существующую базу и пересоздать с нуля (с подтверждением)",
    )
    args = parser.parse_args()

    db_path = settings.db_path

    if args.reset:
        db_existed = db_path.exists()
        if db_existed and not confirm_reset(db_path):
            print("Отменено, база не тронута.")
            return
        reset_database_files(db_path)
        if db_existed:
            print(f"База {db_path} удалена, создаю заново.")
        else:
            print(f"База {db_path} ещё не существовала, создаю с нуля.")

    apply_schema(db_path=db_path, schema_path=SCHEMA_PATH)
    print(f"Схема применена: {SCHEMA_PATH.name} -> {db_path}")

    conn = connect(db_path)
    try:
        if seed_already_loaded(conn):
            print("Seed уже загружен (curriculum_objectives не пуста), пропускаю.")
        else:
            load_seed(conn)
            conn.commit()
            print("Seed загружен: цели обучения и КТП физики 10 класса.")

        objectives = conn.execute(
            "SELECT COUNT(*) AS n FROM curriculum_objectives"
        ).fetchone()["n"]
        ktp = conn.execute("SELECT COUNT(*) AS n FROM ktp_entries").fetchone()["n"]
        print(f"curriculum_objectives: {objectives} строк")
        print(f"ktp_entries: {ktp} строк")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
