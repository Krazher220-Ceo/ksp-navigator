#!/usr/bin/env python3
"""Одноразово переносит данные SQLite в уже развёрнутую схему Supabase.

Сначала проверяет SQLite, создаёт проверяемую резервную копию и только при
--apply отправляет записи через PostgREST. Схему этот скрипт не создаёт:
storage/schema_supabase.sql выполняется в SQL Editor Supabase один раз.
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings  # noqa: E402


TABLES_IN_ORDER = (
    "teachers",
    "curriculum_objectives",
    "templates",
    "style_profiles",
    "template_selections",
    "ktp_entries",
    "generated_ksp",
    "tasks",
    "usage_daily",
    "admin_access",
    "transcripts",
    "konspekty",
    "incidents",
    "worker_heartbeat",
)
IDENTITY_TABLES = ("teachers", "style_profiles", "templates", "ktp_entries", "incidents")
BATCH_SIZE = 100


class MigrationError(RuntimeError):
    """Перенос нельзя безопасно продолжать без исправления причины."""


class SupabaseRest:
    """Минимальный PostgREST-клиент только для одноразового переноса."""

    def __init__(self) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise MigrationError("не заданы SUPABASE_URL или SUPABASE_SERVICE_ROLE_KEY")
        self.base_url = settings.supabase_url.rstrip("/") + "/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        }

    def table_exists(self, table: str) -> bool:
        with httpx.Client(timeout=30) as client:
            response = client.get(f"{self.base_url}/{table}?select=*&limit=1", headers=self.headers)
        return response.status_code == 200

    def count_rows(self, table: str) -> int:
        headers = {**self.headers, "Prefer": "count=exact", "Range": "0-0"}
        with httpx.Client(timeout=30) as client:
            response = client.get(f"{self.base_url}/{table}?select=*", headers=headers)
        if response.status_code >= 400:
            raise MigrationError(f"Supabase не прочитал таблицу {table} (HTTP {response.status_code})")
        content_range = response.headers.get("content-range", "")
        try:
            return int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError) as exc:
            raise MigrationError(f"Supabase не вернул количество строк таблицы {table}") from exc

    def upsert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        with httpx.Client(timeout=60) as client:
            response = client.post(f"{self.base_url}/{table}", headers=headers, json=rows)
        if response.status_code >= 400:
            raise MigrationError(f"Supabase не записал таблицу {table} (HTTP {response.status_code})")

    def execute_sql(self, statement: str) -> None:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/rpc/ksp_execute_sql",
                headers={**self.headers, "Content-Type": "application/json"},
                json={"statement": statement, "parameters": []},
            )
        if response.status_code >= 400:
            raise MigrationError(f"Supabase не выполнил служебный запрос (HTTP {response.status_code})")


def open_sqlite(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise MigrationError(f"не найден SQLite-файл: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        conn.close()
        raise MigrationError(f"SQLite integrity_check вернул {integrity!r}")
    return conn


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def read_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def create_backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"app-before-supabase-{stamp}.db"
    shutil.copy2(db_path, backup_path)
    with sqlite3.connect(backup_path) as backup_conn:
        result = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise MigrationError(f"резервная копия не прошла integrity_check: {backup_path}")
    return backup_path


def check_remote_schema(remote: SupabaseRest) -> None:
    missing = [table for table in TABLES_IN_ORDER if not remote.table_exists(table)]
    if missing:
        names = ", ".join(missing)
        raise MigrationError(
            "в Supabase отсутствует схема: " + names
            + ". Выполните storage/schema_supabase.sql в SQL Editor и повторите команду."
        )


def migrate(db_path: Path, backup_dir: Path, apply: bool) -> dict[str, tuple[int, int]]:
    conn = open_sqlite(db_path)
    try:
        local_tables = sqlite_tables(conn)
        # Старый app.db мог быть создан до аддитивных миграций (например,
        # без admin_access). В новой схеме такие таблицы будут пустыми, а не
        # поводом потерять перенос остальных данных.
        local_rows = {
            table: read_rows(conn, table) if table in local_tables else []
            for table in TABLES_IN_ORDER
        }
    finally:
        conn.close()

    if not apply:
        return {table: (len(rows), -1) for table, rows in local_rows.items()}

    backup_path = create_backup(db_path, backup_dir)
    print(f"Резервная копия проверена: {backup_path}")
    remote = SupabaseRest()
    check_remote_schema(remote)

    for table in TABLES_IN_ORDER:
        rows = local_rows[table]
        for start in range(0, len(rows), BATCH_SIZE):
            remote.upsert_rows(table, rows[start : start + BATCH_SIZE])

    for table in IDENTITY_TABLES:
        remote.execute_sql(
            "SELECT setval(pg_get_serial_sequence('" + table + "', 'id'), "
            "COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)"
        )

    result = {table: (len(local_rows[table]), remote.count_rows(table)) for table in TABLES_IN_ORDER}
    mismatches = {table: counts for table, counts in result.items() if counts[0] != counts[1]}
    if mismatches:
        details = ", ".join(f"{table}: SQLite={left}, Supabase={right}" for table, (left, right) in mismatches.items())
        raise MigrationError("счётчики строк не совпали: " + details)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Перенос SQLite в Supabase с проверкой строк.")
    parser.add_argument("--apply", action="store_true", help="создать бэкап и отправить данные в Supabase")
    parser.add_argument("--db-path", type=Path, default=settings.db_path, help="путь к исходному app.db")
    parser.add_argument("--backup-dir", type=Path, default=settings.base_dir / "backup", help="папка проверяемых бэкапов")
    args = parser.parse_args()

    counts = migrate(args.db_path, args.backup_dir, args.apply)
    for table, (local_count, remote_count) in counts.items():
        if args.apply:
            print(f"{table}: SQLite={local_count}, Supabase={remote_count}")
        else:
            print(f"{table}: SQLite={local_count}")
    if not args.apply:
        print("Проверка завершена без отправки данных. Для переноса повторите с --apply.")


if __name__ == "__main__":
    try:
        main()
    except MigrationError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        raise SystemExit(1)
