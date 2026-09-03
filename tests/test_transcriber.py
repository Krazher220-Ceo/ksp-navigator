"""Тесты транскрипции урока через xAI и измерения длительности аудио."""

import asyncio
from pathlib import Path

import httpx
import pytest

from core.config import settings
from core.transcriber import TranscriptionError, _probe_duration_seconds, transcribe


PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUDIO_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "audio_lesson_snippet.m4a"


def test_probe_duration_seconds_matches_known_fixture_length():
    duration = _probe_duration_seconds(AUDIO_FIXTURE)
    assert 45 <= duration <= 50


async def test_transcribe_does_not_block_event_loop(monkeypatch, tmp_path):
    import core.transcriber as module

    audio = tmp_path / "lesson.m4a"
    audio.write_bytes(b"audio")
    tick_count = 0
    stop = False

    async def ticker():
        nonlocal tick_count
        while not stop:
            tick_count += 1
            await asyncio.sleep(0.2)

    async def delayed_xai(*_args):
        await asyncio.sleep(0.45)
        return {"text": "Текст урока", "duration_seconds": 1, "language": "ru"}

    monkeypatch.setattr(module, "_transcribe_xai", delayed_xai)
    ticker_task = asyncio.create_task(ticker())
    await transcribe(audio)
    stop = True
    await ticker_task
    assert tick_count >= 2


async def test_transcribe_nonexistent_file_raises():
    with pytest.raises(TranscriptionError):
        await transcribe("/tmp/this-file-does-not-exist-ksp-navigator-test.m4a")


async def test_transcribe_uses_xai_backend(monkeypatch, tmp_path):
    import core.transcriber as module

    audio = tmp_path / "lesson.m4a"
    audio.write_bytes(b"audio")

    async def fake_xai(path, language, prompt):
        assert path == audio
        return {"text": "Тестовая расшифровка", "duration_seconds": 3, "language": language}

    monkeypatch.setattr(module, "_transcribe_xai", fake_xai)
    result = await transcribe(audio, language="ru")
    assert result["text"] == "Тестовая расшифровка"


async def test_xai_transcriber_sends_expected_request(monkeypatch, tmp_path):
    import core.transcriber as module

    audio = tmp_path / "lesson.m4a"
    audio.write_bytes(b"audio")
    original_key = settings.xai_api_key
    object.__setattr__(settings, "xai_api_key", "test-key")
    received = {}

    async def handler(request):
        received["url"] = str(request.url)
        received["authorization"] = request.headers["authorization"]
        received["body"] = (await request.aread()).decode("utf-8", errors="replace")
        return httpx.Response(200, json={"text": "Текст урока"})

    transport = httpx.MockTransport(handler)
    original_client = module.httpx.AsyncClient

    def fake_client(*args, **kwargs):
        return original_client(transport=transport, *args, **kwargs)

    try:
        monkeypatch.setattr(module.httpx, "AsyncClient", fake_client)
        monkeypatch.setattr(module, "_probe_duration_seconds", lambda _path: 13)
        result = await module._transcribe_xai(audio, "ru", "термин")
        # words добавились 02.09.2026: xAI отдаёт время каждого слова,
        # и до этого дня мы его выбрасывали. В заглушке ответа слов
        # нет — важно, что ключ есть всегда и вызывающему коду не
        # приходится проверять его наличие.
        assert result == {
            "text": "Текст урока", "duration_seconds": 13, "language": "ru", "words": [],
        }
        assert received["url"] == module.XAI_STT_URL
        assert received["authorization"] == "Bearer test-key"
        assert 'name="format"' in received["body"]
        assert 'name="language"' in received["body"]
        assert 'name="keyterm"' in received["body"]
    finally:
        object.__setattr__(settings, "xai_api_key", original_key)


async def test_xai_transcriber_requires_key(monkeypatch, tmp_path):
    import core.transcriber as module

    audio = tmp_path / "lesson.m4a"
    audio.write_bytes(b"audio")
    original_key = settings.xai_api_key
    object.__setattr__(settings, "xai_api_key", None)
    try:
        with pytest.raises(TranscriptionError, match="XAI_API_KEY"):
            await module._transcribe_xai(audio, "ru", None)
    finally:
        object.__setattr__(settings, "xai_api_key", original_key)
