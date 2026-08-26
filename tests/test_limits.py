"""
tests/test_limits.py — тесты core/limits.py (блок М6).

datetime.now(KOSTANAY_TZ) не мокается глобально — вместо этого тесты на
границу суток управляют временем через прямую вставку строк usage_daily
с конкретным day, а не патчат системные часы (проще и надёжнее).
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.db import execute, init_db, query
from core.limits import (
    DAILY_COUNT_LIMITS,
    DAILY_TOKEN_LIMIT,
    KOSTANAY_TZ,
    LimitExceeded,
    check_count_limit,
    check_token_limit,
    get_usage_today,
    next_reset_kostanay,
    record_usage,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


# =====================================================================
# check_count_limit — граница КГ дословно из плана: пятая проходит,
# шестая отказывает
# =====================================================================


def test_generate_ksp_fifth_passes_sixth_raises(db_path):
    for i in range(5):
        check_count_limit(111, "generate_ksp", db_path=db_path)  # не должно бросить
        record_usage(111, "generate_ksp", count_delta=1, db_path=db_path)

    with pytest.raises(LimitExceeded) as exc_info:
        check_count_limit(111, "generate_ksp", db_path=db_path)
    assert exc_info.value.used == 5
    assert exc_info.value.limit == 5


def test_generate_ktp_limit_is_two(db_path):
    assert DAILY_COUNT_LIMITS["generate_ktp"] == 2
    for i in range(2):
        check_count_limit(111, "generate_ktp", db_path=db_path)
        record_usage(111, "generate_ktp", count_delta=1, db_path=db_path)
    with pytest.raises(LimitExceeded):
        check_count_limit(111, "generate_ktp", db_path=db_path)


def test_operation_without_configured_limit_never_raises(db_path):
    """konspekt/transcribe/textbook_ocr — по количеству не ограничены
    (MASTER.md 0.6, п.5). Любое число вызовов проходит."""
    for i in range(1000):
        check_count_limit(111, "transcribe", db_path=db_path)  # ни разу не бросает


# =====================================================================
# Разные аккаунты не мешают друг другу
# =====================================================================


def test_limits_are_per_account_not_global(db_path):
    for i in range(5):
        check_count_limit(111, "generate_ksp", db_path=db_path)
        record_usage(111, "generate_ksp", count_delta=1, db_path=db_path)

    # у аккаунта 111 лимит исчерпан, у 222 — свежий
    with pytest.raises(LimitExceeded):
        check_count_limit(111, "generate_ksp", db_path=db_path)
    check_count_limit(222, "generate_ksp", db_path=db_path)  # не бросает


# =====================================================================
# Провал не списывает count, но токены — списывает
# =====================================================================


def test_failed_task_does_not_consume_count_quota(db_path):
    """М6.2, ловушка 2: провалившаяся задача не должна списывать квоту по
    количеству — иначе одна ошибка сервера сжигает попытку учителя."""
    # 5 успешных
    for i in range(5):
        record_usage(111, "generate_ksp", count_delta=1, db_path=db_path)
    # провал — count_delta НЕ вызывается вызывающим кодом вовсе (это
    # ответственность вызывающего, не record_usage), лимит остаётся 5/5,
    # не 6/5 и не блокирует навсегда
    usage = get_usage_today(111, db_path=db_path)
    assert usage["counts"]["generate_ksp"] == 5


def test_tokens_recorded_even_when_operation_ultimately_fails(db_path):
    """Токены, если реально потрачены до провала, списываются — деньги
    уже ушли провайдеру независимо от исхода операции."""
    record_usage(111, "generate_ksp", tokens_delta=5000, db_path=db_path)
    # операция "провалилась" дальше по конвейеру — но токены уже записаны
    usage = get_usage_today(111, db_path=db_path)
    assert usage["tokens_total"] == 5000
    assert usage["counts"].get("generate_ksp", 0) == 0  # count не выставлен


# =====================================================================
# Потолок токенов — общий по всем операциям, мягкий по последнему вызову
# =====================================================================


def test_token_limit_is_shared_across_operations(db_path):
    record_usage(111, "generate_ksp", tokens_delta=600_000, db_path=db_path)
    record_usage(111, "generate_ktp", tokens_delta=500_000, db_path=db_path)
    # 1 100 000 суммарно >= 1 000 000 — потолок общий, не по операции
    with pytest.raises(LimitExceeded) as exc_info:
        check_token_limit(111, db_path=db_path)
    assert exc_info.value.used == 1_100_000


def test_token_limit_not_exceeded_passes(db_path):
    record_usage(111, "generate_ksp", tokens_delta=100_000, db_path=db_path)
    check_token_limit(111, db_path=db_path)  # не бросает


def test_token_limit_soft_by_last_call_documented_behavior(db_path):
    """Честная ловушка плана: проверка смотрит на уже накопленное, вызов,
    который перевалит потолок, задним числом не отменяется — это
    проверяется тем, что check_token_limit пройдёт, даже если сейчас
    999 999, а следующий вызов потратит ещё 50 000 и перевалит потолок:
    check_token_limit сама по себе не знает об этом заранее."""
    record_usage(111, "generate_ksp", tokens_delta=DAILY_TOKEN_LIMIT - 1, db_path=db_path)
    check_token_limit(111, db_path=db_path)  # проходит, хотя следующий вызов перевалит


# =====================================================================
# Сброс по смене суток (по Костанаю, не по UTC)
# =====================================================================


def test_usage_resets_on_new_kostanay_day(db_path):
    yesterday = (datetime.now(KOSTANAY_TZ) - timedelta(days=1)).date().isoformat()
    execute(
        "INSERT INTO usage_daily (telegram_user_id, day, operation, count, tokens) VALUES (?, ?, ?, ?, ?)",
        (111, yesterday, "generate_ksp", 5, 500_000),
        db_path=db_path,
    )
    # вчерашние 5/5 не должны блокировать сегодня
    check_count_limit(111, "generate_ksp", db_path=db_path)
    usage_today = get_usage_today(111, db_path=db_path)
    assert usage_today["counts"].get("generate_ksp", 0) == 0


def test_next_reset_kostanay_is_midnight_kostanay_not_utc():
    reset = next_reset_kostanay()
    assert reset.tzinfo == KOSTANAY_TZ
    assert reset.hour == 0
    assert reset.minute == 0
    assert reset > datetime.now(KOSTANAY_TZ)


# =====================================================================
# record_usage — накопление, а не перезапись
# =====================================================================


def test_record_usage_accumulates_not_overwrites(db_path):
    record_usage(111, "generate_ksp", count_delta=1, tokens_delta=1000, db_path=db_path)
    record_usage(111, "generate_ksp", count_delta=1, tokens_delta=2000, db_path=db_path)
    usage = get_usage_today(111, db_path=db_path)
    assert usage["counts"]["generate_ksp"] == 2
    assert usage["tokens_total"] == 3000


def test_get_usage_today_empty_account_returns_zeros(db_path):
    usage = get_usage_today(999, db_path=db_path)
    assert usage == {"counts": {}, "tokens_total": 0}
