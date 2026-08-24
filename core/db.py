"""
core/db.py — тонкая обёртка над стандартным sqlite3.

Зачем модуль: единая точка доступа к базе для всех остальных модулей —
чтобы нужные PRAGMA, row_factory и логика транзакций не дублировались
в каждом месте, где нужна база.

Что осознанно не делает: не является ORM. Никакого маппинга таблиц на
классы и никакой генерации SQL по описанию модели — при часе в неделю
такая надстройка стоит времени больше, чем экономит (см. MASTER.md,
ловушка задачи Б1.2).

На что опирается: только стандартная библиотека sqlite3. Путь к базе
по умолчанию берётся из core.config.settings.db_path, но каждая функция
принимает db_path явно — это нужно для тестов (база создаётся в tmp)
и для scripts/init_db.py (пересоздание базы с нуля).
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.config import settings

DEFAULT_SCHEMA_PATH = settings.base_dir / "storage" / "schema.sql"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Открывает соединение с базой с нужными PRAGMA и row_factory.

    row_factory=sqlite3.Row — доступ к столбцам по имени (row["name"]).
    journal_mode=WAL — параллельное чтение во время записи (бот и
    воркер очереди работают в одном процессе, но конкурентный доступ
    возможен).
    foreign_keys=ON — SQLite не включает проверку внешних ключей по
    умолчанию, её нужно явно запрашивать на каждом соединении.
    """
    path = Path(db_path) if db_path is not None else settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(
    db_path: Path | str | None = None,
    schema_path: Path | str | None = None,
) -> None:
    """Применяет schema.sql к базе. Безопасно вызывать повторно —
    все инструкции в schema.sql идут через IF NOT EXISTS."""
    schema = Path(schema_path) if schema_path is not None else DEFAULT_SCHEMA_PATH
    sql = schema.read_text(encoding="utf-8")
    conn = connect(db_path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Path | str | None = None):
    """Контекстный менеджер транзакции: коммит при успешном выходе из
    блока `with`, откат при любом исключении внутри блока.

    Отдаёт открытое соединение — вызывающий код сам делает execute()
    на нём столько раз, сколько нужно внутри одной транзакции:

        with transaction(db_path) as conn:
            conn.execute("INSERT INTO teachers (name) VALUES (?)", (name,))
            conn.execute("INSERT INTO style_profiles (teacher_id) VALUES (?)", (tid,))
    """
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(
    sql: str,
    params: tuple = (),
    db_path: Path | str | None = None,
) -> list[sqlite3.Row]:
    """Выполняет SELECT и возвращает все строки."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(sql, params)
        return cursor.fetchall()
    finally:
        conn.close()


def execute(
    sql: str,
    params: tuple = (),
    db_path: Path | str | None = None,
) -> int | None:
    """Выполняет один INSERT/UPDATE/DELETE, коммитит сразу же и
    возвращает lastrowid (для INSERT в таблицу с INTEGER PRIMARY KEY)."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def executemany(
    sql: str,
    params_list: list[tuple],
    db_path: Path | str | None = None,
) -> None:
    """Выполняет один SQL-запрос для списка наборов параметров одной транзакцией."""
    conn = connect(db_path)
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()
