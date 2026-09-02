"""
tests/test_telegram_login.py — вход в кабинет через Telegram Login Widget.

Третья дверь: JWT кабинета, initData из Mini App и данные виджета для
обычного браузера. У виджета СВОЯ схема подписи, и перепутать её с
initData легко — код при этом не падает, просто вход молча перестаёт
работать. Поэтому здесь тот же тест с подделанной подписью, что и у двух
других дверей.
"""

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.db import execute, init_db, query
from tests.test_current_user import BOT_TOKEN, init_data
from tests.test_jwt import АДРЕС, СЕКРЕТ
from web.auth import (
    LOGIN_WIDGET_HEADER, ROLE_TEACHER, CurrentUser, InitDataError,
    current_user, verify_login_widget_string,
)
from web.errors import install_error_handlers

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


def данные_виджета(telegram_id: int = 777, bot_token: str = BOT_TOKEN, **правки) -> str:
    """Собирает подпись ровно так, как это делает Telegram Login Widget."""
    поля = {
        "id": str(telegram_id),
        "first_name": "Айгүл",
        "username": "aigul",
        "auth_date": str(int(time.time())),
    }
    поля.update({к: str(з) for к, з in правки.items()})
    строка = "\n".join(f"{к}={поля[к]}" for к in sorted(поля))
    секрет = hashlib.sha256(bot_token.encode()).digest()
    поля["hash"] = hmac.new(секрет, строка.encode(), hashlib.sha256).hexdigest()
    return urlencode(поля)


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


@pytest.fixture
def приложение():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/v1/кто-я")
    async def кто_я(человек: CurrentUser = Depends(current_user)) -> dict:
        return {"user_id": человек.user_id, "telegram_user_id": человек.telegram_user_id,
                "role": человек.role}

    return TestClient(app)


# --- подпись ---

# Токен передаётся явно: без фикстуры «база» в settings лежит настоящий
# токен из .env, и подпись, собранная тестовым, с ним не сойдётся.
def test_настоящие_данные_принимаются():
    поля = verify_login_widget_string(данные_виджета(), bot_token=BOT_TOKEN)
    assert поля["id"] == "777"


def test_подделанные_данные_отвергаются():
    """
    Главный тест двери. Идентификатор подменён — «стал» другим
    человеком, — а подпись осталась от исходных данных. Уберите сверку
    подписи, и этот тест позеленеет молча.
    """
    исходные = данные_виджета(777)
    подпись = исходные.split("hash=")[1]
    подделка = данные_виджета(999).split("&hash=")[0] + "&hash=" + подпись

    with pytest.raises(InitDataError, match="подпись"):
        verify_login_widget_string(подделка, bot_token=BOT_TOKEN)


def test_чужой_токен_бота_не_подходит():
    with pytest.raises(InitDataError, match="подпись"):
        verify_login_widget_string(данные_виджета(bot_token="999:чужой-токен"), bot_token=BOT_TOKEN)


def test_схема_подписи_виджета_не_совпадает_со_схемой_initdata():
    """
    initData: secret = HMAC(key='WebAppData', msg=token).
    Виджет:   secret = SHA256(token).
    Перепутать легко, и код при этом не падает. Данные, подписанные
    «по-initData», обязаны быть отвергнуты.
    """
    поля = {"id": "777", "first_name": "Айгүл", "auth_date": str(int(time.time()))}
    строка = "\n".join(f"{к}={поля[к]}" for к in sorted(поля))
    не_тот_секрет = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    поля["hash"] = hmac.new(не_тот_секрет, строка.encode(), hashlib.sha256).hexdigest()

    with pytest.raises(InitDataError, match="подпись"):
        verify_login_widget_string(urlencode(поля), bot_token=BOT_TOKEN)


def test_просроченные_данные_отвергаются():
    старые = данные_виджета(auth_date=int(time.time()) - 90000)
    with pytest.raises(InitDataError, match="просрочен"):
        verify_login_widget_string(старые, bot_token=BOT_TOKEN)


def test_подпись_сравнивается_только_compare_digest():
    """
    Обычное == выходит на первом различии, и время ответа выдаёт, сколько
    байт подписи угадано. Функциональным тестом это не поймать.
    """
    исходник = (PROJECT_ROOT / "web" / "auth.py").read_text(encoding="utf-8")
    проверка = исходник[исходник.index("def verify_login_widget_string"):]
    проверка = проверка[: проверка.index("class AuthenticatedUser")]
    assert "hmac.compare_digest(ожидаемый, полученный)" in проверка
    assert "ожидаемый == полученный" not in проверка


# --- дверь целиком ---

def test_вход_через_виджет_даёт_роль(база, приложение):
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", 777), db_path=база)
    тело = приложение.get("/api/v1/кто-я", headers={LOGIN_WIDGET_HEADER: данные_виджета(777)}).json()
    assert тело == {"user_id": "tg:777", "telegram_user_id": 777, "role": ROLE_TEACHER}


def test_подделка_в_дверь_не_проходит(база, приложение):
    ответ = приложение.get("/api/v1/кто-я", headers={LOGIN_WIDGET_HEADER: данные_виджета(bot_token="999:чужой")})
    assert ответ.status_code == 401


def test_незнакомый_человек_опознан_но_без_роли(база, приложение):
    тело = приложение.get("/api/v1/кто-я", headers={LOGIN_WIDGET_HEADER: данные_виджета(4242)}).json()
    assert тело["telegram_user_id"] == 4242
    assert тело["role"] is None


def test_три_двери_дают_одинаковую_структуру(база, приложение):
    """Эндпоинту всё равно, откуда пришёл человек."""
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", 777), db_path=база)
    через_виджет = приложение.get("/api/v1/кто-я", headers={LOGIN_WIDGET_HEADER: данные_виджета(777)}).json()
    через_miniapp = приложение.get("/api/v1/кто-я", headers={"X-Telegram-Init-Data": init_data(777)}).json()
    assert через_виджет == через_miniapp
