"""
tests/core/test_queue.py — тесты core/queue.py.

Тест на гонку (test_claim_next_is_atomic_under_concurrent_access) гоняет
настоящие потоки ОС против одной и той же базы — не имитация, а реальная
конкурентная нагрузка на claim_next().
"""

import json
import threading
from pathlib import Path

import pytest

from core.config import settings
from core.db import execute, init_db, query, transaction
from core.queue import (
    MAX_RETRIES,
    RETRY_DELAYS_SECONDS,
    STUCK_PROCESSING_MINUTES_BY_TYPE,
    QueueWorker,
    _build_claim_query,
    claim_next,
    complete,
    enqueue,
    fail,
    recover_stuck_tasks,
    update_worker_heartbeat,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def _age_updated_at(task_id: str, seconds_ago: int, db_path) -> None:
    """Хелпер для тестов: искусственно "состарить" updated_at задачи —
    ждать реальные секунды растущей паузы в тестах не будем."""
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET updated_at = datetime('now', ?) WHERE id = ?",
            (f"-{seconds_ago} seconds", task_id),
        )


# --- Б7.1: базовые примитивы ---


@pytest.mark.parametrize("db_backend", ["sqlite", "supabase"])
def test_build_claim_query_has_no_leading_or_trailing_whitespace(db_backend):
    """Регресс: RPC ksp_execute_sql на стороне Supabase отклоняет запрос
    с 400 Bad Request, если он начинается с перевода строки от отступа
    f-строки — заметно только на реальном проде, SQLite к пробелам
    равнодушен, поэтому баг не ловился тестами до сих пор (найдено при
    перезапуске бота после блока Н1, PLAN.md)."""
    original = settings.db_backend
    object.__setattr__(settings, "db_backend", db_backend)
    try:
        sql = _build_claim_query()
    finally:
        object.__setattr__(settings, "db_backend", original)

    assert sql == sql.strip()
    assert sql.upper().startswith("UPDATE")


