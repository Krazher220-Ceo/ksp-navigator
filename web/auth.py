"""
web/auth.py — валидация Telegram Mini App initData (блок Б9).

Зачем модуль: Cloudflare Tunnel (блок Б11) даёт публичный HTTPS-адрес —
без проверки подписи initData любой человек в интернете, зная только
адрес, смог бы дёргать /api/preview и /api/download чужими данными.
Это единственное место, где решается, кому верить.

Что осознанно не делает: не проверяет права доступа к конкретному
ресурсу (тот ли это учитель, чей generated_ksp) — только то, что
initData подписан настоящим Telegram для настоящего пользователя.
Проверка владения — в web/api.py, на уровне каждого эндпоинта.

На что опирается: только стандартная библиотека (hmac, hashlib,
urllib.parse) — валидация подписи не требует внешних зависимостей.

Алгоритм — ровно тот, что описан в официальной документации Telegram
Web Apps:
  1. initData разбирается как query-строка, hash вынимается отдельно.
  2. Из ОСТАЛЬНЫХ пар строится data_check_string: "ключ=значение",
     отсортированные по ключу, через "\\n".
  3. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token).
     Внимание: порядок обратный интуиции — "WebAppData" здесь КЛЮЧ,
     а токен бота — сообщение. Перепутать легко: код при этом всё
     равно "работает" (ничего не падает), просто ни один hash никогда
     не совпадёт по-настоящему, и никто не заметит, что проверка
     ничего не проверяет.
  4. Сравнивается HMAC_SHA256(key=secret_key, msg=data_check_string) с
     hash — ТОЛЬКО через hmac.compare_digest (обычное == даёт canal
     атаки по времени сравнения строк).
  5. auth_date не старше часа — initData не предназначен жить вечно.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from core.config import settings

INIT_DATA_HEADER = "X-Telegram-Init-Data"

MAX_AUTH_AGE_SECONDS = 3600
_MAX_CLOCK_SKEW_SECONDS = 60  # небольшой допуск на рассинхронизацию часов


class InitDataError(Exception):
    """initData невалиден, подделан или просрочен."""


def _parse_init_data(init_data: str) -> dict[str, str]:
    """initData приходит percent-encoded (это query-строка) —
    parse_qsl декодирует её в пары ключ/значение."""
    return dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))


def _build_data_check_string(fields: dict[str, str]) -> str:
    items = sorted((key, value) for key, value in fields.items() if key != "hash")
    return "\n".join(f"{key}={value}" for key, value in items)


def _compute_hash(data_check_string: str, bot_token: str) -> str:
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_init_data_string(
    init_data: str,
    bot_token: str | None = None,
    max_age_seconds: int = MAX_AUTH_AGE_SECONDS,
) -> dict[str, str]:
    """Проверяет initData по алгоритму Telegram. Возвращает разобранные
    поля при успехе; при провале — InitDataError с понятной причиной
    (наружу, в FastAPI-зависимость, уходит только общий 401 — конкретную
    причину светить в HTTP-ответе незачем, это не подсказка для взлома,
    но и не то, что должен видеть клиент)."""
    bot_token = bot_token if bot_token is not None else settings.telegram_bot_token

    if not init_data:
        raise InitDataError("initData пустой")

    fields = _parse_init_data(init_data)

    received_hash = fields.get("hash")
    if not received_hash:
        raise InitDataError("в initData отсутствует hash")

    data_check_string = _build_data_check_string(fields)
    expected_hash = _compute_hash(data_check_string, bot_token)

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("подпись initData не совпадает")

    auth_date_raw = fields.get("auth_date")
    if not auth_date_raw:
        raise InitDataError("в initData отсутствует auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError:
        raise InitDataError("auth_date не является числом") from None

    age = time.time() - auth_date
    if age > max_age_seconds:
        raise InitDataError(f"initData просрочен: {age:.0f} с назад (лимит {max_age_seconds} с)")
    if age < -_MAX_CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date в будущем — подозрительно")

    return fields


class AuthenticatedUser:
    """Результат успешной проверки: telegram_user_id и разобранные
    поля initData, для эндпоинтов web/api.py."""

    def __init__(self, telegram_user_id: int, fields: dict[str, str]) -> None:
        self.telegram_user_id = telegram_user_id
        self.fields = fields


async def verify_init_data(
    init_data: str | None = Header(default=None, alias=INIT_DATA_HEADER),
) -> AuthenticatedUser:
    """FastAPI-зависимость: Depends(verify_init_data) на каждом
    эндпоинте web/api.py, без исключений (Б9.2). initData приходит в
    заголовке X-Telegram-Init-Data (см. также PROMPTS.md, задача Б10.3
    для Mini App — тот же заголовок на стороне клиента).

    Отсутствующий заголовок, неверная подпись, просроченный auth_date
    или отсутствие user.id в initData — везде один и тот же 401: клиенту
    не нужно знать деталь, чтобы отличить "не прислал" от "прислал
    подделку", разница ничего не даёт атакующему, кроме подсказки."""
    if not init_data:
        raise HTTPException(status_code=401, detail="не авторизован")

    try:
        fields = verify_init_data_string(init_data)
    except InitDataError:
        raise HTTPException(status_code=401, detail="не авторизован") from None

    user_raw = fields.get("user")
    telegram_user_id = None
    if user_raw:
        try:
            telegram_user_id = json.loads(user_raw).get("id")
        except (json.JSONDecodeError, AttributeError):
            telegram_user_id = None

    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail="не авторизован")

    return AuthenticatedUser(telegram_user_id=telegram_user_id, fields=fields)
