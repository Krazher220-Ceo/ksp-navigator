"""Извлекает известные значения для быстрого пути генерации КСП.

Не хранит отдельные «последние настройки» и не угадывает отсутствующие
данные. Читает только КТП и уже созданные КСП, на которые опирается бот.
"""

import json

from core.db import query


def _normalized(value: str) -> str:
    return " ".join(value.strip().lower().split())


def collect(teacher_id: int, topic: str, db_path=None) -> dict:
    """Возвращает только подтверждённые базой значения и их источники."""
    result: dict = {"topic": topic, "sources": {}}
    normalized_topic = _normalized(topic)

    ktp_rows = query(
        "SELECT topic, section, objective_code FROM ktp_entries WHERE teacher_id = ?",
        (teacher_id,), db_path=db_path,
    )
    for row in ktp_rows:
        if _normalized(row["topic"] or "") == normalized_topic:
            if row["section"]:
                result["razdel"] = row["section"]
                result["sources"]["razdel"] = "КТП"
            if row["objective_code"]:
                result["objective_code"] = row["objective_code"]
                result["sources"]["objective_code"] = "КТП"
            break

    generated_rows = query(
        "SELECT template_id, content_json FROM generated_ksp WHERE teacher_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (teacher_id,), db_path=db_path,
    )
    if generated_rows:
        row = generated_rows[0]
        result["template_id"] = row["template_id"]
        result["sources"]["template_id"] = "прошлая генерация"
        try:
            content = json.loads(row["content_json"] or "{}")
        except json.JSONDecodeError:
            content = {}
        if content.get("klass"):
            result["klass"] = content["klass"]
            result["sources"]["klass"] = "прошлая генерация"

    return result