def test_enqueue_creates_pending_task(db_path):
    task_id = enqueue("generate_ksp", {"topic": "Тема"}, chat_id=42, db_path=db_path)
    row = query("SELECT * FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]

    assert row["status"] == "pending"
    assert row["type"] == "generate_ksp"
    assert row["telegram_chat_id"] == 42
    assert row["retries"] == 0
    assert json.loads(row["payload"]) == {"topic": "Тема"}


def test_enqueue_rejects_unknown_task_type(db_path):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        enqueue("mystery_type", {}, db_path=db_path)


def test_claim_next_returns_none_when_empty(db_path):
    assert claim_next(db_path=db_path) is None


def test_claim_next_marks_task_processing(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    task = claim_next(db_path=db_path)

    assert task["id"] == task_id
    assert task["status"] == "processing"

    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "processing"


def test_claim_next_does_not_return_already_claimed_task(db_path):
    enqueue("generate_ksp", {}, db_path=db_path)
    first = claim_next(db_path=db_path)
    second = claim_next(db_path=db_path)

    assert first is not None
    assert second is None


def test_claim_next_picks_oldest_pending_first(db_path):
    first_id = enqueue("generate_ksp", {"n": 1}, db_path=db_path)
    second_id = enqueue("generate_ksp", {"n": 2}, db_path=db_path)

    claimed = claim_next(db_path=db_path)
    assert claimed["id"] == first_id

    claimed2 = claim_next(db_path=db_path)
    assert claimed2["id"] == second_id


def test_complete_sets_status_done_and_result(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    complete(task_id, {"docx_path": "/tmp/x.docx"}, db_path=db_path)

    row = query("SELECT status, result FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "done"
    assert json.loads(row["result"]) == {"docx_path": "/tmp/x.docx"}


# --- КГ Б7.1: атомарность claim_next под настоящей гонкой ---


def test_claim_next_is_atomic_under_concurrent_access(db_path):
    """Два (точнее восемь) воркера одновременно колотят в claim_next()
    по одной и той же базе — ни одна из 50 задач не должна достаться
    более чем одному потоку."""
    task_ids = [enqueue("generate_ksp", {"n": i}, db_path=db_path) for i in range(50)]

    claimed_by_thread: list[list[str]] = [[] for _ in range(8)]

    def worker(idx: int) -> None:
        while True:
            task = claim_next(db_path=db_path)
            if task is None:
                break
            claimed_by_thread[idx].append(task["id"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_claimed = [tid for lst in claimed_by_thread for tid in lst]

    assert len(all_claimed) == 50, f"должно быть забрано ровно 50 задач, забрано {len(all_claimed)}"
    assert len(set(all_claimed)) == 50, "нашлись дубликаты — задачу забрали дважды"
    assert set(all_claimed) == set(task_ids)


# --- Б7.2: fail() — ретраи с растущей паузой, terminal failed ---


def test_fail_returns_pending_before_max_retries(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)

    status = fail(task_id, "временная ошибка", db_path=db_path)
    assert status == "pending"

    row = query("SELECT retries FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["retries"] == 1


def test_fail_reaches_terminal_failed_after_max_retries(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)

    for attempt in range(MAX_RETRIES):
        status = fail(task_id, f"ошибка попытки {attempt + 1}", db_path=db_path)
        if attempt < MAX_RETRIES - 1:
            _age_updated_at(task_id, RETRY_DELAYS_SECONDS[attempt] + 1, db_path)
            claimed = claim_next(db_path=db_path)
            assert claimed is not None, f"задача должна была стать доступной после попытки {attempt + 1}"

    assert status == "failed"
    row = query("SELECT retries, status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "failed"
    assert row["retries"] == MAX_RETRIES


def test_fail_on_unknown_task_raises():
    with pytest.raises(ValueError):
        fail("не-существующий-id", "ошибка")


def test_retrying_task_not_claimable_before_backoff_elapses(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    fail(task_id, "ошибка", db_path=db_path)  # retries=1, нужно 10с ожидания

    assert claim_next(db_path=db_path) is None  # ещё рано

    _age_updated_at(task_id, RETRY_DELAYS_SECONDS[0] + 1, db_path)
    ready = claim_next(db_path=db_path)
    assert ready is not None
    assert ready["id"] == task_id


def test_other_pending_tasks_are_not_blocked_by_a_retrying_task(db_path):
    """Ключевое свойство дизайна: пока задача A "отдыхает" после ошибки,
    воркер должен уметь забрать другую готовую задачу B, а не стоять
    и ждать А."""
    task_a = enqueue("generate_ksp", {"n": "a"}, db_path=db_path)
    claim_next(db_path=db_path)
    fail(task_a, "ошибка", db_path=db_path)  # A теперь "отдыхает" 10с

    task_b = enqueue("generate_ksp", {"n": "b"}, db_path=db_path)
    claimed = claim_next(db_path=db_path)

    assert claimed["id"] == task_b


# --- Б7.2: восстановление зависших задач ---


def test_recover_stuck_tasks_resets_old_processing_to_pending(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 16 * 60, db_path)  # "застряла" 16 минут назад

    recovered_count = recover_stuck_tasks(db_path=db_path)

    assert recovered_count == 1
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "pending"


def test_recover_stuck_tasks_leaves_recent_processing_alone(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)  # только что взята, не зависла

    recovered_count = recover_stuck_tasks(db_path=db_path)

    assert recovered_count == 0
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "processing"


def test_recovered_task_can_be_claimed_and_completed_again(db_path):
    """КГ Б7.2 буквально: "убить процесс во время обработки -> после
    перезапуска задача доделывается"."""
    task_id = enqueue("generate_ksp", {"topic": "X"}, db_path=db_path)
    claim_next(db_path=db_path)  # воркер №1 взял задачу...
    _age_updated_at(task_id, 20 * 60, db_path)  # ...и "упал", не завершив

    # воркер №2 стартует
    recover_stuck_tasks(db_path=db_path)
    # Восстановление засчитывает израсходованную попытку (аудит этапа 2,
    # находка 8), поэтому перед повтором действует обычная растущая пауза.
    # В проде воркер просто заберёт задачу следующим проходом через 10с;
    # в тесте не ждём реальные секунды, а состариваем отметку.
    _age_updated_at(task_id, RETRY_DELAYS_SECONDS[0] + 1, db_path)
    task = claim_next(db_path=db_path)
    assert task is not None
    assert task["id"] == task_id

    complete(task_id, {"done": True}, db_path=db_path)
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "done"


# --- QueueWorker ---


class _RecordingNotifier:
    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    async def __call__(self, chat_id: int, text: str) -> None:
        self.calls.append((chat_id, text))


async def test_worker_completes_task_on_successful_handler(db_path):
    async def handler(task):
        return {"ok": True, "topic": task["payload"]["topic"]}

    notifier = _RecordingNotifier()
    worker = QueueWorker({"generate_ksp": handler}, notify=notifier, db_path=db_path)

    task_id = enqueue("generate_ksp", {"topic": "Тема"}, chat_id=1, db_path=db_path)
    processed = await worker.run_until_idle()

    assert processed == 1
    row = query("SELECT status, result FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "done"
    assert json.loads(row["result"]) == {"ok": True, "topic": "Тема"}
    assert notifier.calls == []  # успех - обязательного уведомления от воркера не требуется


async def test_worker_notifies_on_terminal_failure_kpi(db_path):
    """Главная гарантия блока: после третьей неудачи пользователь
    ОБЯЗАН получить сообщение (KPI из MASTER.md, п.1.7)."""
    call_count = {"n": 0}

    async def always_failing_handler(task):
        call_count["n"] += 1
        raise RuntimeError("LLM недоступен")

    notifier = _RecordingNotifier()
    worker = QueueWorker(
        {"generate_ksp": always_failing_handler}, notify=notifier, db_path=db_path
    )

    task_id = enqueue("generate_ksp", {}, chat_id=777, db_path=db_path)

    # попытка №1
    await worker.run_until_idle()
    assert notifier.calls == []  # ещё не terminal
    _age_updated_at(task_id, RETRY_DELAYS_SECONDS[0] + 1, db_path)

    # попытка №2
    await worker.run_until_idle()
    assert notifier.calls == []
    _age_updated_at(task_id, RETRY_DELAYS_SECONDS[1] + 1, db_path)

    # попытка №3 - terminal
    await worker.run_until_idle()

    assert call_count["n"] == 3
    assert len(notifier.calls) == 1
    chat_id, message = notifier.calls[0]
    assert chat_id == 777
    assert "LLM недоступен" in message

    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "failed"


async def test_worker_never_leaves_failed_task_without_notification(db_path):
    """Прямая проверка KPI-инварианта: сколько угодно неуспешных задач —
    ни одна не должна оказаться в failed без вызова notify()."""
    async def always_failing_handler(task):
        raise RuntimeError("ошибка")

    notifier = _RecordingNotifier()
    worker = QueueWorker({"generate_ksp": always_failing_handler}, notify=notifier, db_path=db_path)

    task_ids = [enqueue("generate_ksp", {}, chat_id=i, db_path=db_path) for i in range(5)]

    for _ in range(MAX_RETRIES):
        await worker.run_until_idle()
        with transaction(db_path) as conn:
            conn.execute(
                "UPDATE tasks SET updated_at = datetime('now', '-100 seconds') WHERE status='pending'"
            )

    failed_rows = query("SELECT id FROM tasks WHERE status = 'failed'", db_path=db_path)
    assert {r["id"] for r in failed_rows} == set(task_ids)
    notified_chat_ids = {chat_id for chat_id, _ in notifier.calls}
    assert notified_chat_ids == set(range(5))


async def test_worker_handles_unknown_task_type_without_crashing(db_path):
    """Задача без зарегистрированного обработчика - не крашит воркер,
    в итоге тоже уходит в failed с уведомлением."""
    notifier = _RecordingNotifier()
    worker = QueueWorker({}, notify=notifier, db_path=db_path)  # ни одного обработчика

    task_id = enqueue("parse_ksp", {}, chat_id=5, db_path=db_path)

    for _ in range(MAX_RETRIES):
        await worker.run_until_idle()
        with transaction(db_path) as conn:
            conn.execute("UPDATE tasks SET updated_at = datetime('now', '-100 seconds') WHERE id=?", (task_id,))

    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "failed"
    assert len(notifier.calls) == 1


async def test_worker_recovers_stuck_task_at_startup_and_completes_it(db_path):
    """Полный сценарий КГ: воркер №1 взял задачу и "упал" (процесс убит),
    воркер №2 стартует и доделывает работу."""
    task_id = enqueue("generate_ksp", {"topic": "После падения"}, chat_id=1, db_path=db_path)
    claim_next(db_path=db_path)  # имитация воркера №1, который не успел завершить
    _age_updated_at(task_id, 20 * 60, db_path)

    async def handler(task):
        return {"topic": task["payload"]["topic"]}

    notifier = _RecordingNotifier()
    worker2 = QueueWorker({"generate_ksp": handler}, notify=notifier, db_path=db_path)
    # run_until_idle сам зовёт recover_stuck_tasks, и после него у задачи
    # есть израсходованная попытка с растущей паузой (аудит, находка 8) —
    # состариваем отметку, чтобы не ждать 10 реальных секунд в тесте.
    recover_stuck_tasks(db_path=db_path)
    _age_updated_at(task_id, RETRY_DELAYS_SECONDS[0] + 1, db_path)
    processed = await worker2.run_until_idle()

    assert processed == 1
    row = query("SELECT status, result FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "done"
    assert json.loads(row["result"]) == {"topic": "После падения"}


async def test_worker_run_forever_can_be_stopped(db_path):
    async def handler(task):
        return {"ok": True}

    notifier = _RecordingNotifier()
    worker = QueueWorker(
        {"generate_ksp": handler}, notify=notifier, db_path=db_path, poll_interval=0.01
    )

    import asyncio

    run_task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(run_task, timeout=1.0)  # не должно зависнуть навсегда


# =====================================================================
# М7.3 — пороги зависания по типу задачи, живость воркера
# =====================================================================


def test_generate_ksp_stuck_threshold_is_three_minutes(db_path):
    """generate_ksp реально занимает 25-42с на живых замерах
    (KPI_STAGE1.md) — 3 минуты порог с большим запасом, не 15, как было
    общим порогом до блока М7."""
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 4 * 60, db_path)  # 4 минуты > порога 3 минуты

    recovered = recover_stuck_tasks(db_path=db_path)
    assert recovered == 1
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "pending"


def test_generate_ksp_not_recovered_before_its_own_threshold(db_path):
    task_id = enqueue("generate_ksp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 2 * 60, db_path)  # 2 минуты < порога 3 минуты

    recovered = recover_stuck_tasks(db_path=db_path)
    assert recovered == 0
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "processing"


def test_generate_ktp_has_its_own_longer_threshold(db_path):
    """generate_ktp может собирать полный учебный год дольше, чем КСП —
    порог 5 минут, отдельный от generate_ksp (3 минуты). Задача,
    зависшая на 4 минуты, должна остаться processing для КТП, но была
    бы восстановлена для КСП — ровно та ловушка плана про "один порог на
    всё"."""
    assert STUCK_PROCESSING_MINUTES_BY_TYPE["generate_ktp"] > STUCK_PROCESSING_MINUTES_BY_TYPE["generate_ksp"]

    task_id = enqueue("generate_ktp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 4 * 60, db_path)

    recovered = recover_stuck_tasks(db_path=db_path)
    assert recovered == 0
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "processing"


def test_different_types_recovered_independently_in_same_call(db_path):
    """Один вызов recover_stuck_tasks обрабатывает все типы разом, каждый
    по своему порогу — не только generate_ksp, если в очереди вперемешку
    разные типы."""
    ksp_id = enqueue("generate_ksp", {}, db_path=db_path)
    ktp_id = enqueue("generate_ktp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(ksp_id, 4 * 60, db_path)  # старше порога generate_ksp (3 мин)
    _age_updated_at(ktp_id, 4 * 60, db_path)  # младше порога generate_ktp (5 мин)

    recovered = recover_stuck_tasks(db_path=db_path)
    assert recovered == 1

    ksp_status = query("SELECT status FROM tasks WHERE id = ?", (ksp_id,), db_path=db_path)[0]["status"]
    ktp_status = query("SELECT status FROM tasks WHERE id = ?", (ktp_id,), db_path=db_path)[0]["status"]
    assert ksp_status == "pending"
    assert ktp_status == "processing"


def test_unknown_task_type_uses_conservative_default_threshold(db_path):
    """Тип задачи без своего порога в STUCK_PROCESSING_MINUTES_BY_TYPE
    (например будущий 'transcribe' до блока К3) — не молчание, а старый
    консервативный порог (STUCK_PROCESSING_MINUTES=15)."""
    task_id = enqueue("parse_ksp", {}, db_path=db_path)  # известный тип, для контраста
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 4 * 60, db_path)  # старше своего порога (3 мин)

    recovered = recover_stuck_tasks(db_path=db_path)
    assert recovered == 1  # parse_ksp - известный тип, порог 3 минуты сработал


def test_stuck_minutes_override_applies_uniformly_to_all_types(db_path):
    """Явный stuck_minutes (для тестов/ручной диагностики) игнорирует
    пороги по типу и применяется одним числом ко всем сразу — задача
    generate_ktp (у которой собственный порог 5 минут) восстанавливается
    уже через 2 минуты, если задан override=1."""
    task_id = enqueue("generate_ktp", {}, db_path=db_path)
    claim_next(db_path=db_path)
    _age_updated_at(task_id, 2 * 60, db_path)  # 2 минуты — младше порога generate_ktp (5), но старше override (1)

    recovered = recover_stuck_tasks(db_path=db_path, stuck_minutes=1)
    assert recovered == 1
    row = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "pending"


def test_worker_heartbeat_creates_row_when_missing(db_path):
    rows = query("SELECT * FROM worker_heartbeat", db_path=db_path)
    assert rows == []

    update_worker_heartbeat(db_path=db_path)

    rows = query("SELECT * FROM worker_heartbeat WHERE id = 1", db_path=db_path)
    assert len(rows) == 1


def test_worker_heartbeat_updates_existing_row_not_duplicates(db_path):
    update_worker_heartbeat(db_path=db_path)
    update_worker_heartbeat(db_path=db_path)
    update_worker_heartbeat(db_path=db_path)

    rows = query("SELECT * FROM worker_heartbeat", db_path=db_path)
    assert len(rows) == 1


async def test_run_forever_updates_heartbeat_on_each_cycle(db_path):
    """М7.3 КГ: воркер обновляет отметку "последний проход цикла"."""
    import asyncio

    worker = QueueWorker(handlers={}, notify=lambda c, t: None, db_path=db_path, poll_interval=0.01)
    run_task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(run_task, timeout=1.0)

    rows = query("SELECT * FROM worker_heartbeat WHERE id = 1", db_path=db_path)
    assert len(rows) == 1


# =====================================================================
# Аудит этапа 2, находка 3 — heartbeat тикает и ВО ВРЕМЯ долгой задачи
# =====================================================================


async def test_heartbeat_updated_while_long_task_is_running(db_path):
    """Пока идёт `await _process_one`, цикл воркера стоит на этом await и
    отметку "жив" не ставит. При пороге протухания 180с в watchdog.sh это
    давало ложный инцидент "воркер завис" на исправном воркере, честно
    расшифровывающем урок.

    Проверяем настоящий эффект, а не факт вызова: ставим отметку заведомо
    старой, запускаем медленную задачу и смотрим, что строка в базе за
    время её работы обновилась."""
    import asyncio

    execute(
        "INSERT INTO worker_heartbeat (id, updated_at) VALUES (1, datetime('now', '-2 hours')) "
        "ON CONFLICT(id) DO UPDATE SET updated_at = datetime('now', '-2 hours')",
        db_path=db_path,
    )
    stale_before = query("SELECT updated_at FROM worker_heartbeat WHERE id = 1", db_path=db_path)[0]["updated_at"]

    async def slow_handler(task):
        await asyncio.sleep(0.3)
        return {"ok": True}

    task_id = enqueue("generate_ksp", {}, chat_id=1, db_path=db_path)
    worker = QueueWorker(
        handlers={"generate_ksp": slow_handler},
        notify=lambda c, t: None,
        db_path=db_path,
        heartbeat_interval=0.05,
    )
    claimed = claim_next(db_path=db_path)
    await worker._process_one_keeping_heartbeat(claimed)

    after = query("SELECT updated_at FROM worker_heartbeat WHERE id = 1", db_path=db_path)[0]["updated_at"]
    assert after != stale_before, "отметка не обновилась за время задачи — watchdog счёл бы воркер зависшим"

    rows = query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)
    assert rows[0]["status"] == "done"  # сама задача при этом отработала штатно


async def test_heartbeat_ticker_stops_after_task_finishes(db_path):
    """Фоновая отметка не должна пережить задачу — иначе за сутки
    накопятся сотни висящих корутин, а "жив" будет тикать даже когда
    цикл уже остановлен."""
    import asyncio

    async def quick_handler(task):
        return {"ok": True}

    enqueue("generate_ksp", {}, chat_id=1, db_path=db_path)
    worker = QueueWorker(
        handlers={"generate_ksp": quick_handler},
        notify=lambda c, t: None,
        db_path=db_path,
        heartbeat_interval=0.01,
    )
    before = len(asyncio.all_tasks())
    await worker._process_one_keeping_heartbeat(claim_next(db_path=db_path))
    await asyncio.sleep(0.05)

    assert len(asyncio.all_tasks()) <= before, "фоновая корутина heartbeat пережила задачу"


# =====================================================================
# Аудит этапа 2, находка 4 — свои пороги зависания у transcribe и
# generate_konspekt (М7.3 требовал порог на тип, они падали на общий)
# =====================================================================


async def test_long_running_transcribe_is_not_recovered_too_early(db_path):
    """Транскрипция многочастевого урока идёт десятки минут. На общем
    пороге в 15 минут её признавали зависшей и отбирали у живого воркера
    посреди работы. Через 20 минут работы задача обязана остаться в
    processing."""
    task_id = enqueue("transcribe", {"audio_paths": []}, chat_id=1, db_path=db_path)
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-20 minutes') WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )

    recovered = recover_stuck_tasks(db_path=db_path)

    assert recovered == 0
    assert query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]["status"] == "processing"


async def test_truly_stuck_transcribe_is_still_recovered(db_path):
    """Граница: порог не бесконечный. Задача, висящая дольше своего
    порога, по-прежнему возвращается в очередь."""
    task_id = enqueue("transcribe", {"audio_paths": []}, chat_id=1, db_path=db_path)
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-70 minutes') WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )

    assert recover_stuck_tasks(db_path=db_path) == 1
    assert query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]["status"] == "pending"


async def test_stuck_generate_konspekt_recovered_faster_than_default(db_path):
    """Конспект — один вызов LLM (живой замер 19с). На общем пороге в 15
    минут зависший вызов держал бы задачу вчетверо дольше нужного."""
    task_id = enqueue("generate_konspekt", {"transcript_id": "x"}, chat_id=1, db_path=db_path)
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-8 minutes') WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )

    assert recover_stuck_tasks(db_path=db_path) == 1
    assert query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]["status"] == "pending"


def test_every_task_type_has_its_own_stuck_threshold():
    """М7.3 ловушка: порог на тип, а не один на всё. Ни один тип задачи из
    схемы не должен молча падать на общий запасной порог — этот тест
    поймает следующий добавленный тип, если про порог для него забудут."""
    import re

    schema = (PROJECT_ROOT / "storage" / "schema.sql").read_text(encoding="utf-8")
    match = re.search(r"type TEXT[^\n]*CHECK\(type IN \(([^)]*)\)\)", schema)
    assert match, "не нашёл CHECK по tasks.type в схеме — тест устарел, поправить"
    types_in_schema = set(re.findall(r"'([a-z_]+)'", match.group(1)))

    missing = types_in_schema - set(STUCK_PROCESSING_MINUTES_BY_TYPE)
    assert not missing, f"нет своего порога зависания для типов задач: {sorted(missing)}"


# =====================================================================
# Аудит этапа 2, находка 8 — восстановление зависшей задачи расходует
# попытку, иначе обработчик не отличит её от первого запуска
# =====================================================================


async def test_recover_stuck_tasks_counts_the_lost_attempt(db_path):
    """Зависшая попытка — израсходованная попытка: работа делалась и
    пропала. Без инкремента обработчик транскрипции получал retries=0,
    считал заход первым и шёл расшифровывать по файлам, которые прошлая
    попытка уже удалила."""
    task_id = enqueue("generate_ksp", {}, chat_id=1, db_path=db_path)
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-30 minutes') WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )

    assert recover_stuck_tasks(db_path=db_path) == 1

    row = query("SELECT status, retries FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]
    assert row["status"] == "pending"
    assert row["retries"] == 1


async def test_recover_stuck_tasks_counts_attempt_with_explicit_override(db_path):
    """Тот же учёт в ветке с явным stuck_minutes — она отдельная, и про
    неё легко забыть."""
    task_id = enqueue("generate_ksp", {}, chat_id=1, db_path=db_path)
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-5 minutes') WHERE id = ?",
        (task_id,),
        db_path=db_path,
    )

    assert recover_stuck_tasks(db_path=db_path, stuck_minutes=1) == 1
    assert query("SELECT retries FROM tasks WHERE id = ?", (task_id,), db_path=db_path)[0]["retries"] == 1
