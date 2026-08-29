"""Контракт адаптера PostgREST без обращения к реальному Supabase."""

from pathlib import Path

from core.config import settings
from core.db import execute, query, using_supabase


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


def test_supabase_query_sends_sql_and_separate_parameters(monkeypatch):
    import core.db as module

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rows": [{"id": 7, "name": "Учитель"}], "rowcount": 1}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    original = _configure_supabase()
    try:
        monkeypatch.setattr(module.httpx, "Client", FakeClient)
        rows = query("SELECT * FROM teachers WHERE telegram_user_id = ?", (42,))
    finally:
        _restore_settings(original)

    assert rows == [{"id": 7, "name": "Учитель"}]
    assert captured["url"].endswith("/rest/v1/rpc/ksp_execute_sql")
    assert captured["json"] == {
        "statement": "SELECT * FROM teachers WHERE telegram_user_id = ?",
        "parameters": [42],
    }
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_supabase_insert_returns_identifier_and_explicit_path_stays_sqlite(monkeypatch, tmp_path):
    import core.db as module

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rows": [{"id": 12}], "rowcount": 1}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    original = _configure_supabase()
    try:
        monkeypatch.setattr(module.httpx, "Client", FakeClient)
        assert execute("INSERT INTO teachers (name) VALUES (?)", ("Учитель",)) == 12
        assert using_supabase() is True
        assert using_supabase(Path(tmp_path / "test.db")) is False
    finally:
        _restore_settings(original)
