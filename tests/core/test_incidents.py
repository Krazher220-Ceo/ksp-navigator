"""
tests/core/test_incidents.py — тесты core/incidents.py (блок М7.1).
"""

from pathlib import Path

import pytest

from core.db import execute, init_db, query
from core.incidents import (
    format_incident_message,
    get_unnotified_resolved_incidents,
    mark_notified,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


def _insert_incident(db_path, started, ended=None, reason="dns_fail", notified=0) -> int:
    return execute(
        "INSERT INTO incidents (started_at, ended_at, reason, notified) VALUES (?, ?, ?, ?)",
        (started, ended, reason, notified),
        db_path=db_path,
    )


# =====================================================================
# get_unnotified_resolved_incidents — только завершённые и неотправленные
# =====================================================================


def test_open_incident_not_returned(db_path):
    """Инцидент ещё идёт (ended_at NULL) — рано слать, watchdog его ещё
    не закрыл."""
    _insert_incident(db_path, "2026-08-25 10:00:00", ended=None)
    assert get_unnotified_resolved_incidents(db_path=db_path) == []


def test_already_notified_incident_not_returned_again(db_path):
    _insert_incident(db_path, "2026-08-25 10:00:00", ended="2026-08-25 11:57:00", notified=1)
    assert get_unnotified_resolved_incidents(db_path=db_path) == []


def test_resolved_unnotified_incident_returned(db_path):
    _insert_incident(db_path, "2026-08-25 10:00:00", ended="2026-08-25 11:57:00", reason="dns_fail")
    incidents = get_unnotified_resolved_incidents(db_path=db_path)
    assert len(incidents) == 1
    assert incidents[0]["reason"] == "dns_fail"


def test_multiple_incidents_returned_in_chronological_order(db_path):
    """Если бот перезапускался несколько раз подряд и накопилось
    несколько неотправленных инцидентов — про каждый отдельно, не только
    про последний."""
    _insert_incident(db_path, "2026-08-26 11:28:00", ended="2026-08-26 11:34:00", reason="tcp_fail")
    _insert_incident(db_path, "2026-08-25 10:00:00", ended="2026-08-25 11:57:00", reason="dns_fail")
    incidents = get_unnotified_resolved_incidents(db_path=db_path)
    assert [i["reason"] for i in incidents] == ["dns_fail", "tcp_fail"]  # по started_at, не по id


# =====================================================================
# mark_notified — идемпотентно снимает с очереди на отправку
# =====================================================================


def test_mark_notified_removes_from_unnotified_list(db_path):
    incident_id = _insert_incident(db_path, "2026-08-25 10:00:00", ended="2026-08-25 11:57:00")
    mark_notified(incident_id, db_path=db_path)
    assert get_unnotified_resolved_incidents(db_path=db_path) == []


# =====================================================================
# format_incident_message — одно сообщение на инцидент (ловушка М7.1)
# =====================================================================


def test_format_incident_message_contains_times_and_human_reason():
    incident = {
        "id": 1,
        "started_at": "2026-08-25T10:00:00",
        "ended_at": "2026-08-25T11:57:00",
        "reason": "dns_fail",
    }
    text = format_incident_message(incident)
    assert "10:00" in text
    assert "11:57" in text
    assert "DNS" in text
    assert "dns_fail" not in text  # человеческий текст, не техническое имя причины
