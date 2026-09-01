"""
tests/test_current_user.py — единая зависимость входа (блок Ф3).

Проверяет, что две двери — JWT кабинета и initData Telegram — не стали
дырой: не сработала ни одна, значит 401; база не ответила при
определении роли, значит честный 503, а не тихий проход без роли.

Зависимость проверяется на отдельном крошечном приложении, а не на
web.api: в проде эндпоинтов с current_user пока нет (они приезжают
блоками Ф5–Ф9), а проверять её надо уже сейчас.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.db import SupabaseDatabaseError, execute, init_db
from tests.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web import auth as web_auth
from web.auth import ROLE_STUDENT, ROLE_TEACHER, CurrentUser, current_user
from web.errors import CODE_NOT_AUTHORIZED, CODE_SERVER_UNAVAILABLE, install_error_handlers

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
BOT_TOKEN = "123456:AAEtest-bot-token-for-tests-only"


@pytest.fixture
def приложение():
    """Одна защищённая ручка под /api/v1 — путь важен: единый формат
    ошибки включается именно по нему."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/v1/кто-я")
    async def кто_я(человек: CurrentUser = Depends(current_user)) -> dict:
        return {"user_id": человек.user_id, "telegram_user_id": человек.telegram_user_id,
                "role": человек.role, "auth_user_id": человек.auth_user_id}

    return TestClient(app)


@pytest.fixture
def база(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    было = (settings.db_path, settings.db_backend, settings.telegram_bot_token,
            settings.supabase_jwt_secret, settings.supabase_url)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    object.__setattr__(settings, "supabase_jwt_secret", СЕКРЕТ)
    object.__setattr__(settings, "supabase_url", АДРЕС)
    try:
        yield db_path
    finally:
        for имя, значение in zip(
            ("db_path", "db_backend", "telegram_bot_token", "supabase_jwt_secret", "supabase_url"), было
        ):
            object.__setattr__(settings, имя, значение)


def init_data(user_id: int, bot_token: str = BOT_TOKEN) -> str:
    поля = {
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "Т"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
    }
    строка = "\n".join(f"{k}={v}" for k, v in sorted(поля.items()))
    ключ = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    поля["hash"] = hmac.new(ключ, строка.encode(), hashlib.sha256).hexdigest()
    return urlencode(поля)


# --- ни одна дверь не сработала ---

def test_без_заголовков_401(база, приложение):
    ответ = приложение.get("/api/v1/кто-я")
    assert ответ.status_code == 401
    assert ответ.json()["error"]["code"] == CODE_NOT_AUTHORIZED


# Значения заголовков только из ASCII: HTTP другого и не разрешает, и
# на кириллице падает сам клиент, не дойдя до проверки.
@pytest.mark.parametrize("заголовок", [
    {"Authorization": "Bearer musor"},
    {"Authorization": "Basic dXNlcjpwYXNz"},
    {"Authorization": "stroka-bez-shemy"},
    {"X-Telegram-Init-Data": "hash=deadbeef&auth_date=1"},
])
def test_негодные_ключи_дают_401_а_не_проход(база, приложение, заголовок):
    ответ = приложение.get("/api/v1/кто-я", headers=заголовок)
    assert ответ.status_code == 401


def test_подделанный_jwt_не_пускает(база, приложение):
    ответ = приложение.get("/api/v1/кто-я", headers={
        "Authorization": "Bearer " + собрать_токен(секрет="не-наш-секрет"),
    })
    assert ответ.status_code == 401


# --- дверь Telegram ---

def test_педагог_из_telegram_получает_роль_teacher(база, приложение):
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тестов Т.", "физика", 777), db_path=база)
    тело = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(777)}).json()
    assert тело == {"user_id": "tg:777", "telegram_user_id": 777, "role": ROLE_TEACHER, "auth_user_id": None}


def test_ученик_из_telegram_получает_роль_student(база, приложение):
    execute("INSERT INTO students (telegram_id, name) VALUES (?, ?)", (888, "Ученик У."), db_path=база)
    тело = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(888)}).json()
    assert тело["role"] == ROLE_STUDENT
    assert тело["telegram_user_id"] == 888


def test_ученик_важнее_педагога_если_человек_и_там_и_там(база, приложение):
    """Цена ошибки в эту сторону меньше: ученик увидит меньше, чем мог
    бы, а педагог, принятый за ученика, завёл бы класс на чужого ребёнка."""
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Оба Сразу", "физика", 999), db_path=база)
    execute("INSERT INTO students (telegram_id, name) VALUES (?, ?)", (999, "Оба Сразу"), db_path=база)
    тело = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(999)}).json()
    assert тело["role"] == ROLE_STUDENT


