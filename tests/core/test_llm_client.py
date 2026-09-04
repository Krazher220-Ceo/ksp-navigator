"""
tests/core/test_llm_client.py — тесты core/llm_client.py.

httpx мокается через httpx.MockTransport — реальные вызовы к DeepSeek,
Gemini, Grok, OpenAI или Anthropic в тестах запрещены (PLAN_STAGE1.md,
Б2.2). asyncio.sleep патчится, чтобы тесты на ретраи не ждали реальные
секунды растущей паузы.

Провайдеры в тестах — не настоящие имена (deepseek/gemini/...), а
условные "primary"/"fallback" с kind="openai_compatible": протокол
запроса/ответа для теста не важен, важна только логика ретраев и
фоллбэка в LLMClient, которая от конкретного провайдера не зависит.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.llm_client import (
    LLMBadResponse,
    LLMConfigError,
    LLMUnavailable,
    LLMClient,
    ProviderConfig,
    TokenUsage,
    _load_providers_from_settings,
    _parse_anthropic,
    _parse_gemini,
    _parse_openai_compatible,
)

# Содержимое неважно — в тестах ниже httpx замокан, реального декодирования
# изображения не происходит нигде, байты просто уходят в base64 в теле
# запроса. Не настоящий PNG, и не должен им быть для этих тестов.
FAKE_IMAGE_BYTES = b"fake-image-bytes-for-tests"

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def make_provider(name: str, base_url: str) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        kind="openai_compatible",
        api_key=f"test-key-{name}",
        model="test-model",
        base_url=base_url,
    )


def openai_compatible_response(content: str, status_code: int = 200) -> httpx.Response:
    if status_code != 200:
        return httpx.Response(status_code, text="upstream error")
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 42},
        },
    )


@pytest.fixture(autouse=True)
def no_real_sleep():
    """Патчит asyncio.sleep во всех тестах этого файла — ретраи не должны
    реально ждать секунды. Возвращает мок, чтобы тест мог проверить,
    сколько раз и с какими паузами он вызывался."""
    with patch("core.llm_client.asyncio.sleep", new_callable=AsyncMock) as sleep_mock:
        yield sleep_mock


# --- (а) успех первого провайдера в цепочке ---


async def test_success_on_first_provider(no_real_sleep):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return openai_compatible_response('{"answer": "ok"}')

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        result = await client.complete_json("система", "вопрос", SCHEMA)

    assert result == {"answer": "ok"}
    assert calls == ["primary.test"]
    no_real_sleep.assert_not_called()


# --- (б) первый провайдер отдаёт 500 -> успех второго ---


async def test_primary_500_falls_back_to_second_provider(no_real_sleep):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "primary.test":
            return openai_compatible_response("", status_code=500)
        return openai_compatible_response('{"answer": "from fallback"}')

    providers = [
        make_provider("primary", "https://primary.test"),
        make_provider("fallback", "https://fallback.test"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json("система", "вопрос", SCHEMA)

    assert result == {"answer": "from fallback"}
    # primary: 3 попытки (max_retries по умолчанию), потом fallback: 1 попытка
    assert calls.count("primary.test") == 3
    assert calls.count("fallback.test") == 1


# --- (в) оба провайдера легли -> LLMUnavailable ---


async def test_all_providers_fail_raises_llm_unavailable(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return openai_compatible_response("", status_code=503)

    providers = [
        make_provider("primary", "https://primary.test"),
        make_provider("fallback", "https://fallback.test"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        with pytest.raises(LLMUnavailable):
            await client.complete_json("система", "вопрос", SCHEMA)


# --- (г) кривой JSON -> один повторный запрос тому же провайдеру -> успех ---


async def test_bad_json_triggers_single_repair_retry(no_real_sleep):
    call_count = {"n": 0}
    seen_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        seen_bodies.append(json.loads(request.content))
        if call_count["n"] == 1:
            return openai_compatible_response("это не JSON, а обычный текст")
        return openai_compatible_response('{"answer": "починено"}')

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        result = await client.complete_json("система", "вопрос", SCHEMA)

    assert result == {"answer": "починено"}
    assert call_count["n"] == 2
    # второй запрос — тому же провайдеру, с явной просьбой вернуть строгий JSON
    second_user_message = seen_bodies[1]["messages"][1]["content"]
    assert "не распарсился" in second_user_message


async def test_bad_json_twice_raises_llm_bad_response(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return openai_compatible_response("всё ещё не JSON")

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        with pytest.raises(LLMBadResponse):
            await client.complete_json("система", "вопрос", SCHEMA)


# --- (д) 429 -> ровно 3 попытки с растущей паузой ---


async def test_429_retries_exactly_three_times_with_growing_pause(no_real_sleep):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return openai_compatible_response("", status_code=429)

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        with pytest.raises(LLMUnavailable):
            await client.complete_json("система", "вопрос", SCHEMA, max_retries=3)

    assert call_count["n"] == 3
    assert no_real_sleep.call_count == 2
    first_delay = no_real_sleep.call_args_list[0].args[0]
    second_delay = no_real_sleep.call_args_list[1].args[0]
    assert second_delay > first_delay


# --- бонус: 400/401 не ретраится, сразу переход к следующему провайдеру ---


async def test_400_fails_immediately_no_retry_moves_to_next_provider(no_real_sleep):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "primary.test":
            return openai_compatible_response("", status_code=400)
        return openai_compatible_response('{"answer": "ok"}')

    providers = [
        make_provider("primary", "https://primary.test"),
        make_provider("fallback", "https://fallback.test"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json("система", "вопрос", SCHEMA)

    assert result == {"answer": "ok"}
    # ровно одна попытка на primary — 400 не ретраится
    assert calls.count("primary.test") == 1
    no_real_sleep.assert_not_called()


async def test_401_fails_immediately_no_retry(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return openai_compatible_response("", status_code=401)

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        with pytest.raises(LLMUnavailable):
            await client.complete_json("система", "вопрос", SCHEMA)

    no_real_sleep.assert_not_called()


# --- бонус: сборка цепочки провайдеров из настроек (_load_providers_from_settings) ---


class _FakeSettings:
    def __init__(self, order, providers):
        self.llm_provider_order = order
        self.llm_providers = providers


def test_provider_without_api_key_is_skipped_silently():
    fake = _FakeSettings(
        order=("deepseek", "gemini"),
        providers={
            "deepseek": {"api_key": None, "model": "deepseek-v4-flash", "base_url": None},
            "gemini": {"api_key": "real-key", "model": "gemini-test", "base_url": None},
        },
    )
    with patch("core.llm_client.settings", fake):
        providers = _load_providers_from_settings()

    assert [p.name for p in providers] == ["gemini"]


def test_provider_with_api_key_but_no_model_raises_config_error():
    fake = _FakeSettings(
        order=("deepseek",),
        providers={
            "deepseek": {"api_key": "real-key", "model": None, "base_url": None},
        },
    )
    with patch("core.llm_client.settings", fake):
        with pytest.raises(LLMConfigError):
            _load_providers_from_settings()


def test_unknown_provider_name_raises_config_error():
    fake = _FakeSettings(
        order=("mystery_llm",),
        providers={
            "mystery_llm": {"api_key": "real-key", "model": "m1", "base_url": None},
        },
    )
    with patch("core.llm_client.settings", fake):
        with pytest.raises(LLMConfigError):
            _load_providers_from_settings()


# =====================================================================
# Р6.2: complete_json_with_image — vision-запросы
# =====================================================================


def make_named_provider(name: str, kind: str, base_url: str) -> ProviderConfig:
    """Как make_provider, но с настоящим именем провайдера — vision-фильтр
    (VISION_CAPABLE_PROVIDERS) проверяет именно имя, не kind."""
    return ProviderConfig(name=name, kind=kind, api_key=f"test-key-{name}", model="test-model", base_url=base_url)


async def test_non_vision_provider_is_skipped_vision_provider_answers(no_real_sleep):
    """КГ Р6.2, дословно из плана: провайдер без vision пропущен, следующий
    ответил, текст попал в промпт генерации."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return openai_compatible_response('{"text": "распознанный текст со страницы"}')

    providers = [
        make_named_provider("deepseek", "openai_compatible", "https://deepseek.test"),  # не vision
        make_named_provider("openai", "openai_compatible", "https://openai.test"),  # vision
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json_with_image(
            "система", "распознай текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/jpeg"
        )

    assert result == {"text": "распознанный текст со страницы"}
    # deepseek вообще не должен быть вызван — он не в VISION_CAPABLE_PROVIDERS,
    # не "первая попытка, которая упала", а полностью пропущен.
    assert "deepseek.test" not in calls
    assert calls == ["openai.test"]


async def test_vision_request_includes_image_in_body(no_real_sleep):
    """Тело запроса реально содержит картинку, не просто текст — иначе
    это не vision-запрос, а обычный текстовый с лишним параметром."""
    captured_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return openai_compatible_response('{"text": "ok"}')

    providers = [make_named_provider("openai", "openai_compatible", "https://openai.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        await client.complete_json_with_image(
            "система", "распознай текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/png"
        )

    user_content = captured_body["messages"][1]["content"]
    assert isinstance(user_content, list)
    image_blocks = [b for b in user_content if b.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_gemini_vision_request_uses_inline_data(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        parts = body["contents"][0]["parts"]
        assert any("inline_data" in p for p in parts)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"text": "ok"}'}]}}]},
        )

    providers = [make_named_provider("gemini", "gemini", "https://gemini.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json_with_image(
            "система", "распознай текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/jpeg"
        )

    assert result == {"text": "ok"}


async def test_gemini_key_sent_as_header_not_in_url(no_real_sleep):
    """М0 (PLAN_STAGE2.md): ключ Gemini раньше уходил в query-строку URL
    (?key=...), а httpx на уровне INFO логирует полный URL запроса — ключ
    утекал в logs/app.log. Теперь ключ обязан идти заголовком
    x-goog-api-key, и в URL его быть не должно вообще."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "key=" not in str(request.url)
        assert request.headers.get("x-goog-api-key") == "test-key-gemini"
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"answer": "ok"}'}]}}]},
        )

    providers = [make_named_provider("gemini", "gemini", "https://gemini.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json("система", "запрос", SCHEMA)

    assert result == {"answer": "ok"}


async def test_gemini_vision_key_sent_as_header_not_in_url(no_real_sleep):
    """То же самое (М0), но для vision-запроса — сборщик у него отдельный
    (_build_gemini_vision), правку легко внести в один и забыть про другой."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "key=" not in str(request.url)
        assert request.headers.get("x-goog-api-key") == "test-key-gemini"
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"text": "ok"}'}]}}]},
        )

    providers = [make_named_provider("gemini", "gemini", "https://gemini.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json_with_image(
            "система", "распознай текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/jpeg"
        )

    assert result == {"text": "ok"}


async def test_complete_json_with_image_raises_when_no_vision_provider_configured(no_real_sleep):
    """Только текстовые провайдеры настроены — честная ошибка, не молчаливый
    провал и не попытка отправить картинку туда, где её не разберут."""
    providers = [make_named_provider("deepseek", "openai_compatible", "https://deepseek.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: openai_compatible_response("{}"))) as http:
        client = LLMClient(providers=providers, http_client=http)
        with pytest.raises(LLMUnavailable):
            await client.complete_json_with_image(
                "система", "текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/jpeg"
            )


async def test_vision_provider_500_falls_back_to_next_vision_provider(no_real_sleep):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "gemini.test":
            return httpx.Response(500, text="upstream error")
        return openai_compatible_response('{"text": "from openai"}')

    providers = [
        make_named_provider("gemini", "gemini", "https://gemini.test"),
        make_named_provider("openai", "openai_compatible", "https://openai.test"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        result = await client.complete_json_with_image(
            "система", "текст", SCHEMA, image_bytes=FAKE_IMAGE_BYTES, image_mime="image/jpeg"
        )

    assert result == {"text": "from openai"}
    assert calls.count("gemini.test") == 3  # свои ретраи, потом фоллбэк
    assert calls.count("openai.test") == 1


# =====================================================================
# М4.1 — учёт токенов и попаданий в кэш по каждому протоколу
# =====================================================================


def test_parse_openai_compatible_extracts_deepseek_cache_fields():
    """Поля проверены живым вызовом DeepSeek 26.08.2026 (KPI_STAGE1.md) —
    prompt_cache_hit_tokens приходит на верхнем уровне usage, не вложенным."""
    data = {
        "choices": [{"message": {"content": "ответ"}}],
        "usage": {
            "prompt_tokens": 527,
            "completion_tokens": 44,
            "total_tokens": 571,
            "prompt_cache_hit_tokens": 512,
            "prompt_cache_miss_tokens": 15,
        },
    }
    text, usage = _parse_openai_compatible(data)
    assert text == "ответ"
    assert usage == TokenUsage(total=571, prompt=527, completion=44, prompt_cached=512)


def test_parse_openai_compatible_falls_back_to_nested_cached_tokens():
    """Провайдер общего OpenAI-совместимого формата (не DeepSeek) может не
    иметь prompt_cache_hit_tokens, но иметь вложенный
    prompt_tokens_details.cached_tokens — тоже должно читаться."""
    data = {
        "choices": [{"message": {"content": "ответ"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
    }
    _, usage = _parse_openai_compatible(data)
    assert usage.prompt_cached == 80


def test_parse_openai_compatible_missing_usage_gives_none_not_zero():
    """Отсутствующее поле — None ("провайдер не сообщает"), не 0
    ("сообщает и получилось ноль") — разница важна при проверке кэша."""
    data = {"choices": [{"message": {"content": "ответ"}}]}
    text, usage = _parse_openai_compatible(data)
    assert text == "ответ"
    assert usage == TokenUsage(total=None, prompt=None, completion=None, prompt_cached=None)


def test_parse_gemini_extracts_real_field_names():
    """Поля проверены живым вызовом Gemini 26.08.2026 — camelCase, не
    snake_case, и totalTokenCount, не total_tokens."""
    data = {
        "candidates": [{"content": {"parts": [{"text": "ответ"}]}}],
        "usageMetadata": {
            "promptTokenCount": 22,
            "candidatesTokenCount": 7,
            "totalTokenCount": 29,
        },
    }
    text, usage = _parse_gemini(data)
    assert text == "ответ"
    assert usage == TokenUsage(total=29, prompt=22, completion=7, prompt_cached=None)


def test_parse_gemini_cached_content_token_count_when_present():
    data = {
        "candidates": [{"content": {"parts": [{"text": "ответ"}]}}],
        "usageMetadata": {
            "promptTokenCount": 500,
            "candidatesTokenCount": 20,
            "totalTokenCount": 520,
            "cachedContentTokenCount": 400,
        },
    }
    _, usage = _parse_gemini(data)
    assert usage.prompt_cached == 400


def test_parse_anthropic_sums_input_and_output_tokens():
    data = {
        "content": [{"text": "ответ"}],
        "usage": {"input_tokens": 100, "output_tokens": 30, "cache_read_input_tokens": 60},
    }
    text, usage = _parse_anthropic(data)
    assert text == "ответ"
    assert usage.total == 130
    assert usage.prompt == 100
    assert usage.completion == 30
    assert usage.prompt_cached == 60


def test_parse_anthropic_missing_usage_does_not_crash():
    data = {"content": [{"text": "ответ"}]}
    text, usage = _parse_anthropic(data)
    assert text == "ответ"
    assert usage == TokenUsage(total=None, prompt=None, completion=None, prompt_cached=None)


async def test_client_logs_usage_without_crashing_when_fields_missing(no_real_sleep, caplog):
    """Ни один протокол не должен ронять вызов из-за отсутствующих полей
    usage — весь путь до лога должен пройти целиком."""
    import logging

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer": "ok"}'}}]})

    providers = [make_provider("primary", "https://primary.test")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=providers, http_client=http)
        with caplog.at_level(logging.INFO, logger="core.llm_client"):
            result = await client.complete_json("система", "запрос", SCHEMA)

    assert result == {"answer": "ok"}
    assert any("токены_из_кэша=None" in record.message for record in caplog.records)


# =====================================================================
# М6: LLMClient.total_tokens_used — накопление за время жизни клиента
# =====================================================================


async def test_total_tokens_used_accumulates_across_calls(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer": "ok"}'}}],
                "usage": {"total_tokens": 100},
            },
        )

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        assert client.total_tokens_used == 0
        await client.complete_json("система", "вопрос", SCHEMA)
        await client.complete_json("система", "вопрос2", SCHEMA)

    assert client.total_tokens_used == 200


async def test_total_tokens_used_not_incremented_when_provider_does_not_report(no_real_sleep):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer": "ok"}'}}]})

    provider = make_provider("primary", "https://primary.test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LLMClient(providers=[provider], http_client=http)
        await client.complete_json("система", "вопрос", SCHEMA)

    assert client.total_tokens_used == 0
