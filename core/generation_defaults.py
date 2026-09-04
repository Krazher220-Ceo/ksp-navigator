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
        "SELECT id, topic, section, objective_code FROM ktp_entries WHERE teacher_id = ?",
        (teacher_id,), db_path=db_path,
    )
    for row in ktp_rows:
        if _normalized(row["topic"] or "") == normalized_topic:
            # id найденной строки КТП — не удобство, а то, чем дашборд
            # считает покрытие программы: core/dashboard.py берёт
            # covered из generated_ksp.ktp_entry_id IS NOT NULL. Пока
            # бот его не проставлял, «покрыто» оставалось нулём даже у
            # учителя, собравшего КСП по каждой теме своего КТП, а
            # «ближайшие уроки без КСП» показывали уже закрытые уроки.
            result["ktp_entry_id"] = row["id"]
            result["sources"]["ktp_entry_id"] = "КТП"
            if row["section"]:
                result["razdel"] = row["section"]
                result["sources"]["razdel"] = "КТП"
            if row["objective_code"]:
                result["objective_code"] = row["objective_code"]
                result["sources"]["objective_code"] = "КТП"
            break

    # Только ORDER BY created_at: псевдоколонки rowid в Postgres нет, и
    # запрос с ней падал в проде 400 Bad Request на первом же шаге
    # /generate (Находка 3 AUDIT.md, грабля 2.12). Тай-брейкер по rowid
    # был нужен лишь при совпадении времени до секунды.
    generated_rows = query(
        "SELECT template_id, content_json FROM generated_ksp WHERE teacher_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
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