def test_незнакомый_человек_из_telegram_опознан_но_без_роли(база, приложение):
    """Роль None — это «ещё не выбрал», а не отказ: так выглядит человек
    до /start. Пускать его дальше или нет — решает эндпоинт."""
    тело = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(1234)}).json()
    assert тело["role"] is None
    assert тело["telegram_user_id"] == 1234


def test_чужая_подпись_initdata_не_пускает(база, приложение):
    ответ = приложение.get("/api/v1/кто-я", headers={
        "X-Telegram-Init-Data": init_data(777, bot_token="999:чужой-токен-бота"),
    })
    assert ответ.status_code == 401


def test_база_молчит_значит_503_а_не_тихий_проход(база, приложение, monkeypatch):
    """
    Ровно тот же принцип, что у согласия (CONSENT_CHECK_UNAVAILABLE):
    не смогли определить роль — говорим прямо. Пустить без роли значило
    бы отдать педагогические экраны кому попало.
    """
    def упасть(*args, **kwargs):
        raise SupabaseDatabaseError("Supabase не ответил")

    monkeypatch.setattr(web_auth, "query", упасть)
    ответ = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(777)})
    assert ответ.status_code == 503
    assert ответ.json()["error"]["code"] == CODE_SERVER_UNAVAILABLE


# --- дверь кабинета ---

def test_вход_по_почте_опознан(база, приложение):
    тело = приложение.get("/api/v1/кто-я", headers={
        "Authorization": "Bearer " + собрать_токен(),
    }).json()
    assert тело["user_id"] == "auth:9c1e7b30-0000-4000-8000-000000000001"
    assert тело["auth_user_id"] == "9c1e7b30-0000-4000-8000-000000000001"
    assert тело["telegram_user_id"] is None


def test_вход_по_почте_пока_без_роли(база, приложение):
    """
    Связать вошедшего по почте с профилем не с чем: в teachers и
    students нет колонки под идентификатор Supabase Auth. Завести её
    может только автор (FRONTEND_PLAN.md, раздел 6, пункт 4). До тех пор
    роль честно None — выдавать её по совпадению почты нельзя, почты в
    teachers тоже нет.
    """
    assert приложение.get("/api/v1/кто-я", headers={
        "Authorization": "Bearer " + собрать_токен(),
    }).json()["role"] is None


def test_если_пришли_оба_ключа_выбирается_jwt(база, приложение):
    """initData Telegram подставляет сам; Authorization человек прислал
    осознанно, и его выбор важнее."""
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тестов Т.", "физика", 777), db_path=база)
    тело = приложение.get("/api/v1/кто-я", headers={
        "Authorization": "Bearer " + собрать_токен(),
        "X-Telegram-Init-Data": init_data(777),
    }).json()
    assert тело["telegram_user_id"] is None and тело["user_id"].startswith("auth:")


def test_ненастроенный_секрет_не_превращается_в_проход(база, приложение):
    object.__setattr__(settings, "supabase_jwt_secret", None)
    ответ = приложение.get("/api/v1/кто-я", headers={"Authorization": "Bearer " + собрать_токен()})
    assert ответ.status_code == 401


# --- контракт структуры ---

def test_третьей_роли_не_существует():
    assert web_auth.ROLES == (ROLE_TEACHER, ROLE_STUDENT)


def test_служебный_ключ_supabase_во_фронтенд_не_уезжает():
    """
    SUPABASE_SERVICE_ROLE_KEY даёт полный доступ к базе мимо всех
    проверок. Во frontend/ его не должно быть ни в коде, ни в примерах.
    """
    frontend = PROJECT_ROOT / "frontend"
    найдено = [
        путь.relative_to(PROJECT_ROOT)
        for путь in frontend.glob("**/*")
        if путь.is_file()
        and "node_modules" not in путь.parts and ".next" not in путь.parts
        and путь.suffix in {".ts", ".tsx", ".js", ".mjs", ".json", ".css", ".example", ""}
        # Упоминание в комментарии «этот ключ сюда не попадает» — не
        # утечка, а объяснение. Ищем сам ключ, а не разговор о нём.
        and "SERVICE_ROLE" in путь.read_text(encoding="utf-8", errors="ignore")
        and "не попадает" not in путь.read_text(encoding="utf-8", errors="ignore")
    ]
    assert найдено == [], f"служебный ключ упомянут во фронтенде: {найдено}"
