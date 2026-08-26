"""
core/dashboard.py — единый расчёт метрик дашборда (блок М5.1, PLAN_STAGE2.md).

Зачем модуль: бот (текстовая сводка, М5.2) и Mini App (третий экран, М5.3)
обязаны показывать одни и те же числа. Весь расчёт — здесь и только здесь,
оба представления читают его результат; дублировать SQL в двух местах
запрещено планом — разъедется при первой же правке.

Что осознанно не делает: не форматирует вывод для конкретного канала —
текст для бота собирает bot/handlers.py, JSON для Mini App отдаёт
web/api.py. Не считает конспекты и транскрипты (появятся после блоков
К3/К4 этого же плана, добавятся в этот модуль там же). Не пытается
угадать формат `ktp_entries.planned_date` за пределами нескольких
распознаваемых видов — на реальных данных это текст вроде
"01-08.09.23" (диапазон дней внутри месяца), и большая часть строк
вообще не парсится в дату; честно относить их к "не распознано", а не
подставлять сегодняшнее число (ловушка плана).

На что опирается: core.db (прямые SQL-запросы, без ORM — тот же принцип,
что и во всём проекте, см. core/db.py)."""

import re
from datetime import date, datetime, timedelta

from core.db import query

# Сколько ближайших уроков без КСП показывать — весь список бесполезен в
# текстовой сводке бота, а самое полезное — именно "что дальше".
UPCOMING_LESSONS_LIMIT = 5


def _parse_planned_date(raw: str | None) -> date | None:
    """Возвращает первый день из значения planned_date, если формат
    распознан, иначе None — вызывающий код обязан отличать "дата не
    распозналась" от "урок сегодня", не путать одно с другим."""
    if not raw:
        return None
    text = raw.strip()

    # ISO: 2026-09-01
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    # Диапазон дней внутри месяца, как в реальных КТП: "01-08.09.23" —
    # берём первый день диапазона (начало недели/периода).
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if m:
        return _build_date(m[1], m[3], m[4])

    # Одна дата: "01.09.2026" или "01.09.26"
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if m:
        return _build_date(m[1], m[2], m[3])

    return None


def _build_date(day: str, month: str, year: str) -> date | None:
    year_int = int(year)
    if year_int < 100:
        # Двузначный год — школьные КТП всегда про текущее тысячелетие,
        # 2000 + YY безопасно для обозримого будущего этого проекта.
        year_int += 2000
    try:
        return date(year_int, int(month), int(day))
    except ValueError:
        return None


def _resolve_telegram_chat_id(teacher_id: int, db_path=None) -> int | None:
    """tasks.telegram_chat_id — не teacher_id (bot/handlers.py кладёт в
    очередь по message.chat.id, а не по teacher_id) — для приватного чата
    с ботом это то же число, что telegram_user_id учителя."""
    rows = query("SELECT telegram_user_id FROM teachers WHERE id = ?", (teacher_id,), db_path=db_path)
    if not rows:
        return None
    return rows[0]["telegram_user_id"]


def collect(teacher_id: int | None, db_path=None) -> dict:
    """Единственная точка расчёта дашборда. teacher_id=None — учитель без
    профиля (нормальное состояние, не ошибка, М5.1 ловушка 2): возвращает
    минимальный набор без чисел, привязанных к учителю, и флаг
    has_profile=False, по которому вызывающий код должен предложить
    /teacher, а не молча показать нули."""
    if teacher_id is None:
        return {
            "has_profile": False,
            "queue": {"pending": 0, "processing": 0, "failed_7d": 0},
            "generated_ksp": {"total": 0, "last_7d": 0, "last_30d": 0},
            "ktp_coverage": {"covered": 0, "not_covered": 0},
            "upcoming_lessons_without_ksp": [],
            "unparsed_planned_dates": 0,
            "style_profile": {"exists": False, "samples_count": None},
            "usage_today": {"generate_ksp": 0, "generate_ktp": 0, "generate_ksp_limit": 0, "generate_ktp_limit": 0},
        }

    now = datetime.now()
    since_7d = (now - timedelta(days=7)).isoformat(sep=" ")
    since_30d = (now - timedelta(days=30)).isoformat(sep=" ")

    queue = _collect_queue(teacher_id, since_7d, db_path=db_path)
    generated_ksp = _collect_generated_ksp(teacher_id, since_7d, since_30d, db_path=db_path)
    ktp_coverage, upcoming, unparsed = _collect_ktp_coverage(teacher_id, db_path=db_path)
    style_profile = _collect_style_profile(teacher_id, db_path=db_path)
    usage_today = _collect_usage_today(teacher_id, db_path=db_path)

    return {
        "has_profile": True,
        "queue": queue,
        "generated_ksp": generated_ksp,
        "ktp_coverage": ktp_coverage,
        "upcoming_lessons_without_ksp": upcoming,
        "unparsed_planned_dates": unparsed,
        "style_profile": style_profile,
        "usage_today": usage_today,
    }


