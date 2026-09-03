"""
web/supabase_token.py — проверка токенов, подписанных ключом Supabase.

Зачем модуль: web/jwt.py проверяет подпись HS256 сам, общим секретом, и
делает это правильно. Но Supabase на новых проектах подписывает токены
асимметрично — ES256, ключ проверки лежит в JWKS, общего секрета больше
нет. Такой токен HS256-проверка отвергает по алгоритму, и вход по почте
на живом продукте отдавал 401 на каждый запрос: «Не удалось подтвердить,
что это вы». Проект в Supabase заведён 29.08.2026, уже с новыми ключами,
поэтому вход по почте не работал ни разу.

Как проверяем: спрашиваем сам Supabase — GET /auth/v1/user с этим
токеном. Отвечает 200 и id — токен настоящий и не просрочен; отвечает
401 — нет. Это авторитетный ответ: подделать его нельзя, не подделав
ответ самого Supabase.

Почему не разбираем ES256 сами: для этого нужна библиотека с
криптографией (в requirements.txt её нет и заводить ради одной подписи
незачем) либо своя реализация ECDSA — то есть самодельная криптография
в дверях авторизации. Лишний сетевой вызов дешевле: сервер и так ходит
в Supabase по HTTP за каждой строкой базы (core/db.py), это не новый
класс отказа.

Что осознанно не делает: не подменяет HS256-проверку. Токен с
alg: HS256 разбирается локально, и до сюда не доходит — иначе подделка
получала бы вторую попытку и стоила бы нам сетевого вызова на каждую.

На что опирается: httpx (уже в зависимостях) и SUPABASE_SERVICE_ROLE_KEY
как apikey. Отдельного ключа не заводится.
"""

import base64
import json
import time

import httpx

from core.config import settings

# Ответ кэшируется на минуту: токен живёт час, и спрашивать Supabase на
# каждый чих незачем. Минута — верхняя граница того, насколько поздно мы
# узнаем о выходе человека из аккаунта; для кабинета это приемлемо,
# а лишних вызовов не остаётся.
CACHE_TTL_SECONDS = 60

# Только успешные ответы. Отказы не кэшируются намеренно: иначе
# случайный сетевой сбой запирал бы человека на минуту.
_кэш: dict[str, tuple[float, dict]] = {}


class SupabaseTokenError(Exception):
    """Токен не подтверждён. Наружу уходит общий 401."""


def алгоритм_токена(token: str) -> str | None:
    """Читает alg из заголовка, НЕ доверяя ему.

    Значение используется ровно для одного решения: проверять локально
    или спрашивать Supabase. Ни на что, что решает доступ, оно не влияет
    — иначе подмена alg стала бы способом выбрать себе проверку помягче.
    """
    части = token.split(".")
    if len(части) != 3:
        return None
    кусок = части[0]
    try:
        заголовок = json.loads(base64.urlsafe_b64decode(кусок + "=" * (-len(кусок) % 4)))
    except (ValueError, TypeError):
        return None
    алгоритм = заголовок.get("alg")
    return алгоритм if isinstance(алгоритм, str) else None


def _из_кэша(token: str, now: float) -> dict | None:
    запись = _кэш.get(token)
    if not запись:
        return None
    до, данные = запись
    if до <= now:
        del _кэш[token]
        return None
    return данные


def очистить_кэш() -> None:
    """Для тестов: между проверками кэш не должен переезжать."""
    _кэш.clear()


def подтвердить_у_supabase(token: str, now: float | None = None) -> dict:
    """Возвращает claims вида {"sub": ...}, если Supabase признал токен.

    Формат ответа приведён к тому же виду, что у web/jwt.py: вызывающий
    код не должен знать, какой дверью проверялся токен.
    """
    now = time.time() if now is None else now
    if not token:
        raise SupabaseTokenError("токен пустой")

    готовое = _из_кэша(token, now)
    if готовое is not None:
        return готовое

    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SupabaseTokenError("SUPABASE_URL или SUPABASE_SERVICE_ROLE_KEY не заданы")

    адрес = settings.supabase_url.rstrip("/") + "/auth/v1/user"
    try:
        with httpx.Client(timeout=10) as клиент:
            ответ = клиент.get(
                адрес,
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.HTTPError as ошибка:
        # Сеть легла — это НЕ «токен плохой». Разные причины должны
        # приводить к разным ответам, иначе сбой сети выглядит как
        # взлом, а человека выкидывает на форму входа без объяснений.
        raise SupabaseTokenError(f"Supabase недоступен: {ошибка}") from ошибка

    if ответ.status_code != 200:
        raise SupabaseTokenError(f"Supabase не признал токен: {ответ.status_code}")

    try:
        данные = ответ.json()
        sub = данные["id"]
    except (ValueError, KeyError, TypeError) as ошибка:
        raise SupabaseTokenError("Supabase ответил не тем, что ожидалось") from ошибка

    claims = {"sub": sub, "aud": данные.get("aud"), "role": данные.get("role")}
    _кэш[token] = (now + CACHE_TTL_SECONDS, claims)
    return claims
