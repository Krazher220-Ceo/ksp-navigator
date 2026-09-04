"""Бэкап Supabase собирается из поддельного источника данных, без сети (блок Э4)."""

import importlib.util
import os
import re
import sqlite3
import time
from pathlib import Path

import pytest

from core.config import settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "backup_supabase.py"

spec = importlib.util.spec_from_file_location("backup_supabase", SCRIPT_PATH)
backup_supabase = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(backup_supabase)


FAKE_ROWS = {
    "teachers": [
        {
            "id": 1,
            "name": "Иванов",
            "subject": "физика",
            "school": None,
            "telegram_user_id": 111,
            "created_at": "2026-08-01 00:00:00",
        },
        {
            "id": 2,
            "name": "Петров",
            "subject": "химия",
            "school": None,
            "telegram_user_id": 222,
            "created_at": "2026-08-02 00:00:00",
        },
    ],
    "curriculum_objectives": [
        {
            "code": "10.1.1.1",
            "grade": 10,
            "section": "Механика",
            "subsection": None,
            "description": "описание",
            "thinking_level": None,
        },
    ],
    "tasks": [
        {
            "id": "t1",
            "type": "generate_ksp",
            "status": "done",
            "payload": "{}",
            "result": None,
            "error": None,
            "telegram_chat_id": 111,
            "retries": 0,
            "created_at": "2026-08-01 00:00:00",
            "updated_at": None,
        },
    ],
}

_TABLE_RE = re.compile(r"from (\w+)", re.IGNORECASE)


def _configure_supabase():
    original = {
        "db_backend": settings.db_backend,
        "supabase_url": settings.supabase_url,
        "supabase_service_role_key": settings.supabase_service_role_key,
    }
    object.__setattr__(settings, "db_backend", "supabase")
    object.__setattr__(settings, "supabase_url", "https://example.supabase.co")
    object.__setattr__(settings, "supabase_service_role_key", "test-key")
    return original


def _restore_settings(original):
    for field, value in original.items():
        object.__setattr__(settings, field, value)


class FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return {"rows": self._rows, "rowcount": len(self._rows)}


