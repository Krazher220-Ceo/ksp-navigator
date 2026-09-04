"""
tests/core/test_konspekt_generator.py — тесты core/konspekt_generator.py (блок К4).
"""

from pathlib import Path

import pytest

from core.db import execute, init_db
from core.konspekt_generator import (
    KonspektGenerationError,
    _validate_konspekt_content,
    build_prompt,
    check_coverage,
    generate_konspekt,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

VALID_CONTENT = {
    "tema": "Закон сохранения импульса",
    "celi": ["Ввести понятие импульса", "Сформулировать закон сохранения"],
    "glavnoe": ["Импульс тела — произведение массы на скорость", "В замкнутой системе импульс сохраняется"],
    "formuly": [{"formula": "p = m*v", "znachenie": "p — импульс, m — масса, v — скорость"}],
    "primery": ["Столкновение двух шаров разной массы"],
    "terminy": [{"termin": "импульс", "opredelenie": "векторная величина, равная произведению массы на скорость"}],
    "voprosy_dlya_samoproverki": ["Что такое импульс?", "В каких условиях он сохраняется?"],
    "domashnee_zadanie": "§15, задачи 1-3",
}

NESTED_CONTENT = {
    "opornye_repliki": ["Здравствуйте, начинаем урок.", "Откройте тетради и решите задачу.", "На сегодня всё, до свидания."],
    "konspekt_uchenika": VALID_CONTENT,
}


def _nested(student_content: dict) -> dict:
    return {"opornye_repliki": list(NESTED_CONTENT["opornye_repliki"]), "konspekt_uchenika": student_content}


class _ScriptedLLMClient:
    """Тот же приём, что в tests/core/test_generator.py — отдаёт заранее
    заданные ответы по очереди, запоминает вызовы для проверки промпта."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete_json(self, system, user, schema, max_retries=3):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise AssertionError("LLM вызван больше раз, чем задано сценарием теста")
        return self.responses.pop(0)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(db_path=path, schema_path=REAL_SCHEMA_PATH)
    return path


# =====================================================================
# build_prompt — транскрипт обязателен, тема/код цели — подсказка
# =====================================================================


def test_build_prompt_contains_transcript():
    prompt = build_prompt("Сегодня мы изучаем закон сохранения импульса...")
    assert "закон сохранения импульса" in prompt


def test_build_prompt_includes_topic_hint_when_given():
    prompt = build_prompt("текст урока", topic="Закон Ома")
    assert "Закон Ома" in prompt


def test_build_prompt_without_topic_has_no_hint():
    prompt = build_prompt("текст урока")
    assert "Подсказка" not in prompt


def test_build_prompt_includes_objective_description_from_db(db_path):
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', 'применять законы сохранения', 'применение')",
        db_path=db_path,
    )
    prompt = build_prompt("текст урока", objective_code="10.1.4.1", db_path=db_path)
    assert "применять законы сохранения" in prompt


def test_build_prompt_transcript_is_placed_after_hints():
    """М4.2-совместимая компоновка: стабильная часть (заголовок) —
    впереди, расшифровка (самая переменная часть) — в конце."""
    prompt = build_prompt("УНИКАЛЬНЫЙ_ТЕКСТ_РАСШИФРОВКИ", topic="Тема урока")
    header_pos = prompt.index("ЗАДАЧА:")
    transcript_pos = prompt.index("УНИКАЛЬНЫЙ_ТЕКСТ_РАСШИФРОВКИ")
    assert header_pos < transcript_pos


# =====================================================================
# generate_konspekt — сценарии по образцу КГ core.ksp_generator
# =====================================================================


async def test_generate_konspekt_valid_response_passes_without_repair():
    fake = _ScriptedLLMClient([NESTED_CONTENT])
    result = await generate_konspekt("текст расшифровки урока", llm_client=fake)
    assert result == NESTED_CONTENT
    assert len(fake.calls) == 1


def test_schema_requires_two_top_level_sections_and_prompt_limits_replics():
    from core.konspekt_generator import KONSPEKT_RESPONSE_SCHEMA, SYSTEM_PROMPT

    assert set(KONSPEKT_RESPONSE_SCHEMA["required"]) == {"opornye_repliki", "konspekt_uchenika"}
    assert "одной-двух реплик" in SYSTEM_PROMPT


def test_validate_rejects_too_many_opornye_repliki():
    from core.konspekt_generator import _validate_konspekt_content

    content = {**NESTED_CONTENT, "opornye_repliki": [f"реплика {index}" for index in range(11)]}
    assert any("opornye_repliki" in problem for problem in _validate_konspekt_content(content))


async def test_generate_konspekt_missing_field_triggers_one_repair_then_succeeds():
    broken = _nested({k: v for k, v in VALID_CONTENT.items() if k != "glavnoe"})
    fake = _ScriptedLLMClient([broken, NESTED_CONTENT])

    result = await generate_konspekt("текст расшифровки", llm_client=fake)

    assert result == NESTED_CONTENT
    assert len(fake.calls) == 2
    assert "glavnoe" in fake.calls[1]["user"]  # причина провала попала в повторный промпт


async def test_generate_konspekt_second_failure_raises_error():
    broken = _nested({k: v for k, v in VALID_CONTENT.items() if k != "celi"})
    fake = _ScriptedLLMClient([broken, broken])

    with pytest.raises(KonspektGenerationError):
        await generate_konspekt("текст расшифровки", llm_client=fake)


async def test_generate_konspekt_empty_celi_is_accepted_not_invented():
    """Аудит этапа 2, находка 1. Учителя обычно не проговаривают цели урока
    вслух — они пишутся в плане. Честный ответ модели с пустым 'celi' обязан
    приниматься как есть, ОДНИМ вызовом: до правки он отклонялся, уходил на
    ремонт и заканчивался KonspektGenerationError, то есть честность
    наказывалась потерей всего конспекта."""
    honest = _nested({**VALID_CONTENT, "celi": []})
    fake = _ScriptedLLMClient([honest])

    result = await generate_konspekt("расшифровка без озвученных целей", llm_client=fake)

    assert result == honest
    assert result["konspekt_uchenika"]["celi"] == []  # пустое осталось пустым, ничего не дописано
    assert len(fake.calls) == 1  # ремонтного вызова не было — платить дважды не за что


async def test_validate_konspekt_content_does_not_complain_about_empty_celi():
    """Та же находка на уровне самого валидатора, без обхода через LLM."""
    problems = _validate_konspekt_content(_nested({**VALID_CONTENT, "celi": []}))
    assert problems == []


async def test_generate_konspekt_empty_glavnoe_still_rejected_after_celi_fix():
    """Граница правки находки 1: смягчили ТОЛЬКО 'celi'. Пустой 'glavnoe'
    по-прежнему невалиден — конспект без главных тезисов бессмыслен, это
    обосновано в самом коде и правкой не затронуто."""
    broken = _nested({**VALID_CONTENT, "glavnoe": [], "celi": []})
    fake = _ScriptedLLMClient([broken, NESTED_CONTENT])
    result = await generate_konspekt("текст расшифровки", llm_client=fake)
    assert result == NESTED_CONTENT
    assert len(fake.calls) == 2  # ремонт был вызван именно из-за glavnoe


async def test_generate_konspekt_empty_glavnoe_list_is_rejected():
    """Конспект без главных тезисов бессмыслен — пустой 'glavnoe'
    считается невалидным ответом, не тихо принимается."""
    broken = _nested({**VALID_CONTENT, "glavnoe": []})
    fake = _ScriptedLLMClient([broken, NESTED_CONTENT])
    result = await generate_konspekt("текст расшифровки", llm_client=fake)
    assert result == NESTED_CONTENT


async def test_generate_konspekt_passes_topic_and_objective_to_prompt():
    fake = _ScriptedLLMClient([NESTED_CONTENT])
    await generate_konspekt(
        "текст расшифровки", topic="Закон Ома", objective_code="10.1.4.1", llm_client=fake
    )
    assert "Закон Ома" in fake.calls[0]["user"]


async def test_generate_konspekt_incomplete_formula_triggers_repair():
    broken = _nested({**VALID_CONTENT, "formuly": [{"formula": "p = m*v"}]})  # нет znachenie
    fake = _ScriptedLLMClient([broken, NESTED_CONTENT])
    result = await generate_konspekt("текст расшифровки", llm_client=fake)
    assert result == NESTED_CONTENT
    assert "formuly" in fake.calls[1]["user"]


# =====================================================================
# check_coverage (К4.2) — простая сверка, НЕ аналитика этапа 3
# =====================================================================


async def test_check_coverage_empty_codes_returns_empty_without_llm_call():
    fake = _ScriptedLLMClient([])  # если бы LLM вызвался - тест упал бы на пустом списке ответов
    result = await check_coverage(VALID_CONTENT, [], llm_client=fake)
    assert result == []
    assert fake.calls == []


async def test_check_coverage_returns_llm_verdict(db_path):
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', 'применять законы сохранения', 'применение')",
        db_path=db_path,
    )
    fake_response = {
        "rezultaty": [
            {"code": "10.1.4.1", "naideno": True, "kommentariy": "формула и определение есть в конспекте"},
        ]
    }
    fake = _ScriptedLLMClient([fake_response])
    result = await check_coverage(VALID_CONTENT, ["10.1.4.1"], llm_client=fake, db_path=db_path)
    assert result == fake_response["rezultaty"]
    assert "применять законы сохранения" in fake.calls[0]["user"]
