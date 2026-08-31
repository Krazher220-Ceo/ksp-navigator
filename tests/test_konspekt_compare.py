"""
tests/test_konspekt_compare.py — тесты core/konspekt_compare.py (блок У4).
"""

import pytest

from core.konspekt_compare import KonspektCompareError, compare_notebook_to_transcript


class _ScriptedLLMClient:
    """Тот же приём, что в tests/test_konspekt_generator.py — отдаёт
    заранее заданные ответы по очереди, запоминает вызовы для проверки
    промпта."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete_json(self, system, user, schema, max_retries=3):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise AssertionError("LLM вызван больше раз, чем задано сценарием теста")
        return self.responses.pop(0)


async def test_returns_missing_items_list():
    client = _ScriptedLLMClient([{"missing_items": ["пропущен вывод формулы", "нет домашнего задания"]}])
    result = await compare_notebook_to_transcript("расшифровка урока...", "конспект ученика...", llm_client=client)
    assert result == ["пропущен вывод формулы", "нет домашнего задания"]


async def test_empty_missing_items_is_not_an_error():
    """Пустой список — валидный, честный ответ (конспект полный), не
    повод бросать ошибку."""
    client = _ScriptedLLMClient([{"missing_items": []}])
    result = await compare_notebook_to_transcript("расшифровка", "конспект", llm_client=client)
    assert result == []


async def test_missing_field_in_response_is_an_error_not_empty_list():
    """2.7: недостающее поле — ошибка ответа, а не молчаливая подстановка
    пустого списка (что выглядело бы неотличимо от "пропусков нет")."""
    client = _ScriptedLLMClient([{}])
    with pytest.raises(KonspektCompareError):
        await compare_notebook_to_transcript("расшифровка", "конспект", llm_client=client)


async def test_non_list_missing_items_is_an_error():
    client = _ScriptedLLMClient([{"missing_items": "не список"}])
    with pytest.raises(KonspektCompareError):
        await compare_notebook_to_transcript("расшифровка", "конспект", llm_client=client)


async def test_prompt_contains_both_texts_and_forbids_full_transcript_dump():
    client = _ScriptedLLMClient([{"missing_items": []}])
    await compare_notebook_to_transcript(
        "УНИКАЛЬНЫЙ_ТЕКСТ_РАСШИФРОВКИ", "УНИКАЛЬНЫЙ_ТЕКСТ_КОНСПЕКТА", llm_client=client
    )
    call = client.calls[0]
    assert "УНИКАЛЬНЫЙ_ТЕКСТ_РАСШИФРОВКИ" in call["user"]
    assert "УНИКАЛЬНЫЙ_ТЕКСТ_КОНСПЕКТА" in call["user"]
    # У4, дословно: не выдаёт и не пересказывает расшифровку целиком —
    # инструкция об этом обязана быть в системном промпте, не только в
    # докстринге модуля.
    assert "не пересказывай" in call["system"].lower() or "целиком" in call["system"].lower()


async def test_response_schema_has_no_grade_or_score_field():
    """У4, дословно: "не ставится оценка" — гарантия на уровне схемы
    ответа, а не только пожелания в тексте промпта: модели физически
    негде вернуть балл или оценку, схема их не содержит."""
    from core.konspekt_compare import RESPONSE_SCHEMA

    assert set(RESPONSE_SCHEMA["properties"].keys()) == {"missing_items"}
