"""
tests/web/test_supabase_token.py — вторая дверь проверки токена (ES256).

Сторожит находку живого прода 02.09.2026: Supabase на этом проекте
подписывает токены асимметрично (JWKS отдаёт ES256), а web/jwt.py
принимает только HS256. Вход по почте отдавал 401 на каждый запрос и не
работал ни разу с самого запуска.

Сети здесь нет: httpx подменяется. Проверяется решение — какой дверью
идти и что считать отказом, — а не доступность Supabase.
"""

import time

import pytest

from core.config import settings
from web.supabase_token import (
    CACHE_TTL_SECONDS,
    SupabaseTokenError,
    алгоритм_токена,
    очистить_кэш,
    подтвердить_у_supabase,
)


def _токен(alg: str) -> str:
    import base64
    import json

    голова = base64.urlsafe_b64encode(json.dumps({"alg": alg, "typ": "JWT"}).encode()).rstrip(b"=")
    тело = base64.urlsafe_b64encode(json.dumps({"sub": "u1"}).encode()).rstrip(b"=")
    return f"{голова.decode()}.{тело.decode()}.подпись"


class ОтветЗаглушка:
    def __init__(self, код, данные=None):
        self.status_code = код
        self._данные = данные or {}

    def json(self):
        return self._данные


class КлиентЗаглушка:
    вызовов = 0

    def __init__(self, ответ):
        self._ответ = ответ

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, адрес, headers):
        type(self).вызовов += 1
        self.последний_адрес = адрес
        self.последние_заголовки = headers
        return self._ответ


# Settings — замороженный dataclass, поэтому подменяется через
# object.__setattr__ и возвращается на место. Тот же приём, что в
# tests/web/test_dashboard_api.py: заводить второй способ незачем.
@pytest.fixture(autouse=True)
def чистый_кэш():
    очистить_кэш()
    было = (settings.supabase_url, settings.supabase_service_role_key)
    object.__setattr__(settings, "supabase_url", "https://проект.supabase.co")
    object.__setattr__(settings, "supabase_service_role_key", "service-key")
    КлиентЗаглушка.вызовов = 0
    yield
    for имя, значение in zip(("supabase_url", "supabase_service_role_key"), было):
        object.__setattr__(settings, имя, значение)
    очистить_кэш()


# --- какой дверью идти ---

@pytest.mark.parametrize("alg", ["HS256", "ES256", "RS256", "none"])
def test_алгоритм_читается_но_не_решает_доступ(alg):
    """
    alg нужен ровно для выбора двери. Если бы он решал доступ, подмена
    заголовка стала бы способом выбрать себе проверку помягче.
    """
    assert алгоритм_токена(_токен(alg)) == alg


@pytest.mark.parametrize("мусор", ["", "не.токен", "одна-часть", "a.b"])
def test_на_мусор_алгоритм_не_придумывается(мусор):
    assert алгоритм_токена(мусор) is None


# --- подтверждение у Supabase ---

def test_supabase_признал_токен(monkeypatch):
    клиент = КлиентЗаглушка(ОтветЗаглушка(200, {"id": "user-42", "aud": "authenticated", "role": "authenticated"}))
    monkeypatch.setattr("web.supabase_token.httpx.Client", клиент)

    claims = подтвердить_у_supabase(_токен("ES256"))

    assert claims["sub"] == "user-42"
    assert клиент.последний_адрес == "https://проект.supabase.co/auth/v1/user"
    # Служебный ключ идёт как apikey, а токен человека — как Bearer.
    # Перепутать их значит спрашивать Supabase про самого себя.
    assert клиент.последние_заголовки["apikey"] == "service-key"
    assert клиент.последние_заголовки["Authorization"].endswith(_токен("ES256"))


def test_supabase_не_признал_токен(monkeypatch):
    monkeypatch.setattr("web.supabase_token.httpx.Client", КлиентЗаглушка(ОтветЗаглушка(401)))
    with pytest.raises(SupabaseTokenError):
        подтвердить_у_supabase(_токен("ES256"))


