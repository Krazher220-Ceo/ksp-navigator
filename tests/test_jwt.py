"""
tests/test_jwt.py — проверка JWT от Supabase Auth (блок Ф3).

Главный тест здесь — про подделанный токен. Он обязан падать, если из
web/jwt.py убрать сверку подписи: проверка, которая ничего не проверяет,
выглядит точно так же, как работающая, и заметить подмену можно только
тестом. Тот же капкан уже описан в шапке web/auth.py для initData.

Сети в тестах нет: токены собираются здесь же, тем же алгоритмом, каким
их выдаёт Supabase.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from web.jwt import ALGORITHM, DEFAULT_AUDIENCE, JwtError, issuer_for, verify_supabase_jwt

СЕКРЕТ = "секрет-проекта-supabase-только-для-тестов"
АДРЕС = "https://abcdefgh.supabase.co"
ИЗДАТЕЛЬ = issuer_for(АДРЕС)


def _b64(данные: bytes) -> str:
    return base64.urlsafe_b64encode(данные).decode().rstrip("=")


def собрать_токен(
    claims: dict | None = None,
    секрет: str = СЕКРЕТ,
    alg: str = ALGORITHM,
    подпись: str | None = None,
) -> str:
    """Токен по тому же алгоритму, каким его подписывает Supabase."""
    заголовок = _b64(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    полезная = {
        "sub": "9c1e7b30-0000-4000-8000-000000000001",
        "aud": DEFAULT_AUDIENCE,
        "iss": ИЗДАТЕЛЬ,
        "exp": int(time.time()) + 3600,
        "email": "uchitel@school.kz",
    }
    полезная.update(claims or {})
    тело = _b64(json.dumps(полезная, separators=(",", ":")).encode())
    if подпись is None:
        подпись = _b64(hmac.new(секрет.encode(), f"{заголовок}.{тело}".encode(), hashlib.sha256).digest())
    return f"{заголовок}.{тело}.{подпись}"


def проверить(токен: str, **kwargs) -> dict:
    kwargs.setdefault("secret", СЕКРЕТ)
    kwargs.setdefault("issuer", ИЗДАТЕЛЬ)
    return verify_supabase_jwt(токен, **kwargs)


def test_настоящий_токен_принимается():
    claims = проверить(собрать_токен())
    assert claims["sub"] == "9c1e7b30-0000-4000-8000-000000000001"
    assert claims["email"] == "uchitel@school.kz"


def test_подделанный_токен_отвергается():
    """
    Главный тест блока. Полезная нагрузка подменена — «стал» другим
    человеком, — а подпись осталась от исходного токена.

    Если из web/jwt.py убрать сверку подписи, этот тест позеленеет
    молча, потому что все остальные поля у подделки в порядке. Поэтому
    он и написан отдельно, и поэтому проверен временным откатом правки.
    """
    настоящий = собрать_токен()
    заголовок, _, подпись = настоящий.split(".")
    чужая_нагрузка = _b64(json.dumps({
        "sub": "00000000-0000-4000-8000-00000000dead",
        "aud": DEFAULT_AUDIENCE,
        "iss": ИЗДАТЕЛЬ,
        "exp": int(time.time()) + 3600,
    }, separators=(",", ":")).encode())

    with pytest.raises(JwtError, match="подпись"):
        проверить(f"{заголовок}.{чужая_нагрузка}.{подпись}")


def test_токен_подписанный_чужим_секретом_отвергается():
    with pytest.raises(JwtError, match="подпись"):
        проверить(собрать_токен(секрет="не-наш-секрет"))


def test_токен_без_подписи_отвергается():
    """alg: none — классическая подделка: подписи нет вообще."""
    with pytest.raises(JwtError, match="алгоритм"):
        проверить(собрать_токен(alg="none", подпись=""))


def test_подмена_алгоритма_на_rs256_отвергается():
    with pytest.raises(JwtError, match="алгоритм"):
        проверить(собрать_токен(alg="RS256"))


def test_перепутанный_порядок_аргументов_hmac_виден():
    """
    Ключ и сообщение в hmac.new легко поменять местами, и код при этом
    не падает. Токен, подписанный «наоборот», обязан быть отвергнут.
    """
    заголовок = _b64(json.dumps({"alg": ALGORITHM, "typ": "JWT"}, separators=(",", ":")).encode())
    тело = _b64(json.dumps({
        "sub": "9c1e7b30-0000-4000-8000-000000000001",
        "aud": DEFAULT_AUDIENCE, "iss": ИЗДАТЕЛЬ, "exp": int(time.time()) + 3600,
    }, separators=(",", ":")).encode())
    наоборот = _b64(hmac.new(f"{заголовок}.{тело}".encode(), СЕКРЕТ.encode(), hashlib.sha256).digest())

    with pytest.raises(JwtError, match="подпись"):
        проверить(f"{заголовок}.{тело}.{наоборот}")


def test_просроченный_токен_отвергается():
    with pytest.raises(JwtError, match="просрочен"):
        проверить(собрать_токен({"exp": int(time.time()) - 1}))


def test_токен_без_exp_отвергается():
    """Токен без срока жил бы вечно — такого в проекте быть не должно."""
    with pytest.raises(JwtError, match="exp"):
        проверить(собрать_токен({"exp": None}))


def test_чужая_аудитория_отвергается():
    with pytest.raises(JwtError, match="аудитория"):
        проверить(собрать_токен({"aud": "service_role"}))


def test_аудитория_списком_принимается():
    """Supabase иногда пишет aud списком — это тот же самый токен."""
    assert проверить(собрать_токен({"aud": ["authenticated", "iot"]}))["sub"]


def test_чужой_издатель_отвергается():
    """Токен настоящего Supabase, но другого проекта."""
    with pytest.raises(JwtError, match="издатель"):
        проверить(собрать_токен({"iss": issuer_for("https://zzzzzzzz.supabase.co")}))


def test_токен_без_sub_отвергается():
    with pytest.raises(JwtError, match="sub"):
        проверить(собрать_токен({"sub": ""}))


def test_ненастроенный_секрет_не_пропускает_никого():
    """Не задан SUPABASE_JWT_SECRET — отказ, а не «пропустим всех»."""
    with pytest.raises(JwtError, match="SUPABASE_JWT_SECRET"):
        verify_supabase_jwt(собрать_токен(), secret="", issuer=ИЗДАТЕЛЬ)


@pytest.mark.parametrize("мусор", ["", "не.токен", "aaa.bbb", "a.b.c.d", "...", "%%%.%%%.%%%"])
def test_мусор_вместо_токена_отвергается(мусор):
    with pytest.raises(JwtError):
        проверить(мусор)


def test_издатель_собирается_из_адреса_проекта():
    assert issuer_for("https://abcdefgh.supabase.co") == "https://abcdefgh.supabase.co/auth/v1"
    assert issuer_for("https://abcdefgh.supabase.co/") == "https://abcdefgh.supabase.co/auth/v1"


def test_подпись_сравнивается_только_compare_digest():
    """
    Статическая проверка: обычное == сравнивает байты с выходом на первом
    различии, и время ответа выдаёт, сколько байт подписи уже угадано.
    Функциональным тестом эту подмену не поймать — оба сравнения дают
    одинаковый ответ, — поэтому смотрим в исходник.
    """
    from pathlib import Path

    исходник = (Path(__file__).resolve().parent.parent / "web" / "jwt.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(" in исходник
    строки_сравнения = [
        строка for строка in исходник.splitlines()
        if "подпись_b64" in строка and ("==" in строка or "!=" in строка)
    ]
    assert строки_сравнения == [], f"подпись сравнивается не compare_digest: {строки_сравнения}"
