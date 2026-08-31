#!/usr/bin/env python3
"""scripts/backup_supabase.py — ежедневный бэкап боевой базы Supabase, блок Э4.

Зачем модуль: с переезда на Supabase (блок С2) storage/app.db больше не
пишется, а scripts/backup_db.sh продолжал снимать копию именно с него —
резервная копия молча замерла в момент переезда, хотя лог рапортовал "OK"
(разбор — PLAN.md, блок Э4). Этот скрипт снимает копию с настоящей боевой
базы: читает все таблицы через core.db.query (тот же httpx/RPC, каким бот
и так читает Supabase) и складывает их в файл SQLite со схемой
storage/schema.sql. Формат выбран не JSON-дампом, а именно SQLite по двум
причинам: README называет DB_BACKEND=sqlite резервным режимом — значит,
восстановление это скопировать файл и переключить одну переменную; а
целостность бэкапа проверяется штатной PRAGMA integrity_check, а не
самодельной сверкой JSON.

Список таблиц не хранится вторым списком руками — он вычисляется из
storage/schema.sql, чтобы таблица, забытая в одном из двух списков файлов
схемы, не выпадала из бэкапа молча.

Что осознанно не делает: не подключается к Postgres напрямую (это была бы
новая зависимость и новый секрет) и не бэкапит саму схему/функцию
ksp_execute_sql — они не данные, а код, и уже лежат в
storage/schema_supabase.sql в репозитории.
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402
from core.config import settings  # noqa: E402


PAGE_SIZE = 500
RETENTION_DAYS = 14
_TABLE_NAME_RE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)", re.IGNORECASE)


class BackupError(RuntimeError):
    """Бэкап нельзя считать успешным — продолжать (например, чистить старые) нет смысла."""


def tables_from_schema(schema_path: Path) -> list[str]:
    """Список таблиц берётся из storage/schema.sql, а не пишется вторым списком (Э4).

    Комментарии в schema.sql сами упоминают "CREATE TABLE IF NOT EXISTS" текстом
    (например, при объяснении, почему аддитивная колонка нужна отдельной
    миграцией) — такие строки нужно вырезать до поиска, иначе кусок
    комментария попадёт в список таблиц как настоящее имя.
    """
    lines = schema_path.read_text(encoding="utf-8").splitlines()
    without_comments = "\n".join(line for line in lines if not line.strip().startswith("--"))
    tables = _TABLE_NAME_RE.findall(without_comments)
    if not tables:
        raise BackupError(f"в {schema_path} не найдено ни одной таблицы CREATE TABLE IF NOT EXISTS")
    return tables


def fetch_all_rows(table: str) -> list[dict]:
    """Читает таблицу порциями, чтобы не держать её целиком в памяти (ktp_entries уже растёт)."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = db.query(f"select * from {table} limit ? offset ?", (PAGE_SIZE, offset))
        page = [dict(row) for row in page]
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def write_sqlite_backup(schema_path: Path, backup_path: Path, tables_rows: dict[str, list[dict]]) -> None:
    """Создаёт файл SQLite со схемой storage/schema.sql, заполненный данными из Supabase."""
    if backup_path.exists():
        backup_path.unlink()
    conn = sqlite3.connect(backup_path)
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        for table, rows in tables_rows.items():
            if not rows:
                continue
            columns = list(rows[0].keys())
            column_list = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            values = [tuple(row.get(column) for column in columns) for row in rows]
            conn.executemany(
                f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
                values,
            )
        conn.commit()
    finally:
        conn.close()


def check_integrity(backup_path: Path) -> str:
    conn = sqlite3.connect(backup_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def cleanup_old_backups(backup_dir: Path, retention_days: int = RETENTION_DAYS) -> list[Path]:
    """Удаляет файлы supabase_*.db старше retention_days — форма scripts/backup_db.sh."""
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = []
    for path in sorted(backup_dir.glob("supabase_*.db")):
        if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
            path.unlink()
            removed.append(path)
    return removed


def run(backup_dir: Path, schema_path: Path) -> tuple[Path, dict[str, int]]:
    """Снимает бэкап и возвращает путь к файлу и число строк по каждой таблице."""
    if not db.using_supabase():
        raise BackupError(
            "DB_BACKEND должен быть 'supabase' — этот скрипт снимает бэкап с боевой базы, "
            "а не с локального резервного режима (для него есть scripts/backup_db.sh)"
        )

    tables = tables_from_schema(schema_path)
    tables_rows = {table: fetch_all_rows(table) for table in tables}

    backup_dir.mkdir(parents=True, exist_ok=True)
    date_suffix = datetime.now().strftime("%Y-%m-%d")
    backup_path = backup_dir / f"supabase_{date_suffix}.db"
    write_sqlite_backup(schema_path, backup_path, tables_rows)

    integrity = check_integrity(backup_path)
    if integrity != "ok":
        raise BackupError(f"{backup_path} не прошёл integrity_check: {integrity}")

    counts = {table: len(rows) for table, rows in tables_rows.items()}
    return backup_path, counts


def _log(message: str, log_file: Path) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} {message}"
    print(line)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ежедневный бэкап боевой базы Supabase в файл SQLite.")
    parser.add_argument("--backup-dir", type=Path, default=settings.base_dir / "backup")
    parser.add_argument("--schema-path", type=Path, default=settings.base_dir / "storage" / "schema.sql")
    parser.add_argument("--log-file", type=Path, default=Path.home() / "logs" / "backup.log")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    args = parser.parse_args()

    try:
        backup_path, counts = run(args.backup_dir, args.schema_path)
    except BackupError as exc:
        _log(f"ОШИБКА: {exc}", args.log_file)
        raise SystemExit(1)

    total_rows = sum(counts.values())
    size_bytes = backup_path.stat().st_size
    _log(
        f"OK: бэкап Supabase создан -> {backup_path} "
        f"({size_bytes} байт, {len(counts)} таблиц, {total_rows} строк)",
        args.log_file,
    )

    removed = cleanup_old_backups(args.backup_dir, args.retention_days)
    if removed:
        names = ", ".join(path.name for path in removed)
        _log(f"удалены старые бэкапы (>{args.retention_days} дней): {names}", args.log_file)


if __name__ == "__main__":
    main()
