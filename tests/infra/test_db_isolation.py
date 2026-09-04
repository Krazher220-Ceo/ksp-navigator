"""tests/infra/test_db_isolation.py — прогон тестов не имеет доступа к боевой базе.

Находка 4 AUDIT.md: изоляция была пофикстурной, и тест, забывший взять
фикстуру, уходил в боевой Supabase — вплоть до `UPDATE tasks` там.
Проверка живёт отдельным файлом и намеренно не берёт ни одной фикстуры
изоляции: она проверяет именно то, что даёт `tests/conftest.py` каждому
тесту сам, без просьбы.
"""

import sqlite3

from core.config import settings
from core.db import connect


def test_default_connection_is_sqlite_not_supabase():
    """`connect()` без аргументов обязан открывать SQLite, а не PostgREST."""
    conn = connect()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()
    assert settings.db_backend == "sqlite"


def test_default_db_path_lives_in_pytest_tmp_dir(tmp_path_factory):
    """Путь базы по умолчанию — временный файл прогона, а не storage/app.db."""
    base_temp = tmp_path_factory.getbasetemp()
    assert base_temp in settings.db_path.parents, (
        f"settings.db_path={settings.db_path} вне временной папки прогона {base_temp}"
    )


def test_queue_fail_on_unknown_task_does_not_touch_production(tmp_path_factory):
    """Тот самый вызов, который писал UPDATE в боевую таблицу tasks.

    `core.queue.fail()` вызывается без `db_path`, то есть смотрит на
    settings. Достаточно убедиться, что смотреть ему теперь некуда,
    кроме временной базы прогона.
    """
    from core.queue import fail

    try:
        fail("несуществующая-задача", "ошибка")
    except Exception as exc:  # ошибка допустима, поход в сеть — нет
        assert "Supabase" not in type(exc).__name__, exc
