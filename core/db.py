"""Единая точка доступа к SQLite и Supabase PostgREST.

SQLite остаётся локальным резервом и используется тестами по явному пути.
Supabase выполняет параметризованные запросы через закрытую RPC-функцию.
Модуль не является ORM и не создаёт удалённую схему: это делает SQL-скрипт.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from core.config import settings


DEFAULT_SCHEMA_PATH = settings.base_dir / "storage" / "schema.sql"
_SUPABASE_RPC_NAME = "ksp_execute_sql"


class SupabaseDatabaseError(RuntimeError):
    """PostgREST не принял запрос к Supabase или вернул неполный ответ."""


class RemoteCursor:
    """Небольшой аналог sqlite3.Cursor для уже полученного ответа RPC."""

    def __init__(self, rows: list[dict[str, Any]], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount
        self.lastrowid = rows[0].get("id") if len(rows) == 1 else None

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class SupabaseConnection:
    """Синхронный адаптер существующего cursor-интерфейса к PostgREST RPC."""

    def __init__(self) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise SupabaseDatabaseError(
                "для Supabase не заданы SUPABASE_URL или SUPABASE_SERVICE_ROLE_KEY"
            )
        self._url = settings.supabase_url.rstrip("/") + f"/rest/v1/rpc/{_SUPABASE_RPC_NAME}"
        self._headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def execute(self, sql: str, params: tuple = ()) -> RemoteCursor:
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    self._url,
                    headers=self._headers,
                    json={"statement": sql, "parameters": list(params)},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SupabaseDatabaseError(f"Supabase не выполнил запрос: {exc}") from exc

        try:
            payload = response.json()
            rows = payload["rows"]
            rowcount = payload["rowcount"]
        except (TypeError, ValueError, KeyError) as exc:
            raise SupabaseDatabaseError("Supabase вернул ответ в неизвестном формате") from exc
        if not isinstance(rows, list) or not isinstance(rowcount, int):
            raise SupabaseDatabaseError("Supabase вернул некорректные строки запроса")
        return RemoteCursor(rows, rowcount)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        for params in params_list:
            self.execute(sql, params)

    def commit(self) -> None:
        """Каждый RPC-вызов PostgREST уже завершается одной транзакцией."""

    def rollback(self) -> None:
        """Незавершённой локальной транзакции при PostgREST не существует."""

    def close(self) -> None:
        """Клиенты httpx закрываются внутри execute()."""


def _use_supabase(db_path: Path | str | None) -> bool:
    """Явный путь всегда означает SQLite: так изолированы тесты и миграции."""
    return db_path is None and settings.db_backend == "supabase"


def using_supabase(db_path: Path | str | None = None) -> bool:
    """Публичный выбор диалекта для редких запросов с разным SQL-синтаксисом."""
    return _use_supabase(db_path)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection | SupabaseConnection:
    """Открывает SQLite либо адаптер Supabase согласно текущим настройкам."""
    if _use_supabase(db_path):
        return SupabaseConnection()

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
    """Применяет SQLite-схему; удалённая схема разворачивается в SQL Editor."""
    if _use_supabase(db_path):
        raise SupabaseDatabaseError(
            "схема Supabase применяется файлом storage/schema_supabase.sql через SQL Editor"
        )
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
    """Даёт совместимый интерфейс транзакции для SQLite и одиночных RPC-вызовов."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple = (), db_path: Path | str | None = None) -> list[Any]:
    """Выполняет SELECT и возвращает строки с доступом по именам столбцов."""
    conn = connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def execute(sql: str, params: tuple = (), db_path: Path | str | None = None) -> int | None:
    """Выполняет INSERT/UPDATE/DELETE и возвращает первичный ключ нового объекта."""
    conn = connect(db_path)
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def executemany(sql: str, params_list: list[tuple], db_path: Path | str | None = None) -> None:
    """Выполняет один запрос для набора параметров через выбранный backend."""
    conn = connect(db_path)
    try:
        conn.executemany(sql, params_list)
        conn.commit()
    finally:
        conn.close()
