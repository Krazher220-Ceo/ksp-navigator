"""
tests/web/test_auth.py — тесты web/auth.py.

Валидный initData строится тем же алгоритмом, что описан в
PLAN_STAGE1.md/Б9.1 и официальной документации Telegram Web Apps, но
НЕЗАВИСИМО от web/auth.py (руками, в этом файле) — так тест реально
проверяет, что реализация соответствует внешнему стандарту, а не просто
то, что она сама с собой согласована.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException

from web.auth import InitDataError, verify_init_data, verify_init_data_string

BOT_TOKEN = "123456:AAEtest-bot-token-for-tests-only"


def _reference_hash(data_check_string: str, bot_token: str) -> str:
    """Та же формула, что в web/auth.py, но набрана отдельно, чтобы
    тест не был "тавтологией" (одна и та же ошибка в обоих местах
    осталась бы незамеченной) — здесь порядок HMAC переписан явно ещё
    раз: secret_key = HMAC(key=b"WebAppData", msg=bot_token)."""
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_init_data(
    bot_token: str = BOT_TOKEN,
    user_id: int = 12345,
    auth_date: int | None = None,
    include_hash: bool = True,
    extra_fields: dict | None = None,
) -> str:
    auth_date = int(time.time()) if auth_date is None else auth_date
    fields = {
        "query_id": "AAHtestQueryId",
        "user": json.dumps({"id": user_id, "first_name": "Тест"}, separators=(",", ":")),
        "auth_date": str(auth_date),
    }
    if extra_fields:
        fields.update(extra_fields)

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    computed_hash = _reference_hash(data_check_string, bot_token)

    if include_hash:
        fields["hash"] = computed_hash
    return urlencode(fields)


# --- (а) валидный initData проходит ---


def test_valid_init_data_passes():
    init_data = _build_init_data()
    fields = verify_init_data_string(init_data, bot_token=BOT_TOKEN)
    assert fields["auth_date"]
    assert "hash" in fields


# --- (б) изменённый на один символ initData отклоняется ---


def test_init_data_tampered_by_one_character_is_rejected():
    init_data = _build_init_data(user_id=12345)
    # меняем ОДИН символ в значении user_id внутри уже подписанной строки —
    # подпись при этом остаётся прежней (как будто злоумышленник просто
    # подправил payload, не пересчитав hash)
    tampered = init_data.replace("12345", "12346")
    assert tampered != init_data

    with pytest.raises(InitDataError):
        verify_init_data_string(tampered, bot_token=BOT_TOKEN)


def test_init_data_with_tampered_hash_itself_is_rejected():
    init_data = _build_init_data()
    # меняем последний символ самого hash
    tampered = init_data[:-1] + ("0" if init_data[-1] != "0" else "1")
    with pytest.raises(InitDataError):
        verify_init_data_string(tampered, bot_token=BOT_TOKEN)


# --- (в) auth_date двухдневной давности отклоняется ---


def test_stale_auth_date_two_days_old_is_rejected():
    two_days_ago = int(time.time()) - 2 * 24 * 3600
    init_data = _build_init_data(auth_date=two_days_ago)

    with pytest.raises(InitDataError, match="просрочен"):
        verify_init_data_string(init_data, bot_token=BOT_TOKEN)


def test_auth_date_within_one_hour_is_accepted():
    fifty_minutes_ago = int(time.time()) - 50 * 60
    init_data = _build_init_data(auth_date=fifty_minutes_ago)
    verify_init_data_string(init_data, bot_token=BOT_TOKEN)  # не должно бросить


def test_auth_date_just_over_one_hour_is_rejected():
    just_over_an_hour_ago = int(time.time()) - 3601
    init_data = _build_init_data(auth_date=just_over_an_hour_ago)
    with pytest.raises(InitDataError):
        verify_init_data_string(init_data, bot_token=BOT_TOKEN)


# --- (г) отсутствующий hash отклоняется ---


def test_missing_hash_is_rejected():
    init_data = _build_init_data(include_hash=False)
    assert "hash=" not in init_data

    with pytest.raises(InitDataError, match="hash"):
        verify_init_data_string(init_data, bot_token=BOT_TOKEN)


def test_empty_init_data_is_rejected():
    with pytest.raises(InitDataError):
        verify_init_data_string("", bot_token=BOT_TOKEN)


# --- ловушка порядка HMAC (PLAN_STAGE1.md, Б9.1) ---


def test_hmac_key_and_message_order_is_not_swapped():
    """Если бы порядок ключ/сообщение в secret_key был перепутан
    (HMAC(key=bot_token, msg="WebAppData") вместо наоборот), валидный
    initData из _build_init_data выше просто не проходил бы валидацию,
    но код при этом не падал бы — тихая ошибка. Этот тест прямо
    сравнивает результат с обоими вариантами формулы, чтобы явно
    зафиксировать: правильный вариант (msg=bot_token) действительно
    даёт другой хеш, чем перепутанный."""
    data_check_string = "auth_date=1\nquery_id=x"
    correct = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    swapped = hmac.new(BOT_TOKEN.encode(), b"WebAppData", hashlib.sha256).digest()
    assert correct != swapped

    correct_final = hmac.new(correct, data_check_string.encode(), hashlib.sha256).hexdigest()
    swapped_final = hmac.new(swapped, data_check_string.encode(), hashlib.sha256).hexdigest()
    assert correct_final != swapped_final

    # initData, подписанный ПРАВИЛЬНОЙ формулой, обязан пройти
    init_data = _build_init_data()
    verify_init_data_string(init_data, bot_token=BOT_TOKEN)


def test_wrong_bot_token_is_rejected():
    init_data = _build_init_data(bot_token=BOT_TOKEN)
    with pytest.raises(InitDataError):
        verify_init_data_string(init_data, bot_token="999999:completely-different-token")


# --- FastAPI-зависимость verify_init_data ---


async def test_dependency_rejects_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        await verify_init_data(init_data=None)
    assert exc_info.value.status_code == 401


@pytest.fixture
def bot_token_override():
    """settings — frozen dataclass-синглтон, monkeypatch.setattr на него
    падает с FrozenInstanceError; object.__setattr__ — штатный способ
    обойти иммутабельность dataclass именно для такого случая, с
    гарантированным восстановлением исходного значения."""
    import web.auth as auth_module

    original = auth_module.settings.telegram_bot_token
    object.__setattr__(auth_module.settings, "telegram_bot_token", BOT_TOKEN)
    try:
        yield
    finally:
        object.__setattr__(auth_module.settings, "telegram_bot_token", original)


async def test_dependency_accepts_valid_header(bot_token_override):
    init_data = _build_init_data(user_id=999)

    result = await verify_init_data(init_data=init_data)
    assert result.telegram_user_id == 999


async def test_dependency_rejects_tampered_header(bot_token_override):
    init_data = _build_init_data().replace("12345", "99999")

    with pytest.raises(HTTPException) as exc_info:
        await verify_init_data(init_data=init_data)
    assert exc_info.value.status_code == 401
