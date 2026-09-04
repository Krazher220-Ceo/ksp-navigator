"""
tests/core/test_dashboard.py — тесты core/dashboard.py (блок М5.1).

Синтетическая база в tmp_path через реальную storage/schema.sql — тем же
способом, что и остальные тесты проекта (см. tests/bot/test_bot_handlers.py,
isolated_env). Реального Telegram/LLM здесь нет вообще — только SQL.
"""

from pathlib import Path

import pytest

from core.dashboard import _parse_planned_date, collect
from core.db import execute, init_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def _create_teacher(db_path, telegram_user_id=555, name="Тестов Т.") -> int:
    return execute(
        "INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
        (name, "физика", telegram_user_id),
        db_path=db_path,
    )


# =====================================================================
# collect() — учитель без профиля (ловушка 2: не ошибка)
# =====================================================================


def test_collect_with_none_teacher_id_returns_no_profile_flag(db_path):
    result = collect(None, db_path=db_path)
    assert result["has_profile"] is False
    assert result["queue"] == {"pending": 0, "processing": 0, "failed_7d": 0}
    assert result["generated_ksp"] == {"total": 0, "last_7d": 0, "last_30d": 0}


# =====================================================================
# collect() — пустая база (учитель есть, данных ещё нет)
# =====================================================================


def test_collect_empty_teacher_does_not_crash(db_path):
    teacher_id = _create_teacher(db_path)
    result = collect(teacher_id, db_path=db_path)
    assert result["has_profile"] is True
    assert result["queue"] == {"pending": 0, "processing": 0, "failed_7d": 0}
    assert result["generated_ksp"] == {"total": 0, "last_7d": 0, "last_30d": 0}
    assert result["ktp_coverage"] == {"covered": 0, "not_covered": 0}
    assert result["upcoming_lessons_without_ksp"] == []
    assert result["unparsed_planned_dates"] == 0
    assert result["style_profile"] == {"exists": False, "samples_count": None}


# =====================================================================
# Очередь — считается по telegram_chat_id, не по teacher_id
# =====================================================================


def test_collect_queue_counts_by_chat_id_not_teacher_id(db_path):
    teacher_id = _create_teacher(db_path, telegram_user_id=777)
    execute(
        "INSERT INTO tasks (id, type, status, telegram_chat_id) VALUES ('t1', 'generate_ksp', 'pending', 777)",
        db_path=db_path,
    )
    execute(
        "INSERT INTO tasks (id, type, status, telegram_chat_id) VALUES ('t2', 'generate_ksp', 'processing', 777)",
        db_path=db_path,
    )
    # чужая задача с другим chat_id не должна попасть в счёт
    execute(
        "INSERT INTO tasks (id, type, status, telegram_chat_id) VALUES ('t3', 'generate_ksp', 'pending', 999)",
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["queue"]["pending"] == 1
    assert result["queue"]["processing"] == 1


# =====================================================================
# Сгенерированные КСП — total/7д/30д
# =====================================================================


def test_collect_generated_ksp_counts_total(db_path):
    teacher_id = _create_teacher(db_path)
    for i in range(3):
        execute(
            "INSERT INTO generated_ksp (id, teacher_id, content_json) VALUES (?, ?, '{}')",
            (f"ksp{i}", teacher_id),
            db_path=db_path,
        )
    result = collect(teacher_id, db_path=db_path)
    assert result["generated_ksp"]["total"] == 3
    assert result["generated_ksp"]["last_7d"] == 3  # created_at = CURRENT_TIMESTAMP, только что


# =====================================================================
# Покрытие КТП: covered vs not_covered
# =====================================================================


def test_collect_ktp_coverage_splits_covered_and_not_covered(db_path):
    teacher_id = _create_teacher(db_path)
    entry_covered = execute(
        "INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, 'Тема 1', '2026-09-01')",
        (teacher_id,),
        db_path=db_path,
    )
    execute(
        "INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, 'Тема 2', '2026-09-08')",
        (teacher_id,),
        db_path=db_path,
    )
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, ktp_entry_id, content_json) VALUES ('ksp1', ?, ?, '{}')",
        (teacher_id, entry_covered),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["ktp_coverage"] == {"covered": 1, "not_covered": 1}


