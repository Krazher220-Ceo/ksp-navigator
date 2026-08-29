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

from core.db import execute, query, transaction, using_supabase

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = [10, 30, 90]  # пауза перед попыткой №2, №3, (№4 - про запас)
STUCK_PROCESSING_MINUTES = 15  # порог по умолчанию — незнакомый тип задачи (М7.3)

# М7.3 (PLAN_STAGE2.md), ловушка плана дословно: "порог для транскрипции —
# минуты, для генерации КСП — секунды. Один порог на всё даст либо ложные
# тревоги, либо слепоту." На реальных замерах (KPI_STAGE1.md) генерация
# КСП занимает 25-42с даже с полными репликами педагога (блок Р2) — 3
# минуты уже с большим запасом. generate_ktp может собирать полный
# учебный год дольше — запас пошире. parse_ksp — разбор файлов без LLM,
# быстрее всех.
#
# 'transcribe' и 'generate_konspekt' заведены по аудиту этапа 2 (находка 4):
# замер в блоке К3 уже сделан, а порога по его итогам так и не появилось, и
# оба типа молча падали на общий STUCK_PROCESSING_MINUTES = 15. Для конспекта
# это вчетверо больше нужного, для транскрипции — втрое меньше.
#
# transcribe = 60 мин. Считано от худшего РЕАЛЬНОГО случая, а не от балды:
# xAI обрабатывает полный урок за секунды, но 60 минут оставлены как запас
# на загрузку двух частей, сетевые повторы и очередь.
# Порог намеренно не опущен вслед за уменьшением числа частей (было 10,
# стало 2 решением автора 27.08.2026): 60 минут теперь ещё и перекрывают
# возможные сетевые повторы xAI на двух частях. Ложная тревога здесь дороже
# позднего обнаружения.
#
# generate_konspekt = 5 мин. Один вызов LLM, живой замер — 19с; 5 минут
# покрывают три попытки LLMClient с его backoff.
STUCK_PROCESSING_MINUTES_BY_TYPE = {
    "parse_ksp": 3,
    "generate_ksp": 3,
    "generate_ktp": 5,
    "generate_konspekt": 5,
    "transcribe": 60,
}

# М7.3: раз в сколько проходов цикла проверять зависшие задачи повторно
# (не при каждом опросе — recover_stuck_tasks это UPDATE по всей таблице
# tasks, незачем гонять его чаще, чем реально может что-то зависнуть).
STUCK_RECOVERY_CHECK_EVERY_N_CYCLES = 30  # примерно раз в минуту при POLL_INTERVAL_SECONDS=2

# Как часто тикать heartbeat ВО ВРЕМЯ выполнения одной задачи (аудит этапа 2,
# находка 3). Отметка в начале прохода цикла долгую задачу не покрывает: пока
# идёт `await _process_one`, цикл стоит на этом await и heartbeat не
# обновляется. При пороге протухания 180с в scripts/watchdog.sh, запуске
# watchdog раз в 300с и тревоге на третьей неудаче подряд это давало ложный
# инцидент "воркер очереди завис" примерно через 15 минут — на исправном
# воркере, который в этот момент честно расшифровывал урок. Ложная тревога
# здесь дороже пропущенной: М7 делался ради того, чтобы уведомлениям можно
# было верить.
#
# Это НЕ маскирует по-настоящему зависшую задачу: за неё отвечает другой
# механизм — recover_stuck_tasks со своим порогом на тип задачи. Разделение
# намеренное: heartbeat отвечает на вопрос "жив ли цикл", пороги
# STUCK_PROCESSING_MINUTES_BY_TYPE — на вопрос "не застряла ли задача".
HEARTBEAT_DURING_TASK_SECONDS = 30.0


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


def _build_claim_query(db_path=None) -> str:
    """CASE по retries -> секунд задержки, из того же списка
    RETRY_DELAYS_SECONDS, что видит воркер в сообщении об ошибке —
    единый источник правды, значения не дублируются вручную в SQL."""
    cases = "\n".join(f"WHEN {i + 1} THEN {delay}" for i, delay in enumerate(RETRY_DELAYS_SECONDS))
    max_delay = RETRY_DELAYS_SECONDS[-1]
    if using_supabase(db_path):
        return f"""
            UPDATE tasks
            SET status = 'processing', updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending'
                  AND (
                        retries = 0
                        OR updated_at <= CURRENT_TIMESTAMP - (
                            CASE retries
                                {cases}
                                ELSE {max_delay}
                            END * INTERVAL '1 second'
                        )
                      )
                ORDER BY created_at
                LIMIT 1
            )
            AND status = 'pending'
            RETURNING *
        """
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
        cursor = conn.execute(_build_claim_query(db_path))
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


