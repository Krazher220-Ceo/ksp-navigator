"""Контракт адаптера PostgREST без обращения к реальному Supabase."""

from pathlib import Path

import pytest

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


# --- Находка 10 AUDIT.md: настоящая причина отказа Postgres в тексте ошибки ---


def test_error_message_includes_response_body():
    """raise_for_status() даёт "Client error '400 Bad Request' for url…"
    одинаково для всех причин; настоящая — в теле ответа, и без неё
    Находки 2 и 3 выглядели в логе неотличимо."""
    import httpx

    from core.db import SupabaseConnection, SupabaseDatabaseError

    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, json):
            request = httpx.Request("POST", url)
            return httpx.Response(
                400,
                request=request,
                text='{"code":"P0001","message":"Плейсхолдеров больше, чем параметров"}',
            )

    original = _configure_supabase()
    try:
        import core.db as db_module

        original_client = db_module.httpx.Client
        db_module.httpx.Client = _FailingClient
        try:
            with pytest.raises(SupabaseDatabaseError) as exc_info:
                SupabaseConnection().execute("SELECT ? AS a", ("Что такое сила?",))
        finally:
            db_module.httpx.Client = original_client
    finally:
        _restore_settings(original)

    message = str(exc_info.value)
    assert "P0001" in message
    assert "Плейсхолдеров больше, чем параметров" in message


def test_error_body_is_trimmed_and_survives_unread_response():
    import httpx

    from core.db import _ERROR_BODY_LIMIT, _error_body

    class _Unread:
        @property
        def text(self):
            raise httpx.ResponseNotRead()

    assert _error_body(Exception()) == ""

    class _Long:
        text = "x" * (_ERROR_BODY_LIMIT + 100)

    long_exc = Exception()
    long_exc.response = _Long()
    assert len(_error_body(long_exc)) <= _ERROR_BODY_LIMIT + 10

    unread_exc = Exception()
    unread_exc.response = _Unread()
    assert _error_body(unread_exc) == ""