# =====================================================================
# Кривые даты — отдельный счётчик, не сегодняшнее число (ловушка 1)
# =====================================================================


def test_unparseable_planned_date_counted_separately_not_guessed(db_path):
    teacher_id = _create_teacher(db_path)
    execute(
        "INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, 'Тема без даты', 'какая-то ерунда')",
        (teacher_id,),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["unparsed_planned_dates"] == 1
    assert result["upcoming_lessons_without_ksp"] == []
    assert result["ktp_coverage"]["not_covered"] == 1


def test_planned_date_range_format_uses_first_day():
    """Реальный формат из storage/app.db: "01-08.09.23" (диапазон дней
    внутри месяца) — берём первый день диапазона."""
    from datetime import date

    assert _parse_planned_date("01-08.09.23") == date(2023, 9, 1)


def test_planned_date_iso_format():
    from datetime import date

    assert _parse_planned_date("2026-09-01") == date(2026, 9, 1)


def test_planned_date_single_dot_format():
    from datetime import date

    assert _parse_planned_date("01.09.2026") == date(2026, 9, 1)


def test_planned_date_none_or_empty_returns_none():
    assert _parse_planned_date(None) is None
    assert _parse_planned_date("") is None
    assert _parse_planned_date("   ") is None


def test_planned_date_garbage_returns_none():
    assert _parse_planned_date("совсем не дата") is None


# =====================================================================
# Ближайшие уроки без КСП — только будущие, отсортированы, с лимитом
# =====================================================================


def test_upcoming_lessons_only_future_dates_sorted_and_limited(db_path, monkeypatch):
    import core.dashboard as dashboard_module
    from datetime import date as real_date

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 9, 1)

    monkeypatch.setattr(dashboard_module, "date", FixedDate)

    teacher_id = _create_teacher(db_path)
    # два в прошлом (не должны попасть), три в будущем в перепутанном порядке
    dates = ["2026-08-01", "2026-08-15", "2026-09-10", "2026-09-05", "2026-09-20"]
    for i, d in enumerate(dates):
        execute(
            "INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, ?, ?)",
            (teacher_id, f"Тема {i}", d),
            db_path=db_path,
        )
    result = collect(teacher_id, db_path=db_path)
    upcoming_dates = [item["planned_date"] for item in result["upcoming_lessons_without_ksp"]]
    assert upcoming_dates == ["2026-09-05", "2026-09-10", "2026-09-20"]


def test_upcoming_lessons_limited_to_five(db_path, monkeypatch):
    import core.dashboard as dashboard_module
    from datetime import date as real_date

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 1, 1)

    monkeypatch.setattr(dashboard_module, "date", FixedDate)

    teacher_id = _create_teacher(db_path)
    for i in range(8):
        execute(
            "INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, ?, ?)",
            (teacher_id, f"Тема {i}", f"2026-06-{i+1:02d}"),
            db_path=db_path,
        )
    result = collect(teacher_id, db_path=db_path)
    assert len(result["upcoming_lessons_without_ksp"]) == 5


# =====================================================================
# Профиль стиля
# =====================================================================


