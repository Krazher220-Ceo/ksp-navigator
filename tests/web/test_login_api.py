"""
tests/web/test_login_api.py — вход в кабинет через бота, со стороны API.

Что здесь сторожится. Два новых эндпоинта открыты без авторизации — они
её и выдают, — поэтому каждая проверка ниже про одно: что открытым
оказался ровно тот минимум, который нельзя было закрыть. Талон без
подтверждения в боте не пускает никуда, подтверждённый отдаётся один раз,
а подписанные данные проходят ту же проверку, что и Login Widget, — то
есть второй двери в кабинет не появилось.

Главное, ради чего всё затевалось: номера телефона в этом пути нет
нигде. Login Widget уводил на oauth.telegram.org, а тот на телефоне
почти всегда просил номер, и вход не доходил до конца.
"""

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from core.db import init_db
from core.login_codes import DEEP_LINK_PREFIX, выдать_код, подтвердить
from pathlib import Path
from web.api import app
from web.auth import verify_login_widget_string

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

BOT_TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"


@pytest.fixture
def isolated_api(tmp_path):
    """Своя база и предсказуемые имя бота с токеном.

    Имя бота — часть ответа start (из него собирается ссылка), токен —
    то, чем подписан ответ poll. Оба берутся из настроек, поэтому
    подменяются здесь, а не угадываются в тестах.
    """
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    было = (
        settings.db_path,
        settings.db_backend,
        settings.telegram_bot_name,
        settings.telegram_bot_token,
    )
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_name", "ksp_navigator_bot")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    try:
        yield db_path
    finally:
        # Только object.__setattr__: settings — frozen-датакласс, обычное
        # присваивание здесь бросает FrozenInstanceError уже в teardown,
        # то есть тест «проходит», а прогон при этом красный.
        object.__setattr__(settings, "db_path", было[0])
        object.__setattr__(settings, "db_backend", было[1])
        object.__setattr__(settings, "telegram_bot_name", было[2])
        object.__setattr__(settings, "telegram_bot_token", было[3])


@pytest.fixture
def client():
    return TestClient(app)


def test_start_отдаёт_ссылку_на_бота_и_контрольные_знаки(isolated_api, client):
    ответ = client.post("/api/v1/auth/telegram/start")
    assert ответ.status_code == 200
    тело = ответ.json()

    assert тело["deep_link"] == f"https://t.me/ksp_navigator_bot?start={DEEP_LINK_PREFIX}{тело['code']}"
    assert тело["check_digits"] == тело["code"][-4:]
    assert тело["expires_in"] == 300


def test_start_не_работает_без_имени_бота(isolated_api, client):
    """Не задан TELEGRAM_BOT_NAME — честный отказ, а не ссылка в никуда.

    Собрать «https://t.me/None?start=…» было бы хуже отказа: человек
    ушёл бы по ней и не понял, почему ничего не открылось.
    """
    object.__setattr__(settings, "telegram_bot_name", None)
    ответ = client.post("/api/v1/auth/telegram/start")
    assert ответ.status_code == 503


def test_неподтверждённый_талон_никуда_не_пускает(isolated_api, client):
    """Открытие ссылки само по себе входом не является."""
    код = client.post("/api/v1/auth/telegram/start").json()["code"]
    ответ = client.get(f"/api/v1/auth/telegram/poll?code={код}")
    assert ответ.status_code == 200
    assert ответ.json() == {"status": "pending"}


def test_подтверждённый_талон_даёт_данные_входа_и_они_проходят_проверку(isolated_api, client):
    """Формат и подпись — те же, что у Login Widget.

    Это главный тест файла: если бы здесь появился свой формат, в
    проекте стало бы две независимые проверки входа, и однажды одна из
    них отстала бы от другой.
    """
    код = client.post("/api/v1/auth/telegram/start").json()["code"]
    подтвердить(код, 4242, first_name="Алихан", username="alikhan")

    тело = client.get(f"/api/v1/auth/telegram/poll?code={код}").json()
    assert тело["status"] == "confirmed"

    from urllib.parse import urlencode

    поля = verify_login_widget_string(urlencode(тело["login"]), bot_token=BOT_TOKEN)
    assert поля["id"] == "4242"
    assert поля["first_name"] == "Алихан"


def test_подделанная_подпись_данных_входа_не_проходит(isolated_api, client):
    """Страховка от «проверка ничего не проверяет»: тест выше был бы
    зелёным и при сломанной проверке подписи."""
    from urllib.parse import urlencode

    from web.auth import InitDataError

    код = client.post("/api/v1/auth/telegram/start").json()["code"]
    подтвердить(код, 4242, first_name="Алихан")
    login = client.get(f"/api/v1/auth/telegram/poll?code={код}").json()["login"]

    login["id"] = "9999"  # подменили, кем входим — подпись обязана перестать сходиться
    with pytest.raises(InitDataError):
        verify_login_widget_string(urlencode(login), bot_token=BOT_TOKEN)


def test_данные_входа_отдаются_один_раз(isolated_api, client):
    """Второй раз тот же талон — уже не вход, а 404."""
    код = client.post("/api/v1/auth/telegram/start").json()["code"]
    подтвердить(код, 4242)

    assert client.get(f"/api/v1/auth/telegram/poll?code={код}").json()["status"] == "confirmed"
    assert client.get(f"/api/v1/auth/telegram/poll?code={код}").status_code == 404


def test_несуществующий_талон_отвечает_как_протухший(isolated_api, client):
    """Одинаковый ответ намеренно: разные ответы рассказали бы тому, кто
    перебирает коды, какой из них существовал."""
    assert client.get("/api/v1/auth/telegram/poll?code=нетакого").status_code == 404


def test_оба_эндпоинта_работают_без_авторизации(isolated_api, client):
    """Иначе получилось бы «авторизуйтесь, чтобы авторизоваться»."""
    from web import api_v1

    assert "/api/v1/auth/telegram/start" in api_v1.PUBLIC_PATHS
    assert "/api/v1/auth/telegram/poll" in api_v1.PUBLIC_PATHS
    # И это подтверждается настоящим запросом без единого заголовка входа.
    assert client.post("/api/v1/auth/telegram/start").status_code == 200