def test_сеть_легла_это_не_плохой_токен(monkeypatch):
    import httpx

    class Падающий(КлиентЗаглушка):
        def get(self, адрес, headers):
            raise httpx.ConnectError("сети нет")

    monkeypatch.setattr("web.supabase_token.httpx.Client", Падающий(None))
    with pytest.raises(SupabaseTokenError, match="недоступен"):
        подтвердить_у_supabase(_токен("ES256"))


# --- кэш ---

def test_повторная_проверка_не_ходит_в_сеть(monkeypatch):
    клиент = КлиентЗаглушка(ОтветЗаглушка(200, {"id": "user-42"}))
    monkeypatch.setattr("web.supabase_token.httpx.Client", клиент)

    токен = _токен("ES256")
    подтвердить_у_supabase(токен, now=1000.0)
    подтвердить_у_supabase(токен, now=1000.0 + CACHE_TTL_SECONDS - 1)

    assert КлиентЗаглушка.вызовов == 1, "кэш не сработал — вызов на каждый запрос"


def test_кэш_протухает(monkeypatch):
    клиент = КлиентЗаглушка(ОтветЗаглушка(200, {"id": "user-42"}))
    monkeypatch.setattr("web.supabase_token.httpx.Client", клиент)

    токен = _токен("ES256")
    подтвердить_у_supabase(токен, now=1000.0)
    подтвердить_у_supabase(токен, now=1000.0 + CACHE_TTL_SECONDS + 1)

    assert КлиентЗаглушка.вызовов == 2, "просроченный ответ переиспользован"


def test_отказ_не_кэшируется(monkeypatch):
    """Иначе случайный сбой сети запирал бы человека на минуту."""
    monkeypatch.setattr("web.supabase_token.httpx.Client", КлиентЗаглушка(ОтветЗаглушка(401)))
    токен = _токен("ES256")
    for _ in range(2):
        with pytest.raises(SupabaseTokenError):
            подтвердить_у_supabase(токен)
    assert КлиентЗаглушка.вызовов == 2


def test_без_настроек_supabase_не_пропускает():
    """Ненастроенный Supabase не должен превращаться в «пропустим всех»."""
    object.__setattr__(settings, "supabase_url", None)
    with pytest.raises(SupabaseTokenError):
        подтвердить_у_supabase(_токен("ES256"))


# --- какой дверью идёт сам current_user ---

async def test_current_user_спрашивает_supabase_про_es256(monkeypatch):
    """
    Токен, который локально проверить нечем, уходит на подтверждение.

    Без этой ветки вход по почте отдавал 401 на каждый запрос: подпись
    ES256, а web/jwt.py принимает только HS256.
    """
    import web.auth as auth

    monkeypatch.setattr(auth, "подтвердить_у_supabase", lambda t: {"sub": "user-99"})
    monkeypatch.setattr(auth.accounts, "resolve_role_by_auth_user", lambda sub: "teacher")

    кто = await auth.current_user(authorization=f"Bearer {_токен('ES256')}")

    assert кто.user_id == "auth:user-99"
    assert кто.role == "teacher"


async def test_поддельный_hs256_в_сеть_не_ходит(monkeypatch):
    """
    Токен с alg: HS256 отвергается локально и второй попытки не получает.

    Иначе подделка стоила бы нам сетевого вызова на каждую — то есть
    любой желающий мог бы гонять наш сервер в Supabase.
    """
    import web.auth as auth
    from fastapi import HTTPException

    ходили = []
    monkeypatch.setattr(auth, "подтвердить_у_supabase", lambda t: ходили.append(t) or {"sub": "x"})

    with pytest.raises(HTTPException) as отказ:
        await auth.current_user(authorization=f"Bearer {_токен('HS256')}")

    assert отказ.value.status_code == 401
    assert ходили == [], "поддельный HS256 всё-таки пошёл в сеть"
