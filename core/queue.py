"""
core/queue.py — очередь фоновых задач (parse_ksp, generate_ksp) в SQLite.

Зачем модуль: rate-limit у LLM-провайдера, временная недоступность сети
или падение Mac не должны терять запрос учителя — задача остаётся в
таблице tasks до тех пор, пока не будет либо успешно выполнена, либо
исчерпает попытки (и тогда пользователь ГАРАНТИРОВАННО получит
уведомление — это KPI из MASTER.md, п.1.7: "задач в failed без
уведомления — ноль").

Что осознанно не делает: не знает про Telegram/aiogram напрямую —
QueueWorker принимает функцию notify(chat_id, text) снаружи (её даст
bot/handlers.py, блок Б8), чтобы core/ не зависел от bot/. Не решает,
ЧТО делать с задачей каждого типа — это handlers, которые регистрирует
вызывающий код; queue.py только гарантирует, что задача не потеряется.

На что опирается: core.db (транзакции, атомарный UPDATE...RETURNING —
SQLite 3.35+, здесь 3.51, так что доступно).

Как устроена растущая пауза между попытками (10с -> 30с -> 90с): НЕ
через asyncio.sleep() внутри воркера — это заблокировало бы весь цикл
и не дало бы обработать другие задачи, пока эта "отдыхает". Вместо
этого пауза встроена в сам claim_next(): задача с retries > 0 просто
не считается готовой к захвату, пока не пройдёт нужное время с
последней попытки (updated_at). Воркер как опрашивал очередь раз в
POLL_INTERVAL_SECONDS, так и продолжает — просто "не готовые" задачи
временно не попадают в выборку.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Awaitable, Callable

from core.db import query, transaction

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = [10, 30, 90]  # пауза перед попыткой №2, №3, (№4 - про запас)
STUCK_PROCESSING_MINUTES = 15


# =====================================================================
# Б7.1 — примитивы очереди
# =====================================================================


def enqueue(task_type: str, payload: dict, chat_id: int | None = None, db_path=None) -> str:
    """Создаёт задачу со статусом pending. task_type — 'parse_ksp' или
    'generate_ksp'; неверное значение отклонит сам CHECK в schema.sql
    (sqlite3.IntegrityError) — дублировать эту проверку в Python не нужно."""
    task_id = str(uuid.uuid4())
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, status, payload, telegram_chat_id, retries) "
            "VALUES (?, ?, 'pending', ?, ?, 0)",
            (task_id, task_type, json.dumps(payload, ensure_ascii=False), chat_id),
        )
    return task_id


def _build_claim_query() -> str:
    """CASE по retries -> секунд задержки, из того же списка
    RETRY_DELAYS_SECONDS, что видит воркер в сообщении об ошибке —
    единый источник правды, значения не дублируются вручную в SQL."""
    cases = "\n".join(f"WHEN {i + 1} THEN {delay}" for i, delay in enumerate(RETRY_DELAYS_SECONDS))
    max_delay = RETRY_DELAYS_SECONDS[-1]
    return f"""
        UPDATE tasks
        SET status = 'processing', updated_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id FROM tasks
            WHERE status = 'pending'
              AND (
                    retries = 0
                    OR updated_at <= datetime('now', '-' || (
                        CASE retries
                            {cases}
                            ELSE {max_delay}
                        END
                    ) || ' seconds')
                  )
            ORDER BY created_at
            LIMIT 1
        )
        AND status = 'pending'
        RETURNING *;
    """


_CLAIM_QUERY = _build_claim_query()


def claim_next(db_path=None) -> dict | None:
    """Атомарно забирает следующую готовую задачу: pending -> processing
    одним UPDATE ... RETURNING (не SELECT, потом UPDATE — между двумя
    отдельными запросами два воркера могли бы выбрать одну и ту же
    задачу). Возвращает None, если готовых задач нет (включая те, что
    ещё "отдыхают" после неудачной попытки)."""
    with transaction(db_path) as conn:
        cursor = conn.execute(_CLAIM_QUERY)
        row = cursor.fetchone()
    return dict(row) if row is not None else None


def complete(task_id: str, result: Any, db_path=None) -> None:
    result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET status = 'done', result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (result_text, task_id),
        )


def fail(task_id: str, error: str, max_retries: int = MAX_RETRIES, db_path=None) -> str:
    """Записывает провалившуюся попытку. Если после инкремента retries
    ещё меньше max_retries — задача возвращается в pending (её заберёт
    claim_next() после нужной паузы). Иначе — terminal 'failed'.

    Возвращает итоговый статус ('pending' или 'failed'), чтобы
    вызывающий код (QueueWorker) знал, обязан ли он уведомить
    пользователя прямо сейчас."""
    with transaction(db_path) as conn:
        cursor = conn.execute(
            "UPDATE tasks SET "
            "retries = retries + 1, "
            "status = CASE WHEN retries + 1 < ? THEN 'pending' ELSE 'failed' END, "
            "error = ?, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? "
            "RETURNING status",
            (max_retries, error, task_id),
        )
        row = cursor.fetchone()
    if row is None:
        raise ValueError(f"задача {task_id} не найдена")
    return row["status"]


def recover_stuck_tasks(db_path=None, stuck_minutes: int = STUCK_PROCESSING_MINUTES) -> int:
    """Задачи, застрявшие в processing дольше stuck_minutes (Mac уснул,
    процесс воркера убит, что угодно) — возвращает в pending. Вызывается
    один раз при старте воркера (run_forever/run_until_idle). Возвращает
    число восстановленных задач."""
    with transaction(db_path) as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'pending', updated_at = CURRENT_TIMESTAMP "
            "WHERE status = 'processing' "
            "AND updated_at <= datetime('now', ?) "
            "RETURNING id",
            (f"-{stuck_minutes} minutes",),
        )
        rows = cursor.fetchall()
    return len(rows)


# =====================================================================
# Б7.2 — воркер
# =====================================================================

TaskHandler = Callable[[dict], Awaitable[Any]]
NotifyFn = Callable[[int, str], Awaitable[None]]


def _default_failure_message(task: dict, error: str) -> str:
    return (
        "Не получилось выполнить задачу после нескольких попыток.\n"
        f"Тип задачи: {task.get('type')}\n"
        f"Причина: {error}\n"
        "Попробуйте ещё раз позже."
    )


class QueueWorker:
    """Асинхронный цикл: claim_next -> handler(task) -> complete, или
    при ошибке -> fail(); при terminal 'failed' — уведомление
    пользователя ОБЯЗАТЕЛЬНО (это гарантирует сам воркер, не handler,
    чтобы ни один обработчик не мог случайно этот шаг пропустить).

    handlers — {"parse_ksp": async_fn, "generate_ksp": async_fn}, каждый
    получает разобранный task (payload уже json.loads()) и делает свою
    работу; успешное уведомление пользователя о результате — забота
    самого handler'а (он знает, что именно отправить — файл, текст).
    notify — async fn(chat_id, text), только для гарантированного
    уведомления о провале; откуда его брать (aiogram Bot.send_message)
    решает вызывающий код в bot/handlers.py."""

    def __init__(
        self,
        handlers: dict[str, TaskHandler],
        notify: NotifyFn,
        db_path=None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        failure_message: Callable[[dict, str], str] = _default_failure_message,
    ) -> None:
        self._handlers = handlers
        self._notify = notify
        self._db_path = db_path
        self._poll_interval = poll_interval
        self._failure_message = failure_message
        self._running = False

    async def run_forever(self) -> None:
        """Долгоживущий цикл: опрос очереди раз в poll_interval секунд.
        Останавливается вызовом stop()."""
        recovered = recover_stuck_tasks(db_path=self._db_path)
        if recovered:
            logger.warning("восстановлено %d задач(и), зависших в processing", recovered)

        self._running = True
        while self._running:
            task = claim_next(db_path=self._db_path)
            if task is None:
                await asyncio.sleep(self._poll_interval)
                continue
            await self._process_one(task)

    def stop(self) -> None:
        self._running = False

    async def run_until_idle(self, max_iterations: int = 1000) -> int:
        """Обрабатывает задачи, пока есть что взять прямо сейчас (без
        ожидания растущей паузы у ретраящихся задач). Возвращает число
        обработанных задач. Для тестов и разовых прогонов — run_forever()
        для реального долгоживущего процесса."""
        recover_stuck_tasks(db_path=self._db_path)
        processed = 0
        for _ in range(max_iterations):
            task = claim_next(db_path=self._db_path)
            if task is None:
                break
            await self._process_one(task)
            processed += 1
        return processed

    async def _process_one(self, task: dict) -> None:
        parsed_task = dict(task)
        parsed_task["payload"] = json.loads(task["payload"]) if task["payload"] else {}

        handler = self._handlers.get(task["type"])
        if handler is None:
            await self._fail_and_maybe_notify(
                task, f"неизвестный тип задачи: {task['type']!r} (нет обработчика)"
            )
            return

        try:
            result = await handler(parsed_task)
        except Exception as exc:  # обработчик может упасть как угодно - это ожидаемо
            logger.exception("задача %s (%s) провалилась", task["id"], task["type"])
            await self._fail_and_maybe_notify(task, str(exc))
            return

        complete(task["id"], result, db_path=self._db_path)

    async def _fail_and_maybe_notify(self, task: dict, error_text: str) -> None:
        status = fail(task["id"], error_text, db_path=self._db_path)
        if status != "failed":
            return  # ещё будет ретрай, уведомлять рано

        chat_id = task.get("telegram_chat_id")
        if chat_id is None:
            logger.error(
                "задача %s провалилась окончательно, но у нее нет telegram_chat_id — "
                "уведомить пользователя невозможно",
                task["id"],
            )
            return

        message = self._failure_message(task, error_text)
        await self._notify(chat_id, message)
