"""
core/limits.py — дневные лимиты на аккаунт (блок М6, PLAN_STAGE2.md,
решение автора от 26.08.2026, зафиксировано в MASTER.md 0.6, пункт 5).

Зачем модуль: одна ошибка в коде или один заскучавший пользователь не
должны съесть дневной лимит провайдера и оставить систему без генерации
до утра. Лимиты по количеству — на генерацию КСП (5/сутки) и КТП
(2/сутки); общий потолок токенов (1 000 000/сутки) — на всё сразу.

Что осознанно не делает: не проверяет лимит токенов "жёстко" — число
токенов известно только ПОСЛЕ ответа модели, поэтому проверка смотрит
на уже накопленное и не может помешать конкретному вызову перевалить
потолок задним числом. Это мягкий лимит "по последнему вызову", и это
не тихая слабость реализации — так написано и в тексте для пользователя
(bot/texts.py), потому что врать про лимит хуже, чем иметь неточный
лимит. Не считает токены транскрипции — whisper.cpp локальный, токенов
LLM не тратит.

На что опирается: core.db (прямой SQL, без ORM — тот же принцип, что и
во всём проекте)."""

from datetime import datetime, timedelta, timezone

from core.db import execute, query

# Костанай — UTC+5, Казахстан не переходит на летнее время, так что это
# не приближение, а константа. Часовой пояс задан явно, не
# datetime.utcnow() наугад: по UTC сброс лимита пришёлся бы на 05:00
# утра по местному времени — посреди обычного рабочего дня учителя
# (М6.1, ловушка плана).
KOSTANAY_TZ = timezone(timedelta(hours=5))

# Лимиты по количеству операций в сутки. Операция, которой здесь нет
# (konspekt, transcribe, textbook_ocr — появятся в блоках К2-К4), не
# ограничена по количеству вообще — так и задумано автором (MASTER.md
# 0.6, п.5: "по количеству не ограничены").
DAILY_COUNT_LIMITS = {
    "generate_ksp": 5,
    "generate_ktp": 2,
}

DAILY_TOKEN_LIMIT = 1_000_000


class LimitExceeded(Exception):
    """Дневной лимit исчерпан. reset_at — когда он обнулится (по
    Костанаю), used/limit — для текста пользователю (bot/texts.py):
    сколько потрачено и сколько разрешено, не просто "лимит исчерпан"
    (М6.3, требование плана)."""

    def __init__(self, message: str, *, reset_at: datetime, used: int, limit: int):
        super().__init__(message)
        self.reset_at = reset_at
        self.used = used
        self.limit = limit


def _today_kostanay() -> str:
    return datetime.now(KOSTANAY_TZ).date().isoformat()


def next_reset_kostanay() -> datetime:
    """Начало следующих суток по Костанаю — когда лимит обнулится.
    Используется и для сообщения пользователю (М6.3), и в тестах на
    границу смены суток."""
    today_date = datetime.now(KOSTANAY_TZ).date()
    tomorrow = today_date + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=KOSTANAY_TZ)


def check_count_limit(telegram_user_id: int, operation: str, db_path=None) -> None:
    """Бросает LimitExceeded, если operation уже достигла дневного лимита
    по количеству. Для операций без лимита (нет в DAILY_COUNT_LIMITS) —
    не проверяет вообще, ничего не бросает. Вызывать ДО постановки задачи
    в очередь (М6.2) — проверка после генерации уже потратила бы то, что
    хотели сэкономить."""
    limit = DAILY_COUNT_LIMITS.get(operation)
    if limit is None:
        return

    day = _today_kostanay()
    rows = query(
        "SELECT count FROM usage_daily WHERE telegram_user_id = ? AND day = ? AND operation = ?",
        (telegram_user_id, day, operation),
        db_path=db_path,
    )
    used = rows[0]["count"] if rows else 0
    if used >= limit:
        raise LimitExceeded(
            f"дневной лимит операции '{operation}' исчерпан: {used}/{limit}",
            reset_at=next_reset_kostanay(),
            used=used,
            limit=limit,
        )


def check_token_limit(telegram_user_id: int, db_path=None) -> None:
    """Мягкая проверка (см. шапку модуля) — смотрит уже накопленное за
    сутки по ВСЕМ операциям сразу, не может учесть токены самого
    предстоящего вызова. Вызывать ДО постановки задачи в очередь, как и
    check_count_limit."""
    day = _today_kostanay()
    rows = query(
        "SELECT SUM(tokens) AS total FROM usage_daily WHERE telegram_user_id = ? AND day = ?",
        (telegram_user_id, day),
        db_path=db_path,
    )
    total = rows[0]["total"] or 0
    if total >= DAILY_TOKEN_LIMIT:
        raise LimitExceeded(
            f"дневной потолок токенов исчерпан: {total}/{DAILY_TOKEN_LIMIT}",
            reset_at=next_reset_kostanay(),
            used=total,
            limit=DAILY_TOKEN_LIMIT,
        )


def record_usage(telegram_user_id: int, operation: str, *, count_delta: int = 0, tokens_delta: int = 0, db_path=None) -> None:
    """Прибавляет count_delta/tokens_delta к сегодняшней (по Костанаю)
    строке usage_daily для (telegram_user_id, operation), создавая её при
    необходимости. count_delta вызывать ТОЛЬКО на успешном завершении
    операции (М6.2, ловушка 2): провалившаяся задача не должна списывать
    квоту по количеству — иначе одна ошибка сервера сжигает попытку
    учителя. tokens_delta, наоборот, писать всегда, когда токены реально
    потрачены, даже если сама операция в итоге провалилась — деньги уже
    ушли провайдеру независимо от исхода."""
    day = _today_kostanay()
    execute(
        "INSERT INTO usage_daily (telegram_user_id, day, operation, count, tokens) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(telegram_user_id, day, operation) DO UPDATE SET "
        "count = count + excluded.count, tokens = tokens + excluded.tokens",
        (telegram_user_id, day, operation, count_delta, tokens_delta),
        db_path=db_path,
    )


def get_usage_today(telegram_user_id: int, db_path=None) -> dict:
    """Сводка на сегодня — для дашборда (М6.3) и текста об отказе:
    {"counts": {"generate_ksp": N, ...}, "tokens_total": N}."""
    day = _today_kostanay()
    rows = query(
        "SELECT operation, count, tokens FROM usage_daily WHERE telegram_user_id = ? AND day = ?",
        (telegram_user_id, day),
        db_path=db_path,
    )
    counts = {row["operation"]: row["count"] for row in rows}
    tokens_total = sum(row["tokens"] for row in rows)
    return {"counts": counts, "tokens_total": tokens_total}
