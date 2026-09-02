"""
tests/test_api_v1.py — версионированный API для кабинета (блок Ф2).

Проверяет три вещи, каждую из которых легко сломать незаметно:
единый формат ошибки под /api/v1/*, неизменность старых /api/* и то,
что новый эндпоинт нельзя выложить без авторизации.

Настоящих HTTP-запросов в сеть нет: TestClient работает in-process, а
settings подменяются на SQLite во временной папке — тем же приёмом
(frozen dataclass, object.__setattr__ с восстановлением), что в
tests/test_api.py.
"""

from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from bot import texts
from core.config import settings
from core.db import init_db
from web import api_v1
from web.api import app
from web.errors import CODE_NOT_FOUND, CODE_SERVER_UNAVAILABLE

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def isolated_api(tmp_path):
    """Своя база во временной папке: /api/v1/health ходит в базу
    по-настоящему, и без подмены он стучался бы в боевой Supabase."""
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    было = (settings.db_path, settings.db_backend)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    try:
        yield db_path
    finally:
        object.__setattr__(settings, "db_path", было[0])
        object.__setattr__(settings, "db_backend", было[1])


@pytest.fixture
def client():
    return TestClient(app)


# --- /api/v1/health ---

def test_health_отвечает_без_авторизации(isolated_api, client):
    ответ = client.get("/api/v1/health")
    assert ответ.status_code == 200
    тело = ответ.json()
    assert тело["api_version"] == "v1"
    assert тело["database"] == {"available": True, "backend": "sqlite"}


def test_health_при_недоступной_базе_отвечает_503_и_русским_текстом(isolated_api, client, monkeypatch):
    def упасть(*args, **kwargs):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(api_v1, "query", упасть)
    ответ = client.get("/api/v1/health")

    assert ответ.status_code == 503, "watchdog обязан заметить простой по коду состояния"
    тело = ответ.json()
    assert тело["database"]["available"] is False
    assert тело["error"]["code"] == CODE_SERVER_UNAVAILABLE
    assert тело["error"]["message"] == texts.API_SERVER_UNAVAILABLE


def test_health_не_рассказывает_о_причине_отказа(isolated_api, client, monkeypatch):
    """Адрес базы и текст её ошибки в публичном эндпоинте не светятся."""
    def упасть(*args, **kwargs):
        raise RuntimeError("could not connect to db.example.internal:5432 as postgres")

    monkeypatch.setattr(api_v1, "query", упасть)
    текст = client.get("/api/v1/health").text
    assert "db.example.internal" not in текст and "postgres" not in текст


# --- единый формат ошибки ---

def test_неизвестный_путь_v1_отвечает_единым_форматом(isolated_api, client):
    ответ = client.get("/api/v1/такого-эндпоинта-нет")
    assert ответ.status_code == 404
    assert ответ.json() == {"error": {"code": CODE_NOT_FOUND, "message": texts.API_NOT_FOUND}}


def test_текст_ошибки_взят_из_bot_texts_а_не_сочинён(isolated_api, client):
    """Одна ситуация — одна формулировка у бота и у кабинета."""
    сообщение = client.get("/api/v1/такого-эндпоинта-нет").json()["error"]["message"]
    assert сообщение == texts.API_NOT_FOUND
    assert "черновик" not in сообщение.lower()  # текст не про документ, а про доступ


def test_старые_api_отвечают_как_раньше(isolated_api, client):
    """Mini App работает в проде на /api/* — формат его ответов не менялся."""
    ответ = client.get("/api/dashboard")
    assert ответ.status_code == 401
    assert ответ.json() == {"detail": "не авторизован"}


def test_статика_mini_app_отвечает_как_раньше(isolated_api, client):
    ответ = client.get("/такой-статики-нет")
    assert ответ.status_code == 404
    assert ответ.json() == {"detail": "Not Found"}


# --- защита от дыры: эндпоинт без авторизации ---

def _развернуть(routes) -> list[APIRoute]:
    """Плоский список эндпоинтов приложения.

    Свежий FastAPI не расклеивает include_router по app.routes, а кладёт
    туда один объект-обёртку _IncludedRouter. Поэтому в него нужно
    спуститься: иначе поиск «эндпоинтов без авторизации» ничего не
    находит и тест зеленеет впустую. Один раз я на этом уже попался.
    """
    плоско = []
    for route in routes:
        вложенный = getattr(route, "original_router", None)
        if вложенный is not None:
            плоско.extend(_развернуть(вложенный.routes))
        elif isinstance(route, APIRoute):
            плоско.append(route)
    return плоско


