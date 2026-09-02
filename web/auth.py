"""
web/auth.py — валидация Telegram Mini App initData (блок Б9).

Зачем модуль: Cloudflare Tunnel (блок Б11) даёт публичный HTTPS-адрес —
без проверки подписи initData любой человек в интернете, зная только
адрес, смог бы дёргать /api/preview и /api/download чужими данными.
Это единственное место, где решается, кому верить.

Блок Ф3 (FRONTEND_PLAN.md) добавил сюда вторую дверь — JWT от Supabase
Auth (web/jwt.py) — и одну зависимость current_user, которая принимает
любую из двух и возвращает одну и ту же структуру. Проверка initData
ниже не тронута ни на строку: она работает в проде.

Две двери не должны стать дырой. Если не сработала ни одна — 401, а не
«пропустим на всякий случай». База не ответила при определении роли —
честный 503, а не тихий проход: ровно так уже сделано для согласия
(CONSENT_CHECK_UNAVAILABLE в bot/texts.py).

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
import logging
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from bot import texts
from core import accounts
from core.config import settings
from core.db import SupabaseDatabaseError, query
from web.errors import (
    CODE_CONSENT_REQUIRED,
    CODE_NOT_AUTHORIZED,
    CODE_SERVER_UNAVAILABLE,
    ApiError,
)
from web.jwt import JwtError, issuer_for, verify_supabase_jwt

logger = logging.getLogger(__name__)

INIT_DATA_HEADER = "X-Telegram-Init-Data"
# Ф16: вход в кабинет через Telegram Login Widget — третья дверь. Это НЕ
# initData: у виджета другая схема подписи (secret = SHA256(токен), а не
# HMAC с ключом "WebAppData"), и путать их нельзя.
LOGIN_WIDGET_HEADER = "X-Telegram-Login"

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


# Данные Telegram Login Widget живут сутки: дольше держать подпись,
# по которой пускают в кабинет, незачем.
MAX_LOGIN_AGE_SECONDS = 86400


def verify_login_widget_string(
    login_data: str,
    bot_token: str | None = None,
    max_age_seconds: int = MAX_LOGIN_AGE_SECONDS,
) -> dict[str, str]:
    """
    Проверяет данные Telegram Login Widget.

    Схема похожа на initData, но НЕ совпадает с ней, и это главная
    ловушка места:
      initData: secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
      виджет:   secret = SHA256(bot_token)
    Перепутать легко, код при этом не падает — просто ни одна подпись не
    сходится, и вход «молча не работает». Поэтому проверки разные, и на
    каждую написан свой тест с подделанной подписью.

    Сравнение — только hmac.compare_digest: обычное == выходит на первом
    различии и выдаёт временем, сколько байт подписи уже угадано.
    """
    bot_token = bot_token if bot_token is not None else settings.telegram_bot_token

    if not login_data:
        raise InitDataError("данные входа пустые")
    поля = _parse_init_data(login_data)

    полученный = поля.get("hash")
    if not полученный:
        raise InitDataError("в данных входа отсутствует hash")

    строка = _build_data_check_string(поля)
    секрет = hashlib.sha256(bot_token.encode("utf-8")).digest()
    ожидаемый = hmac.new(секрет, строка.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(ожидаемый, полученный):
        raise InitDataError("подпись данных входа не совпадает")

    сырая_дата = поля.get("auth_date")
    if not сырая_дата:
        raise InitDataError("в данных входа отсутствует auth_date")
    try:
        дата = int(сырая_дата)
    except ValueError:
        raise InitDataError("auth_date не является числом") from None

    возраст = time.time() - дата
    if возраст > max_age_seconds:
        raise InitDataError(f"данные входа просрочены: {возраст:.0f} с назад")
    if возраст < -_MAX_CLOCK_SKEW_SECONDS:
        raise InitDataError("auth_date в будущем — подозрительно")

    if not поля.get("id"):
        raise InitDataError("в данных входа нет id — некого опознавать")
    return поля


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


# =====================================================================
# Ф3: одна дверь на два входа
#
# Кабинет входит по почте через Supabase Auth и приносит JWT, Telegram
# приносит initData. Проверка у каждого своя, а результат — один и тот
# же CurrentUser, чтобы эндпоинту было всё равно, откуда пришёл человек.
# =====================================================================

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"

# Третьей роли нет и не заводится (FRONTEND_PLAN.md, блок Ф3).
ROLES = (ROLE_TEACHER, ROLE_STUDENT)

BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class CurrentUser:
    """Кто пришёл, одинаково для обеих дверей.

    user_id — стабильная строка с пометкой двери: "tg:508…" для
    Telegram, "auth:9c1e…" для входа по почте. Пометка нужна, чтобы два
    разных пространства идентификаторов нельзя было спутать: числовой
    telegram_user_id и uuid из Supabase Auth совпасть не могут, но
    сравнивать их без пометки всё равно опасно.

    role — "teacher", "student" или None. None значит «роль ещё не
    определена», а не «никто»: так выглядит человек, который вошёл, но
    профиля пока не завёл. Ему показывают регистрацию, а не отказ.
    """

    user_id: str
    telegram_user_id: int | None
    role: str | None

    @property
    def auth_user_id(self) -> str | None:
        """Идентификатор в Supabase Auth, если человек вошёл по почте."""
        return self.user_id[len("auth:") :] if self.user_id.startswith("auth:") else None


def resolve_role(telegram_user_id: int, db_path=None) -> str | None:
    """Роль по наличию строки в students или teachers.

    Ученик проверяется первым — так же, как в боте (_student_gate,
    bot/handlers.py): если человек почему-то оказался и там и там,
    считаем его учеником, потому что цена ошибки в эту сторону меньше.
    Ученик увидит меньше, чем мог бы; педагог, ошибочно принятый за
    ученика, — заведёт класс на чужого ребёнка.
    """
    if query("SELECT 1 FROM students WHERE telegram_id = ?", (telegram_user_id,), db_path=db_path):
        return ROLE_STUDENT
    if query("SELECT 1 FROM teachers WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path):
        return ROLE_TEACHER
    return None


async def require_consent(человек: "CurrentUser") -> None:
    """
    Проверка согласия ПЕРЕД действием, а не после.

    Вызывается первой строкой каждого эндпоинта, который что-то делает
    (Ф4: регистрация педагога и вступление в класс). База не ответила —
    честный отказ, а не тихий проход: ровно так уже сделано в боте
    (CONSENT_CHECK_UNAVAILABLE).
    """
    try:
        дано = accounts.has_given_consent(
            telegram_user_id=человек.telegram_user_id, auth_user_id=человек.auth_user_id
        )
    except SupabaseDatabaseError:
        logger.warning("require_consent: база недоступна, user_id=%s", человек.user_id, exc_info=True)
        raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None
    if not дано:
        raise ApiError(403, CODE_CONSENT_REQUIRED, texts.API_CONSENT_REQUIRED)


def _не_авторизован() -> ApiError:
    return ApiError(401, CODE_NOT_AUTHORIZED, texts.API_NOT_AUTHORIZED)


async def current_user(
    authorization: str | None = Header(default=None),
    init_data: str | None = Header(default=None, alias=INIT_DATA_HEADER),
    login_data: str | None = Header(default=None, alias=LOGIN_WIDGET_HEADER),
) -> CurrentUser:
    """
    FastAPI-зависимость для /api/v1/*: принимает ЛИБО JWT, ЛИБО initData.

    Порядок разбора: сначала Authorization — он приходит от кабинета и
    задан явно; initData Telegram подставляет сам, и если пришли оба,
    выбор человека важнее. Не сработало ничего — 401. Промежуточного
    состояния «пропустим на всякий случай» здесь нет.

    Дверей три: JWT кабинета, initData из Mini App и данные Telegram
    Login Widget — вход из обычного браузера. Все три дают одну и ту же
    структуру, и эндпоинту всё равно, откуда пришёл человек.

    Роль ищется по той двери, через которую он вошёл: по
    telegram_user_id или по auth_user_id (блок Ф4 завёл эту колонку).
    База не ответила — 503, а не «пропустим без роли».
    """
    if authorization:
        if not authorization.startswith(BEARER_PREFIX):
            raise _не_авторизован()
        try:
            claims = verify_supabase_jwt(
                authorization[len(BEARER_PREFIX) :].strip(),
                secret=settings.supabase_jwt_secret or "",
                issuer=issuer_for(settings.supabase_url or ""),
            )
        except JwtError as exc:
            logger.info("current_user: токен отвергнут — %s", exc)
            raise _не_авторизован() from None
        auth_user_id = claims["sub"]
        try:
            роль = accounts.resolve_role_by_auth_user(auth_user_id)
        except SupabaseDatabaseError:
            logger.warning("current_user: роль не определена, база недоступна, sub=%s", auth_user_id, exc_info=True)
            raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None
        return CurrentUser(user_id=f"auth:{auth_user_id}", telegram_user_id=None, role=роль)

    if login_data:
        # Вход из обычного браузера через Telegram Login Widget.
        try:
            поля = verify_login_widget_string(login_data)
        except InitDataError as ошибка:
            logger.info("current_user: данные входа Telegram отвергнуты — %s", ошибка)
            raise _не_авторизован() from None
        telegram_user_id = int(поля["id"])
        try:
            роль = resolve_role(telegram_user_id)
        except SupabaseDatabaseError:
            logger.warning("current_user: роль не определена, база недоступна, id=%s", telegram_user_id, exc_info=True)
            raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None
        return CurrentUser(
            user_id=f"tg:{telegram_user_id}", telegram_user_id=telegram_user_id, role=роль
        )

    if init_data:
        try:
            authenticated = await verify_init_data(init_data)
        except HTTPException:
            raise _не_авторизован() from None
        try:
            роль = resolve_role(authenticated.telegram_user_id)
        except SupabaseDatabaseError:
            # База молчит — говорим об этом прямо. Пустить без роли
            # значило бы отдать педагогические экраны кому попало.
            logger.warning(
                "current_user: роль не определена, база недоступна, telegram_user_id=%s",
                authenticated.telegram_user_id,
                exc_info=True,
            )
            raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None
        return CurrentUser(
            user_id=f"tg:{authenticated.telegram_user_id}",
            telegram_user_id=authenticated.telegram_user_id,
            role=роль,
        )

    raise _не_авторизован()
