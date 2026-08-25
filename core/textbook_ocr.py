"""
core/textbook_ocr.py — распознавание текста страницы учебника по фото (блок Р6.2).

Зачем модуль: учитель может прислать фото страницы учебника при
/generate — урок тогда строится не только по теме, но и по реальному
содержанию учебника. Отдельный модуль, а не функция внутри
core/llm_client.py: там протокол связи с провайдерами, здесь — конкретная
задача (распознать текст), со своим промптом и схемой ответа.

Что осознанно не делает: не проверяет орфографию, не переводит, не
пересказывает — только распознаёт текст как есть. Не использует
tesseract или любые локальные ML-модели — только уже подключённые в
core/llm_client.py провайдеры с поддержкой изображений (Gemini, OpenAI),
через тот же httpx-клиент, без новых зависимостей (PLAN_STAGE1_EXT.md,
блок Р6.2, прямой запрет в CLAUDE.md).

На что опирается: core.llm_client.LLMClient.complete_json_with_image.
"""

from core.llm_client import LLMClient

SYSTEM_PROMPT = (
    "Ты распознаёшь текст на фотографии страницы учебника. Верни ВЕСЬ "
    "текст со страницы дословно, как он напечатан, без своих пояснений, "
    "исправлений ошибок, перевода или пересказа. Если часть текста "
    "нечитаема — пропусти именно эту часть, не выдумывай, что там могло "
    "быть написано."
)
TASK_TEXT = "Распознай текст на приложенной фотографии страницы учебника."

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Весь распознанный текст со страницы, дословно.",
        },
    },
    "required": ["text"],
}


class TextbookOCRError(Exception):
    """Не удалось распознать текст на фотографии."""


async def recognize_textbook_page(
    image_bytes: bytes,
    image_mime: str,
    llm_client: LLMClient | None = None,
) -> str:
    """Возвращает распознанный текст страницы. Пустой ответ модели —
    честная ошибка (TextbookOCRError), а не пустая строка молча: если
    распознавание не удалось, вызывающий код (bot/handlers.py) должен об
    этом сказать учителю, а не сгенерировать урок без опоры на учебник
    так, будто фото и не присылали."""
    client = llm_client or LLMClient()
    owns_client = llm_client is None
    try:
        result = await client.complete_json_with_image(
            system=SYSTEM_PROMPT,
            user=TASK_TEXT,
            schema=RESPONSE_SCHEMA,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
    finally:
        if owns_client:
            await client.aclose()

    text = (result.get("text") or "").strip()
    if not text:
        raise TextbookOCRError("модель не распознала на фотографии никакого текста")
    return text
