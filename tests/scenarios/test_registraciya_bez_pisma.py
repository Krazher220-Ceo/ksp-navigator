"""
tests/scenarios/test_registraciya_bez_pisma.py — регистрация без письма (02.09.2026).

Сторожит решение автора: аккаунт заводит сервер служебным ключом и сразу
помечает почту подтверждённой. Причина — на живом продукте письмо
ломалось трижды: встроенная почта Supabase ограничена несколькими
письмами в час, ссылка одноразовая, второй клик отвечает «Email link is
invalid or has expired».

Сети здесь нет: httpx подменяется. Проверяется поведение сервера, а не
доступность Supabase.
"""

import pytest

from core.config import settings
from web.supabase_users import (
    MIN_PASSWORD_LENGTH,
    UserCreateError,
    создать_подтверждённого,
)


class Ответ:
    def __init__(self, код, данные=None, текст=""):
        self.status_code = код
        self._данные = данные or {}
        self.text = текст

    def json(self):
        return self._данные


class Клиент:
    последний = None

    def __init__(self, ответ):
        self._ответ = ответ

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, адрес, headers, json):
        type(self).последний = {"адрес": адрес, "headers": headers, "json": json}
        return self._ответ


@pytest.fixture(autouse=True)
def настройки():
    было = (settings.supabase_url, settings.supabase_service_role_key)
    object.__setattr__(settings, "supabase_url", "https://проект.supabase.co")
    object.__setattr__(settings, "supabase_service_role_key", "service-key")
    Клиент.последний = None
    yield
    for имя, значение in zip(("supabase_url", "supabase_service_role_key"), было):
        object.__setattr__(settings, имя, значение)


def test_почта_помечается_подтверждённой(monkeypatch):
    """
    Без email_confirm аккаунт создаётся, но войти нельзя — Supabase
    отвечает «Email not confirmed». Ровно это и было в проде.
    """
    monkeypatch.setattr("web.supabase_users.httpx.Client", Клиент(Ответ(200, {"id": "u1"})))

    assert создать_подтверждённого("A.Tleubaeva@school12.kz", "parol-12345") == "u1"
    отправлено = Клиент.последний
    assert отправлено["json"]["email_confirm"] is True, "почта не помечена подтверждённой"
    # Адрес приводится к нижнему регистру: иначе один человек заведёт два
    # аккаунта, отличающихся только заглавной буквой.
    assert отправлено["json"]["email"] == "a.tleubaeva@school12.kz"
    assert отправлено["адрес"].endswith("/auth/v1/admin/users")


def test_служебный_ключ_идёт_только_в_заголовке(monkeypatch):
    monkeypatch.setattr("web.supabase_users.httpx.Client", Клиент(Ответ(200, {"id": "u1"})))
    создать_подтверждённого("a@b.kz", "parol-12345")
    заголовки = Клиент.последний["headers"]
    assert заголовки["apikey"] == "service-key"
    assert "service-key" not in str(Клиент.последний["json"]), "ключ уехал в тело запроса"


def test_занятая_почта_это_не_ошибка(monkeypatch):
    """
    Ответ одинаков для занятой и свободной почты: иначе форма
    регистрации превращается в способ проверять, кто зарегистрирован.
    """
    monkeypatch.setattr(
        "web.supabase_users.httpx.Client",
        Клиент(Ответ(422, текст='{"error_code":"email_exists"}')),
    )
    assert создать_подтверждённого("a@b.kz", "parol-12345") is None


@pytest.mark.parametrize("почта", ["", "без-собаки", "a@b", "a@ b.kz", "@b.kz"])
def test_мусор_вместо_почты_отсекается_до_supabase(почта, monkeypatch):
    monkeypatch.setattr("web.supabase_users.httpx.Client", Клиент(Ответ(200, {"id": "u1"})))
    with pytest.raises(UserCreateError):
        создать_подтверждённого(почта, "parol-12345")
    assert Клиент.последний is None, "мусорный адрес всё-таки ушёл в Supabase"


def test_короткий_пароль_отсекается_до_supabase(monkeypatch):
    monkeypatch.setattr("web.supabase_users.httpx.Client", Клиент(Ответ(200, {"id": "u1"})))
    with pytest.raises(UserCreateError, match=str(MIN_PASSWORD_LENGTH)):
        создать_подтверждённого("a@b.kz", "korotko")
    assert Клиент.последний is None


def test_сеть_легла_объясняется_по_русски(monkeypatch):
    import httpx

    class Падает(Клиент):
        def post(self, адрес, headers, json):
            raise httpx.ConnectError("сети нет")

    monkeypatch.setattr("web.supabase_users.httpx.Client", Падает(None))
    with pytest.raises(UserCreateError, match="недоступен"):
        создать_подтверждённого("a@b.kz", "parol-12345")


def test_без_настроек_не_притворяется_что_завёл(monkeypatch):
    object.__setattr__(settings, "supabase_service_role_key", None)
    with pytest.raises(UserCreateError):
        создать_подтверждённого("a@b.kz", "parol-12345")


# --- эндпоинт ---

def test_регистрация_единственная_открытая_дверь_кроме_health():
    """
    Список открытых эндпоинтов короткий намеренно: сервер публично
    доступен, и каждая строка в нём — сознательно открытая дверь.
    """
    from web import api_v1

    assert api_v1.PUBLIC_PATHS == frozenset({"/api/v1/health", "/api/v1/auth/register"})


def test_пароль_нигде_не_логируется():
    """
    Пароль приходит на сервер один раз, чтобы аккаунт вообще появился.
    Ни в лог, ни в базу он попасть не должен: логи живут дольше сессий и
    читаются глазами.
    """
    from pathlib import Path as _P

    модуль = (_P(__file__).resolve().parents[2] / "web" / "supabase_users.py").read_text(encoding="utf-8")
    assert "logger" not in модуль, "в модуле с паролем завёлся логгер"
    assert "print(" not in модуль
    assert "execute(" not in модуль and "INSERT" not in модуль, "пароль пишется в базу"


def test_эндпоинт_не_возвращает_токен():
    """
    Сервер не входит за человека: паролем в Supabase стучится браузер.
    Иначе сервер становится посредником, через которого идут сессии.
    """
    from pathlib import Path as _P

    api = (_P(__file__).resolve().parents[2] / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/auth/register")'):]
    кусок = кусок[: кусок.index("@router.post(\"/consent\")")]
    assert "access_token" not in кусок and "session" not in кусок
    assert 'return {"created": создан is not None}' in кусок