def _collect_usage_today(teacher_id: int, db_path=None) -> dict:
    """М6.3: остаток дневного лимита для дашборда. Ключ usage_daily —
    telegram_user_id, не teacher_id (core/limits.py, ловушка "что такое
    аккаунт") — резолвится тем же способом, что и очередь задач."""
    # Импорт внутри функции, не на уровне модуля: core.limits не должен
    # быть обязательной зависимостью для тех, кто использует только
    # collect() без блока М6 (например, будущие тесты этого модуля,
    # написанные раньше М6 — не роняем их лишним импортом).
    from core.limits import DAILY_COUNT_LIMITS, get_usage_today

    chat_id = _resolve_telegram_chat_id(teacher_id, db_path=db_path)
    if chat_id is None:
        return {"generate_ksp": 0, "generate_ktp": 0, "generate_ksp_limit": 0, "generate_ktp_limit": 0}

    usage = get_usage_today(chat_id, db_path=db_path)
    counts = usage["counts"]
    return {
        "generate_ksp": counts.get("generate_ksp", 0),
        "generate_ktp": counts.get("generate_ktp", 0),
        "generate_ksp_limit": DAILY_COUNT_LIMITS["generate_ksp"],
        "generate_ktp_limit": DAILY_COUNT_LIMITS["generate_ktp"],
    }


def _collect_queue(teacher_id: int, since_7d: str, db_path=None) -> dict:
    chat_id = _resolve_telegram_chat_id(teacher_id, db_path=db_path)
    if chat_id is None:
        return {"pending": 0, "processing": 0, "failed_7d": 0}

    rows = query(
        "SELECT status, COUNT(*) AS n FROM tasks "
        "WHERE telegram_chat_id = ? AND status IN ('pending', 'processing') "
        "GROUP BY status",
        (chat_id,),
        db_path=db_path,
    )
    counts = {row["status"]: row["n"] for row in rows}

    failed_row = query(
        "SELECT COUNT(*) AS n FROM tasks "
        "WHERE telegram_chat_id = ? AND status = 'failed' AND created_at >= ?",
        (chat_id, since_7d),
        db_path=db_path,
    )
    return {
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "failed_7d": failed_row[0]["n"],
    }


def _collect_generated_ksp(teacher_id: int, since_7d: str, since_30d: str, db_path=None) -> dict:
    total = query(
        "SELECT COUNT(*) AS n FROM generated_ksp WHERE teacher_id = ?", (teacher_id,), db_path=db_path
    )[0]["n"]
    last_7d = query(
        "SELECT COUNT(*) AS n FROM generated_ksp WHERE teacher_id = ? AND created_at >= ?",
        (teacher_id, since_7d),
        db_path=db_path,
    )[0]["n"]
    last_30d = query(
        "SELECT COUNT(*) AS n FROM generated_ksp WHERE teacher_id = ? AND created_at >= ?",
        (teacher_id, since_30d),
        db_path=db_path,
    )[0]["n"]
    return {"total": total, "last_7d": last_7d, "last_30d": last_30d}


def _collect_ktp_coverage(teacher_id: int, db_path=None) -> tuple[dict, list[dict], int]:
    entries = query(
        "SELECT id, topic, planned_date FROM ktp_entries WHERE teacher_id = ? ORDER BY id",
        (teacher_id,),
        db_path=db_path,
    )
    covered_ids = {
        row["ktp_entry_id"]
        for row in query(
            "SELECT DISTINCT ktp_entry_id FROM generated_ksp "
            "WHERE teacher_id = ? AND ktp_entry_id IS NOT NULL",
            (teacher_id,),
            db_path=db_path,
        )
    }

    covered = 0
    not_covered = 0
    unparsed = 0
    today = date.today()
    upcoming: list[dict] = []

    for entry in entries:
        is_covered = entry["id"] in covered_ids
        if is_covered:
            covered += 1
            continue
        not_covered += 1

        parsed = _parse_planned_date(entry["planned_date"])
        if parsed is None:
            unparsed += 1
            continue
        if parsed >= today:
            upcoming.append({"topic": entry["topic"], "planned_date": parsed.isoformat()})

    upcoming.sort(key=lambda item: item["planned_date"])
    return (
        {"covered": covered, "not_covered": not_covered},
        upcoming[:UPCOMING_LESSONS_LIMIT],
        unparsed,
    )


def _collect_style_profile(teacher_id: int, db_path=None) -> dict:
    rows = query(
        "SELECT raw_samples_count FROM style_profiles WHERE teacher_id = ? ORDER BY updated_at DESC LIMIT 1",
        (teacher_id,),
        db_path=db_path,
    )
    if not rows:
        return {"exists": False, "samples_count": None}
    return {"exists": True, "samples_count": rows[0]["raw_samples_count"]}
