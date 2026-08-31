"""
tests/test_konspekt_compare.py — тесты core/konspekt_compare.py (блок У4).
"""

import pytest

from core.konspekt_compare import (
    MIN_NOTEBOOK_TEXT_LENGTH,
    KonspektCompareError,
    NotebookUnreadableError,
    compare_notebook_to_transcript,
    notebook_is_unreadable,
)

# Находка 6 AUDIT.md ввела порог MIN_NOTEBOOK_TEXT_LENGTH: тетрадь короче
# него сравнению не подлежит. Тесты ниже проверяют не порог, а разбор
# ответа модели, поэтому им нужен заведомо «читаемый» текст тетради —
# раньше здесь стояли строки в одно слово, и после правки они означали
# бы совсем другое.
READABLE_NOTEBOOK = "Конспект ученика: определение скорости, формула v = s / t, пример задачи."
assert len(READABLE_NOTEBOOK) >= MIN_NOTEBOOK_TEXT_LENGTH


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
    result = await compare_notebook_to_transcript("расшифровка урока...", READABLE_NOTEBOOK, llm_client=client)
    assert result == ["пропущен вывод формулы", "нет домашнего задания"]


async def test_empty_missing_items_is_not_an_error():
    """Пустой список — валидный, честный ответ (конспект полный), не
    повод бросать ошибку."""
    client = _ScriptedLLMClient([{"missing_items": []}])
    result = await compare_notebook_to_transcript("расшифровка", READABLE_NOTEBOOK, llm_client=client)
    assert result == []


async def test_missing_field_in_response_is_an_error_not_empty_list():
    """2.7: недостающее поле — ошибка ответа, а не молчаливая подстановка
    пустого списка (что выглядело бы неотличимо от "пропусков нет")."""
    client = _ScriptedLLMClient([{}])
    with pytest.raises(KonspektCompareError):
        await compare_notebook_to_transcript("расшифровка", READABLE_NOTEBOOK, llm_client=client)


async def test_non_list_missing_items_is_an_error():
    client = _ScriptedLLMClient([{"missing_items": "не список"}])
    with pytest.raises(KonspektCompareError):
        await compare_notebook_to_transcript("расшифровка", READABLE_NOTEBOOK, llm_client=client)


async def test_prompt_contains_both_texts_and_forbids_full_transcript_dump():
    client = _ScriptedLLMClient([{"missing_items": []}])
    await compare_notebook_to_transcript(
        "УНИКАЛЬНЫЙ_ТЕКСТ_РАСШИФРОВКИ", f"УНИКАЛЬНЫЙ_ТЕКСТ_КОНСПЕКТА {READABLE_NOTEBOOK}", llm_client=client
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


# --- Находка 6 AUDIT.md: пустая тетрадь ≠ «в конспекте есть всё» ---


async def test_empty_notebook_is_an_error_not_an_empty_missing_list():
    """Главный сценарий находки: ученик сфотографировал чистый лист и
    получал «Похоже, в конспекте есть всё существенное с этого урока!»."""
    client = _ScriptedLLMClient([{"missing_items": []}])
    with pytest.raises(NotebookUnreadableError):
        await compare_notebook_to_transcript("расшифровка урока", "", llm_client=client)
    assert client.calls == [], "модель не должна вызываться, сравнивать нечего"


async def test_whitespace_only_notebook_is_an_error():
    client = _ScriptedLLMClient([{"missing_items": []}])
    with pytest.raises(NotebookUnreadableError):
        await compare_notebook_to_transcript("расшифровка урока", "   \n\t  ", llm_client=client)
    assert client.calls == []


async def test_too_short_notebook_is_an_error():
    """Неразборчивая страница даёт не пустую строку, а огрызок вроде
    «не видно» — его тоже нельзя объявлять полным конспектом."""
    client = _ScriptedLLMClient([{"missing_items": []}])
    with pytest.raises(NotebookUnreadableError):
        await compare_notebook_to_transcript("расшифровка урока", "не видно", llm_client=client)
    assert client.calls == []


def test_notebook_is_unreadable_boundary():
    assert notebook_is_unreadable("") is True
    assert notebook_is_unreadable(None) is True
    assert notebook_is_unreadable("a" * (MIN_NOTEBOOK_TEXT_LENGTH - 1)) is True
    assert notebook_is_unreadable("a" * MIN_NOTEBOOK_TEXT_LENGTH) is False


def test_notebook_unreadable_is_a_compare_error():
    """Вызывающий код, ловящий KonspektCompareError, не должен внезапно
    пропустить новую ошибку мимо себя."""
    assert issubclass(NotebookUnreadableError, KonspektCompareError)
