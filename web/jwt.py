"""
web/jwt.py — проверка JWT от Supabase Auth (блок Ф3).

Зачем модуль: кабинет входит по почте и паролю через Supabase Auth и
приносит нам JWT в заголовке Authorization. Здесь решается, верить ему
или нет. Сервер публично доступен через Cloudflare Tunnel, так что
непроверенный токен — это доступ к чужим урокам и чужим детям.

Почему свой код, а не библиотека: проверка HS256 — это сорок строк
стандартной библиотеки, и каждую из них видно. Библиотека дала бы
зависимость, обновления и настройки по умолчанию, о которых пришлось бы
помнить (у ряда обёрток проверка aud и iss выключена, пока её не
включишь). Если библиотеку когда-нибудь возьмут — она обязана проверять
подпись, exp, aud и iss, и на неё пишется тот же тест с подделанным
токеном, что лежит в tests/web/test_jwt.py.

Что осознанно не делает: не ходит в сеть и не спрашивает Supabase, жив
ли пользователь. Подпись и срок — это всё, что можно проверить локально,
и этого достаточно: токен живёт час. Не поддерживает RS256 — Supabase
на проекте настроен на общий секрет, а «поддержим и то и другое» здесь
означало бы вторую ветку кода, которую некому проверять.

Три места, где легко ошибиться и не заметить:
  1. Порядок аргументов hmac.new: ключ первым, сообщение вторым.
     Перепутать легко, и код при этом не падает — просто ни одна подпись
     никогда не совпадёт... или, что хуже, совпадёт не то. Тот же капкан
     описан в шапке web/auth.py для initData.
  2. Сравнение подписи — только hmac.compare_digest. Обычное ==
     сравнивает строки посимвольно и с выходом на первом различии, то
     есть время ответа зависит от того, сколько байт подписи угадано.
  3. alg из заголовка токена нельзя принимать на веру. Токен с
     alg: none подписи не имеет вообще, и наивная проверка «посчитай по
     alg из заголовка» пропускает такой токен как валидный. Здесь alg
     обязан быть ровно HS256, всё остальное — отказ.
"""

import base64
import hashlib
import hmac
import json
import time

ALGORITHM = "HS256"

# Supabase выдаёт токены вошедшим людям с этой аудиторией. Сервисные
# ключи имеют другую и до сюда доходить не должны.
DEFAULT_AUDIENCE = "authenticated"


class JwtError(Exception):
    """Токен подделан, просрочен или не тот. Наружу уходит общий 401."""


def _b64url_decode(part: str) -> bytes:
    """base64url без выравнивания — так его пишут в JWT."""
    padding = "=" * (-len(part) % 4)
    try:
        return base64.urlsafe_b64decode(part + padding)
    except (ValueError, TypeError) as exc:
        raise JwtError("часть токена не разбирается как base64url") from exc


def _json_part(part: str, name: str) -> dict:
    try:
        значение = json.loads(_b64url_decode(part))
    except json.JSONDecodeError as exc:
        raise JwtError(f"{name} токена не разбирается как JSON") from exc
    if not isinstance(значение, dict):
        raise JwtError(f"{name} токена не объект")
    return значение


def issuer_for(supabase_url: str) -> str:
    """Издатель токенов проекта Supabase — всегда <адрес>/auth/v1."""
    return supabase_url.rstrip("/") + "/auth/v1"


def verify_supabase_jwt(
    token: str,
    secret: str,
    issuer: str,
    audience: str = DEFAULT_AUDIENCE,
    now: float | None = None,
) -> dict:
    """Проверяет токен и возвращает его claims.

    Проверяется всё четыре: подпись, срок, аудитория и издатель. Любая
    непройденная проверка — JwtError с внятной причиной; наружу, в
    зависимость FastAPI, уходит только общий 401 (причина отказа —
    подсказка тому, кто подбирает токен).
    """
    if not secret:
        # Не настроенный секрет не должен превращаться в «пропустим всех»:
        # это ровно та тихая дыра, от которой предостерегает план.
        raise JwtError("SUPABASE_JWT_SECRET не задан — проверить подпись нечем")
    if not token:
        raise JwtError("токен пустой")

    части = token.split(".")
    if len(части) != 3:
        raise JwtError("в токене не три части")
    заголовок_b64, полезная_b64, подпись_b64 = части

    заголовок = _json_part(заголовок_b64, "заголовок")
    if заголовок.get("alg") != ALGORITHM:
        # Сюда попадает и alg: none, и подмена на RS256 с чужим ключом.
        raise JwtError(f"алгоритм {заголовок.get('alg')!r} не поддерживается")

    ожидаемая = hmac.new(
        secret.encode("utf-8"),
        f"{заголовок_b64}.{полезная_b64}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(ожидаемая, _b64url_decode(подпись_b64)):
        raise JwtError("подпись токена не совпадает")

    claims = _json_part(полезная_b64, "полезная нагрузка")

    срок = claims.get("exp")
    if not isinstance(срок, (int, float)):
        raise JwtError("в токене нет exp")
    if (now if now is not None else time.time()) >= срок:
        raise JwtError("токен просрочен")

    аудитория = claims.get("aud")
    подходит = audience in аудитория if isinstance(аудитория, list) else аудитория == audience
    if not подходит:
        raise JwtError(f"аудитория токена {аудитория!r} не {audience!r}")

    if claims.get("iss") != issuer:
        raise JwtError(f"издатель токена {claims.get('iss')!r} не {issuer!r}")

    if not claims.get("sub"):
        raise JwtError("в токене нет sub — некого опознавать")

    return claims
