"""Проверки честного сбора значений для быстрого пути Ф1."""

import json

from core.db import execute, init_db
from core.generation_defaults import collect


def test_collect_uses_only_ktp_and_previous_generation(tmp_path):
    db_path = tmp_path / "app.db"
    init_db(db_path)
    teacher_id = execute(
        "INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
        ("Тест", "физика", 1), db_path=db_path,
    )
    execute(
        "INSERT INTO curriculum_objectives (code, grade, description) VALUES (?, ?, ?)",
        ("10.2.2.1", 10, "Тестовая цель"), db_path=db_path,
    )
    execute(
        "INSERT INTO ktp_entries (teacher_id, section, topic, objective_code) VALUES (?, ?, ?, ?)",
        (teacher_id, "Механика", "Закон Ньютона", "10.2.2.1"), db_path=db_path,
    )
    execute(
        "INSERT INTO templates (id, name, structure_json) VALUES (?, ?, ?)",
        (7, "Тестовый", "{}"), db_path=db_path,
    )
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, template_id, content_json) VALUES (?, ?, ?, ?)",
        ("g1", teacher_id, 7, json.dumps({"klass": "10А"})), db_path=db_path,
    )

    result = collect(teacher_id, "  закон   ньютона ", db_path=db_path)

    assert result["razdel"] == "Механика"
    assert result["objective_code"] == "10.2.2.1"
    assert result["klass"] == "10А"
    assert result["template_id"] == 7
    assert result["sources"] == {
        "razdel": "КТП", "objective_code": "КТП",
        "template_id": "прошлая генерация", "klass": "прошлая генерация",
    }


def test_collect_does_not_invent_missing_ktp_fields(tmp_path):
    db_path = tmp_path / "app.db"
    init_db(db_path)
    teacher_id = execute(
        "INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
        ("Тест", "физика", 2), db_path=db_path,
    )

    result = collect(teacher_id, "Новая тема", db_path=db_path)

    assert result == {"topic": "Новая тема", "sources": {}}