def _маршруты_v1() -> list[APIRoute]:
    return [r for r in _развернуть(app.routes) if r.path.startswith(api_v1.router.prefix)]


def test_каждый_эндпоинт_v1_кроме_health_требует_авторизации():
    """
    Сервер публично доступен через Cloudflare Tunnel: эндпоинт без
    зависимости авторизации — дыра. Тест смотрит на собранный граф
    зависимостей FastAPI, поэтому его не обойти ни переносом проверки
    в тело функции, ни забытым Depends.
    """
    без_защиты = []
    for route in _маршруты_v1():
        if route.path in api_v1.PUBLIC_PATHS:
            continue
        имена = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
        if not имена & {"verify_init_data", "current_user"}:
            без_защиты.append(route.path)
    assert без_защиты == [], f"эндпоинты без авторизации: {без_защиты}"


def test_маршруты_v1_вообще_находятся():
    """
    Сторож для самого сторожа выше: если развернуть роутер перестанет
    получаться, тест про авторизацию позеленеет на пустом списке.

    Сверяется с самим роутером, а не со списком путей: список растёт
    каждым блоком, и переписывать его — значит однажды переписать не
    глядя. А вот расхождение «в роутере есть, в приложении нет» —
    настоящая поломка.
    """
    из_приложения = sorted(r.path for r in _маршруты_v1())
    из_роутера = sorted(r.path for r in api_v1.router.routes)
    assert из_приложения == из_роутера
    assert "/api/v1/health" in из_приложения


def test_список_публичных_путей_закрытый():
    """Расширить PUBLIC_PATHS можно только осознанно, правкой этого теста."""
    assert api_v1.PUBLIC_PATHS == frozenset({"/api/v1/health"})


def test_маршруты_v1_зарегистрированы_до_статики():
    """
    Starlette проверяет маршруты в порядке регистрации: роутер, попавший
    после app.mount("/"), не сработает никогда.
    """
    позиции_v1 = [
        i for i, r in enumerate(app.routes)
        if any(x.path.startswith("/api/v1") for x in _развернуть([r]))
    ]
    позиция_статики = next(i for i, r in enumerate(app.routes) if isinstance(r, Mount) and r.path == "")
    assert позиции_v1, "роутер /api/v1 вообще не подключён"
    assert max(позиции_v1) < позиция_статики


# --- CORS ---

def _cors_настройки() -> dict:
    for слой in app.user_middleware:
        if слой.cls is CORSMiddleware:
            return dict(слой.kwargs)
    raise AssertionError("CORSMiddleware не подключён")


def test_cors_разрешает_адрес_кабинета(isolated_api, client):
    ответ = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert ответ.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_не_пускает_посторонний_сайт(isolated_api, client):
    ответ = client.get("/api/v1/health", headers={"Origin": "https://chuzhoy-sait.example"})
    assert "access-control-allow-origin" not in ответ.headers


def test_cors_список_берётся_из_config_а_не_прошит_в_коде():
    assert _cors_настройки()["allow_origins"] == list(settings.cors_origins)


@pytest.mark.parametrize("заголовок", [
    "Authorization", "Content-Type", "X-Telegram-Init-Data", "X-Filename", "X-Konspekt-Mode",
    "X-Transcript-Id", "X-Telegram-Login",
])
def test_cors_пропускает_каждый_заголовок_который_шлёт_кабинет(заголовок):
    """
    Предполётный запрос браузера сверяется ровно с этим списком. Забытое
    имя выглядит для человека как «сервер недоступен» — так и случилось с
    X-Filename при первой сборке загрузки записи урока.
    """
    assert заголовок in _cors_настройки()["allow_headers"]


def test_cors_не_разрешает_куки():
    """Кабинет ходит с заголовком Authorization; куки включать нельзя —
    с ними allow_origins перестаёт быть настоящей границей."""
    assert _cors_настройки()["allow_credentials"] is False


def test_звёздочка_в_cors_origins_запрещена():
    from core.config import _parse_cors_origins

    with pytest.raises(SystemExit):
        _parse_cors_origins("https://x.example,*")


def test_пустой_cors_origins_оставляет_адрес_разработки():
    from core.config import DEFAULT_CORS_ORIGINS, _parse_cors_origins

    assert _parse_cors_origins(None) == DEFAULT_CORS_ORIGINS
    assert _parse_cors_origins("") == DEFAULT_CORS_ORIGINS
    assert _parse_cors_origins("https://mazmun.vercel.app, http://localhost:3000") == (
        "https://mazmun.vercel.app", "http://localhost:3000",
    )
