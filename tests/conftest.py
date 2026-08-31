"""tests/conftest.py — обычный прогон тестов не имеет доступа к боевой базе.

Зачем файл: в `.env` рабочей машины стоит `DB_BACKEND=supabase`, и любой
тест, который не взял себе фикстуру изоляции вручную, уходил запросом в
боевой Postgres. Один такой тест выполнял там `UPDATE tasks` (Находка 4
`AUDIT.md`). Здесь изоляция становится свойством прогона, а не привычкой
автора теста.

Что осознанно не делает: не подменяет `uploads_dir`/`generated_dir` — за
ними следят собственные фикстуры тестов, и трогать их «заодно» этот файл
не должен. Не отключает сеть вообще: LLM-провайдеры и Telegram мокаются
на своём уровне.

На что опирается: `core.config.settings` — `frozen`-датакласс, поэтому
подмена идёт через `object.__setattr__`, тем же приёмом, каким это уже
делают `tests/test_bot_handlers.py`, `tests/test_api.py` и
`tests/test_miniapp_flow.py`.
"""

from pathlib import Path

import pytest

from core.config import settings
from core.db import init_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture(scope="session")
def isolated_session_db(tmp_path_factory) -> Path:
    """Одна временная SQLite на весь прогон — общая база «по умолчанию».

    Тесты, которым нужна своя чистая база, по-прежнему берут собственную
    фикстуру и получают отдельный файл; эта нужна лишь для того, чтобы
    запрос без явного `db_path` попадал хоть куда-то, кроме прода.
    """
    db_path = tmp_path_factory.mktemp("session_db") / "session.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    return db_path


@pytest.fixture(autouse=True)
def isolate_database(request, isolated_session_db):
    """На время каждого теста `settings` указывают на временную SQLite.

    Тесты с меткой `supabase` исключены явно: они и существуют затем,
    чтобы ходить в настоящий Postgres (блок Э2). В обычном прогоне
    pytest.ini их не выбирает вовсе (`-m "not supabase"`).
    """
    if request.node.get_closest_marker("supabase") is not None:
        yield
        return

    original_db_path = settings.db_path
    original_db_backend = settings.db_backend
    object.__setattr__(settings, "db_path", isolated_session_db)
    object.__setattr__(settings, "db_backend", "sqlite")
    try:
        yield
    finally:
        object.__setattr__(settings, "db_path", original_db_path)
        object.__setattr__(settings, "db_backend", original_db_backend)