class FakeClient:
    """Подменяет httpx.Client: отдаёт поддельные страницы вместо реального Supabase."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers, json):
        statement = json["statement"]
        limit, offset = json["parameters"]
        match = _TABLE_RE.search(statement)
        table = match.group(1) if match else ""
        all_rows = FAKE_ROWS.get(table, [])
        page = all_rows[offset : offset + limit]
        return FakeResponse(page)


def test_backup_supabase_creates_valid_sqlite_file(monkeypatch, tmp_path):
    import core.db as db_module

    original = _configure_supabase()
    try:
        monkeypatch.setattr(db_module.httpx, "Client", FakeClient)
        backup_dir = tmp_path / "backup"
        schema_path = PROJECT_ROOT / "storage" / "schema.sql"
        backup_path, counts = backup_supabase.run(backup_dir, schema_path)
    finally:
        _restore_settings(original)

    assert counts["teachers"] == 2
    assert counts["curriculum_objectives"] == 1
    assert counts["tasks"] == 1
    assert counts.get("konspekty", 0) == 0
    assert backup_path.name.startswith("supabase_")

    conn = sqlite3.connect(backup_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == 2
        assert conn.execute("SELECT name FROM teachers WHERE id = 1").fetchone()[0] == "Иванов"
        assert conn.execute("SELECT COUNT(*) FROM konspekty").fetchone()[0] == 0
    finally:
        conn.close()


# --- Находка 9 AUDIT.md: страницы бэкапа обязаны иметь устойчивый порядок ---

# Больше одной страницы (PAGE_SIZE = 500) — именно на этом объёме
# начинает работать пагинация, ради которой всё и написано.
_MANY_TEACHERS = [
    {
        "id": i,
        "name": f"Педагог {i}",
        "subject": "физика",
        "school": None,
        "telegram_user_id": 1000 + i,
        "created_at": "2026-08-01 00:00:00",
    }
    for i in range(1, 1201)
]


class UnstableOrderClient:
    """Postgres без ORDER BY не обязан отдавать строки в одном и том же
    порядке между запросами — этот клиент ведёт себя ровно так.

    Запрос с "order by" получает стабильно отсортированный список,
    запрос без него — список, перевёрнутый на каждом втором обращении.
    Это не выдумка про Postgres, а самый мягкий способ смоделировать то,
    что он разрешает себе делать.
    """

    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers, json):
        statement = json["statement"]
        limit, offset = json["parameters"]
        match = _TABLE_RE.search(statement)
        table = match.group(1) if match else ""
        all_rows = _MANY_TEACHERS if table == "teachers" else FAKE_ROWS.get(table, [])

        if "order by" in statement.lower():
            # "order by 1" — сортировка по первой колонке, какой бы она
            # ни называлась: у teachers это id, у curriculum_objectives — code.
            ordered = sorted(all_rows, key=lambda row: str(next(iter(row.values()))))
        else:
            UnstableOrderClient.calls += 1
            ordered = list(all_rows)
            if UnstableOrderClient.calls % 2 == 0:
                ordered.reverse()

        return FakeResponse(ordered[offset : offset + limit])


def test_backup_pages_are_stable_across_requests(monkeypatch, tmp_path):
    """Без ORDER BY часть строк дублируется, часть теряется молча —
    и молчаливая потеря опаснее: бэкап выглядит успешным."""
    import core.db as db_module

    UnstableOrderClient.calls = 0
    original = _configure_supabase()
    try:
        monkeypatch.setattr(db_module.httpx, "Client", UnstableOrderClient)
        backup_path, counts = backup_supabase.run(tmp_path / "backup", PROJECT_ROOT / "storage" / "schema.sql")
    finally:
        _restore_settings(original)

    assert counts["teachers"] == len(_MANY_TEACHERS)

    conn = sqlite3.connect(backup_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0] == len(_MANY_TEACHERS)
        saved_ids = [row[0] for row in conn.execute("SELECT id FROM teachers ORDER BY id")]
        assert saved_ids == [row["id"] for row in _MANY_TEACHERS]
    finally:
        conn.close()


def test_paging_query_asks_postgres_for_an_order(monkeypatch):
    """Прямая проверка самого запроса: без ORDER BY гарантии выше не
    существует, сколько её ни тестируй поведением."""
    import core.db as db_module

    seen: list[str] = []

    class RecordingClient(UnstableOrderClient):
        def post(self, url, headers, json):
            seen.append(json["statement"])
            return FakeResponse([])

    original = _configure_supabase()
    try:
        monkeypatch.setattr(db_module.httpx, "Client", RecordingClient)
        backup_supabase.fetch_all_rows("teachers")
    finally:
        _restore_settings(original)

    assert seen and all("order by" in statement.lower() for statement in seen), seen


def test_backup_supabase_refuses_when_backend_is_not_supabase(tmp_path):
    original = settings.db_backend
    object.__setattr__(settings, "db_backend", "sqlite")
    try:
        with pytest.raises(backup_supabase.BackupError):
            backup_supabase.run(tmp_path / "backup", PROJECT_ROOT / "storage" / "schema.sql")
    finally:
        object.__setattr__(settings, "db_backend", original)


def test_tables_from_schema_reads_real_schema_without_duplicates():
    tables = backup_supabase.tables_from_schema(PROJECT_ROOT / "storage" / "schema.sql")
    assert "teachers" in tables
    assert "consents" in tables
    assert len(tables) == len(set(tables))


def test_tables_from_schema_ignores_mentions_inside_comments(tmp_path):
    # storage/schema.sql настоящий уже наступал на эту грабли: комментарий
    # к колонке teachers.school буквально содержит текст "CREATE TABLE IF
    # NOT EXISTS колонку не добавит" — наивный поиск по всему файлу берёт
    # "колонку" за имя таблицы. Проверяем на отдельном файле, чтобы тест не
    # зависел от того, останется ли этот конкретный комментарий в схеме.
    fake_schema = tmp_path / "schema.sql"
    fake_schema.write_text(
        "CREATE TABLE IF NOT EXISTS teachers (\n"
        "    id INTEGER PRIMARY KEY,\n"
        "    -- CREATE TABLE IF NOT EXISTS колонку не добавит без миграции\n"
        "    school TEXT\n"
        ");\n"
        "CREATE TABLE IF NOT EXISTS consents (\n"
        "    telegram_user_id INTEGER PRIMARY KEY\n"
        ");\n",
        encoding="utf-8",
    )

    tables = backup_supabase.tables_from_schema(fake_schema)

    assert tables == ["teachers", "consents"]
    assert "колонку" not in tables


def test_tables_from_schema_matches_exact_set_of_real_tables():
    # Явный список — намеренное дублирование storage/schema.sql: если сюда
    # забудут добавить новую таблицу (или парсер снова начнёт цеплять лишнее
    # из комментариев), тест покажет расхождение, а не молча пропустит его.
    expected = {
        "teachers",
        "style_profiles",
        "templates",
        "template_selections",
        "curriculum_objectives",
        "ktp_entries",
        "generated_ksp",
        "tasks",
        "usage_daily",
        "admin_access",
        "transcripts",
        "konspekty",
        "incidents",
        "worker_heartbeat",
        "consents",
        # Ф4 (FRONTEND_PLAN.md): согласие того, кто вошёл по почте.
        # В бэкап входит на общих основаниях — согласие, которого нет в
        # резервной копии, придётся спрашивать заново.
        "consents_web",
        # Вход подтверждением в боте (core/login_codes.py). Талоны
        # живут пять минут, но в бэкап входят на общих основаниях:
        # список таблиц обязан совпадать со схемой целиком, иначе
        # завтра так же «незаметно» выпадет таблица с данными.
        "login_codes",
        "classes",
        "students",
        "class_members",
    }
    tables = backup_supabase.tables_from_schema(PROJECT_ROOT / "storage" / "schema.sql")
    assert set(tables) == expected


def test_cleanup_old_backups_removes_only_files_older_than_retention(tmp_path):
    old_file = tmp_path / "supabase_2020-01-01.db"
    new_file = tmp_path / "supabase_2099-01-01.db"
    old_file.write_text("x")
    new_file.write_text("x")
    old_timestamp = time.time() - 20 * 86400
    os.utime(old_file, (old_timestamp, old_timestamp))

    removed = backup_supabase.cleanup_old_backups(tmp_path, retention_days=14)

    assert removed == [old_file]
    assert not old_file.exists()
    assert new_file.exists()