def test_style_profile_reflects_existing_profile(db_path):
    teacher_id = _create_teacher(db_path)
    execute(
        "INSERT INTO style_profiles (teacher_id, raw_samples_count) VALUES (?, 3)",
        (teacher_id,),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["style_profile"] == {"exists": True, "samples_count": 3}


# =====================================================================
# М6.3 — остаток дневного лимита в дашборде
# =====================================================================


def test_usage_today_reflects_recorded_generations(db_path):
    from core.limits import record_usage

    teacher_id = _create_teacher(db_path, telegram_user_id=888)
    record_usage(888, "generate_ksp", count_delta=3, db_path=db_path)
    record_usage(888, "generate_ktp", count_delta=1, db_path=db_path)

    result = collect(teacher_id, db_path=db_path)
    assert result["usage_today"] == {
        "generate_ksp": 3,
        "generate_ktp": 1,
        "generate_ksp_limit": 5,
        "generate_ktp_limit": 1,
    }


def test_usage_today_zero_when_nothing_recorded(db_path):
    teacher_id = _create_teacher(db_path)
    result = collect(teacher_id, db_path=db_path)
    assert result["usage_today"]["generate_ksp"] == 0
    assert result["usage_today"]["generate_ksp_limit"] == 5


# =====================================================================
# М7.4 — аптайм в дашборде
# =====================================================================


def test_uptime_empty_when_no_incidents(db_path):
    teacher_id = _create_teacher(db_path)
    result = collect(teacher_id, db_path=db_path)
    assert result["uptime"]["incidents_7d"] == 0
    assert result["uptime"]["downtime_seconds_7d"] == 0
    assert result["uptime"]["last_incident"] is None
    assert result["uptime"]["measured_since"] == "2026-08-26"


def test_uptime_present_even_without_teacher_profile(db_path):
    """М7.4: живучесть не про конкретного учителя, показывается и без
    профиля (в отличие от остальных секций дашборда)."""
    result = collect(None, db_path=db_path)
    assert "uptime" in result
    assert result["uptime"]["incidents_7d"] == 0


def test_uptime_counts_closed_incident_within_7_days(db_path):
    from datetime import datetime, timedelta

    teacher_id = _create_teacher(db_path)
    start = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ")
    end = (datetime.now() - timedelta(hours=1, minutes=50)).isoformat(sep=" ")
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (?, ?, 'dns_fail')",
        (start, end),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["uptime"]["incidents_7d"] == 1
    # 10 минут = 600 секунд простоя
    assert result["uptime"]["downtime_seconds_7d"] == 600
    assert result["uptime"]["last_incident"]["reason"] == "dns_fail"


def test_uptime_ignores_incidents_older_than_7_days(db_path):
    from datetime import datetime, timedelta

    teacher_id = _create_teacher(db_path)
    old_start = (datetime.now() - timedelta(days=10)).isoformat(sep=" ")
    old_end = (datetime.now() - timedelta(days=10) + timedelta(minutes=5)).isoformat(sep=" ")
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (?, ?, 'dns_fail')",
        (old_start, old_end),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["uptime"]["incidents_7d"] == 0
    assert result["uptime"]["downtime_seconds_7d"] == 0


def test_uptime_still_open_incident_not_counted_in_downtime(db_path):
    """Инцидент, который ещё идёт (ended_at NULL) — его длительность
    неизвестна, не считаем в downtime_seconds_7d, но last_incident его
    показывает (с ended_at=None, вызывающий код должен это увидеть)."""
    teacher_id = _create_teacher(db_path)
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (datetime('now'), NULL, 'tcp_fail')",
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["uptime"]["downtime_seconds_7d"] == 0
    assert result["uptime"]["last_incident"]["ended_at"] is None
    assert result["uptime"]["last_incident"]["reason"] == "tcp_fail"


def test_uptime_last_incident_is_most_recent_by_start(db_path):
    from datetime import datetime, timedelta

    teacher_id = _create_teacher(db_path)
    older = (datetime.now() - timedelta(days=1)).isoformat(sep=" ")
    newer = (datetime.now() - timedelta(hours=1)).isoformat(sep=" ")
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (?, ?, 'dns_fail')",
        (older, older),
        db_path=db_path,
    )
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (?, ?, 'tcp_fail')",
        (newer, newer),
        db_path=db_path,
    )
    result = collect(teacher_id, db_path=db_path)
    assert result["uptime"]["last_incident"]["reason"] == "tcp_fail"