def recover_stuck_tasks(db_path=None, stuck_minutes: int | None = None) -> int:
    """Задачи, застрявшие в processing дольше разумного времени для ИХ
    типа (Mac уснул, процесс воркера убит, провайдер завис без таймаута,
    что угодно) — возвращает в pending. Вызывается при старте воркера и
    затем периодически из run_forever (М7.3 — не только один раз при
    старте, застрять можно и посреди долгой работы). Возвращает суммарное
    число восстановленных задач.

    stuck_minutes — необязательный override ОДНИМ порогом на все типы
    сразу (для тестов и ручной диагностики); без него — пороги по типу
    из STUCK_PROCESSING_MINUTES_BY_TYPE (М7.3, ловушка плана: один порог
    на всё даёт либо ложные тревоги, либо слепоту).

    retries увеличивается вместе с возвратом в pending (аудит этапа 2,
    находка 8). Зависшая попытка — это ИЗРАСХОДОВАННАЯ попытка: работа
    делалась и пропала. Без инкремента обработчик получал задачу с
    retries=0 и не мог отличить её от первого запуска — защита
    TRANSCRIBE_REAL_ATTEMPT_LIMIT (bot/handlers.py, К3.2) молчала, и
    транскрипция шла заново по файлам, которые предыдущая попытка уже
    удалила в своём finally. Учитель видел бодрое "Начал расшифровку",
    а следом техническую ошибку про несуществующий файл."""
    with transaction(db_path) as conn:
        if stuck_minutes is not None:
            stale_expression = "CURRENT_TIMESTAMP + (?::interval)" if using_supabase(db_path) else "datetime('now', ?)"
            cursor = conn.execute(
                "UPDATE tasks SET status = 'pending', retries = retries + 1, updated_at = CURRENT_TIMESTAMP "
                f"WHERE status = 'processing' AND updated_at <= {stale_expression} RETURNING id",
                (f"-{stuck_minutes} minutes",),
            )
            return len(cursor.fetchall())

        total_recovered = 0
        for task_type, minutes in STUCK_PROCESSING_MINUTES_BY_TYPE.items():
            stale_expression = "CURRENT_TIMESTAMP + (?::interval)" if using_supabase(db_path) else "datetime('now', ?)"
            cursor = conn.execute(
                "UPDATE tasks SET status = 'pending', retries = retries + 1, updated_at = CURRENT_TIMESTAMP "
                f"WHERE status = 'processing' AND type = ? AND updated_at <= {stale_expression} RETURNING id",
                (task_type, f"-{minutes} minutes"),
            )
            total_recovered += len(cursor.fetchall())

        # Тип, для которого своего порога ещё не завели, — старый
        # консервативный STUCK_PROCESSING_MINUTES, не молчание. Сейчас
        # таких типов нет (все из схемы перечислены выше, это проверяет
        # test_every_task_type_has_its_own_stuck_threshold), ветка
        # оставлена страховкой на будущий тип задачи.
        known_types = tuple(STUCK_PROCESSING_MINUTES_BY_TYPE.keys())
        placeholders = ",".join("?" * len(known_types))
        stale_expression = "CURRENT_TIMESTAMP + (?::interval)" if using_supabase(db_path) else "datetime('now', ?)"
        cursor = conn.execute(
            f"UPDATE tasks SET status = 'pending', retries = retries + 1, updated_at = CURRENT_TIMESTAMP "
            f"WHERE status = 'processing' AND type NOT IN ({placeholders}) "
            f"AND updated_at <= {stale_expression} RETURNING id",
            (*known_types, f"-{STUCK_PROCESSING_MINUTES} minutes"),
        )
        total_recovered += len(cursor.fetchall())

    return total_recovered


