"""
tests/core/test_textbook_ocr.py — тесты core/textbook_ocr.py (блок Р6.2).

LLM мокается тем же простым fake, что и в tests/core/test_generator.py/
tests/core/test_ktp_generator.py — здесь важна логика модуля (что делает с
ответом), не транспорт (он покрыт tests/core/test_llm_client.py).
"""

import pytest

from core.textbook_ocr import TextbookOCRError, recognize_textbook_page


class _ScriptedVisionLLMClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    async def complete_json_with_image(self, system, user, schema, image_bytes, image_mime, max_retries=3):
        self.calls.append(
            {"system": system, "user": user, "schema": schema, "image_bytes": image_bytes, "image_mime": image_mime}
        )
        return self.response


async def test_recognize_textbook_page_returns_text():
    fake = _ScriptedVisionLLMClient({"text": "Закон сохранения импульса гласит..."})
    text = await recognize_textbook_page(b"fake-bytes", "image/jpeg", llm_client=fake)

    assert text == "Закон сохранения импульса гласит..."
    assert len(fake.calls) == 1
    assert fake.calls[0]["image_bytes"] == b"fake-bytes"
    assert fake.calls[0]["image_mime"] == "image/jpeg"


async def test_recognize_textbook_page_strips_whitespace():
    fake = _ScriptedVisionLLMClient({"text": "  текст с пробелами по краям  \n"})
    text = await recognize_textbook_page(b"x", "image/png", llm_client=fake)
    assert text == "текст с пробелами по краям"


async def test_recognize_textbook_page_raises_on_empty_text():
    """Пустой ответ модели — честная ошибка, а не пустая строка молча
    (вызывающий код должен сказать учителю, что распознавание не удалось)."""
    fake = _ScriptedVisionLLMClient({"text": ""})
    with pytest.raises(TextbookOCRError):
        await recognize_textbook_page(b"x", "image/jpeg", llm_client=fake)


async def test_recognize_textbook_page_raises_on_missing_text_key():
    fake = _ScriptedVisionLLMClient({})
    with pytest.raises(TextbookOCRError):
        await recognize_textbook_page(b"x", "image/jpeg", llm_client=fake)


async def test_recognize_textbook_page_raises_on_whitespace_only_text():
    fake = _ScriptedVisionLLMClient({"text": "   "})
    with pytest.raises(TextbookOCRError):
        await recognize_textbook_page(b"x", "image/jpeg", llm_client=fake)