def update_worker_heartbeat(db_path=None) -> None:
    """М7.3: отметка "цикл воркера жив", читает scripts/watchdog.sh
    напрямую через sqlite3 CLI. UPSERT в одну строку (id=1) — не история,
    только последний момент."""
    execute(
        "INSERT INTO worker_heartbeat (id, updated_at) VALUES (1, CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP",
        db_path=db_path,
    )


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
        heartbeat_interval: float = HEARTBEAT_DURING_TASK_SECONDS,
    ) -> None:
        self._handlers = handlers
        self._notify = notify
        self._db_path = db_path
        self._poll_interval = poll_interval
        self._failure_message = failure_message
        # heartbeat_interval — параметр ради тестируемости (как poll_interval
        # выше): тест не должен ждать реальные 30 секунд, чтобы увидеть тик.
        self._heartbeat_interval = heartbeat_interval
        self._running = False

    async def run_forever(self) -> None:
        """Долгоживущий цикл: опрос очереди раз в poll_interval секунд.
        Останавливается вызовом stop()."""
        recovered = recover_stuck_tasks(db_path=self._db_path)
        if recovered:
            logger.warning("восстановлено %d задач(и), зависших в processing", recovered)

        self._running = True
        cycle_count = 0
        while self._running:
            # Цикл не имеет права умереть: он единственный, кто вообще
            # разбирает очередь. Любая ошибка опроса базы (транзиентная
            # 'database is locked', ошибка ввода-вывода на спящем Mac)
            # раньше выбрасывала исключение из run_forever, задача-воркер
            # тихо умирала, а бот продолжал принимать команды и класть
            # задачи в очередь, которую уже некому разбирать: ни одного
            # уведомления, всё висит в pending навсегда. Это и есть
            # нарушение KPI "задач без уведомления — ноль" (MASTER.md
            # п.1.7), причём в худшем виде — задача даже до failed не
            # доходит. Поэтому здесь ловится всё и цикл продолжается.
            try:
                # М7.3: отметка "цикл жив" на каждом проходе, ДО claim_next —
                # даже проход без готовых задач должен обновить heartbeat,
                # иначе тихая очередь выглядела бы как зависший воркер.
                update_worker_heartbeat(db_path=self._db_path)
                cycle_count += 1
                if cycle_count % STUCK_RECOVERY_CHECK_EVERY_N_CYCLES == 0:
                    recovered = recover_stuck_tasks(db_path=self._db_path)
                    if recovered:
                        logger.warning(
                            "восстановлено %d задач(и), зависших в processing (периодическая проверка)",
                            recovered,
                        )

                task = claim_next(db_path=self._db_path)
                if task is None:
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self._process_one_keeping_heartbeat(task)
            except asyncio.CancelledError:
                raise  # штатная остановка при выключении бота — не ошибка
            except Exception:
                logger.exception("сбой цикла воркера очереди, продолжаю работу")
                await asyncio.sleep(self._poll_interval)

    async def _tick_heartbeat_forever(self) -> None:
        """Фоновая отметка "цикл жив" на время выполнения одной задачи
        (аудит этапа 2, находка 3). Отменяется вызывающим кодом, когда
        задача закончилась, — сама не завершается никогда.

        Ошибку записи глушим намеренно и осознанно: heartbeat — сигнал
        наблюдаемости, а не часть работы. Уронить из-за него уже идущую
        расшифровку урока было бы хуже, чем пропустить одну отметку;
        следующая попытка через HEARTBEAT_DURING_TASK_SECONDS.
        """
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                update_worker_heartbeat(db_path=self._db_path)
            except Exception:
                logger.warning("не удалось обновить heartbeat во время задачи", exc_info=True)

    async def _process_one_keeping_heartbeat(self, task: dict) -> None:
        """_process_one с фоновым heartbeat на всё время работы задачи."""
        heartbeat = asyncio.create_task(self._tick_heartbeat_forever())
        try:
            await self._process_one(task)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

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
        # Разбор payload — тоже под защитой: битая строка в поле payload
        # (ручная правка базы, обрыв записи) раньше роняла весь воркер
        # ещё до входа в try, то есть одна испорченная задача убивала
        # обработку всех остальных. Теперь это обычный провал ОДНОЙ
        # задачи, с ретраями и финальным уведомлением, как любой другой.
        try:
            parsed_task = dict(task)
            parsed_task["payload"] = json.loads(task["payload"]) if task["payload"] else {}
        except (ValueError, TypeError) as exc:
            logger.exception("не удалось разобрать payload задачи %s", task["id"])
            await self._fail_and_maybe_notify(task, f"повреждённый payload задачи: {exc}")
            return

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

        # Запись результата тоже может не удаться (база занята, диск).
        # Работа при этом уже сделана, но пометить задачу done не вышло —
        # честно проводим это как провал задачи, а не как смерть воркера:
        # задача останется в очереди и будет повторена.
        try:
            complete(task["id"], result, db_path=self._db_path)
        except Exception as exc:
            logger.exception("не удалось записать результат задачи %s", task["id"])
            await self._fail_and_maybe_notify(task, f"не удалось сохранить результат: {exc}")

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
        try:
            await self._notify(chat_id, message)
        except Exception:
            # Задача уже помечена failed, а сказать об этом не вышло —
            # ровно тот случай, который KPI запрещает (MASTER.md п.1.7).
            # Гасить молча нельзя, ронять воркер из-за одного
            # неотправленного сообщения — тоже: остальные задачи в
            # очереди не виноваты. Поэтому громкий ERROR в лог, по
            # которому это видно грепом.
            logger.exception(
                "KPI: задача %s ушла в failed, но уведомить чат %s не удалось",
                task["id"],
                chat_id,
            )
