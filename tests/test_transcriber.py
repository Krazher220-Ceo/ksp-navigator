"""
tests/test_transcriber.py — тесты core/transcriber.transcribe() (блок К3).

КГ плана дословно: "реальная расшифровка короткого тестового файла
(10-20 секунд собственной речи, записать при подготовке), не только мок.
Проверить, что бот в это время отвечает на другие команды — это и есть
проверка, что to_thread реально применён."

tests/fixtures/audio_lesson_snippet.m4a — реальная запись автора,
47 секунд, физическая терминология (кинематика). Настоящий whisper.cpp
и ffmpeg зовутся напрямую, не через мок — так же, как тесты
core/pdf_export.py зовут настоящий LibreOffice.
"""

import asyncio
import time
from pathlib import Path

import pytest

from core.transcriber import (
    TranscriptionError,
    _probe_duration_seconds,
    transcribe,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
AUDIO_FIXTURE = FIXTURES_DIR / "audio_lesson_snippet.m4a"


# =====================================================================
# КГ: настоящая расшифровка настоящего файла, не мок
# =====================================================================


async def test_transcribe_real_recording_produces_real_text():
    result = await transcribe(AUDIO_FIXTURE)

    assert result["language"] == "ru"
    assert 40 <= result["duration_seconds"] <= 55  # запись ~47с
    # Дословно ждать точный текст нельзя (whisper не детерминирован
    # побитово между версиями/сборками), но содержательные слова из
    # реальной физической лекции должны появиться.
    text_lower = result["text"].lower()
    assert "кинематика" in text_lower or "механика" in text_lower
    assert len(result["text"]) > 50


async def test_transcribe_respects_explicit_language():
    result = await transcribe(AUDIO_FIXTURE, language="ru")
    assert result["language"] == "ru"


def test_probe_duration_seconds_matches_known_fixture_length():
    duration = _probe_duration_seconds(AUDIO_FIXTURE)
    assert 45 <= duration <= 50


# =====================================================================
# КГ дословно: событийный цикл не блокируется во время транскрипции
# (доказывает, что asyncio.to_thread реально применён, а не просто
# заявлен в комментарии)
# =====================================================================


async def test_transcribe_does_not_block_event_loop():
    tick_count = 0
    stop = False

    async def ticker():
        nonlocal tick_count
        while not stop:
            tick_count += 1
            await asyncio.sleep(0.2)

    ticker_task = asyncio.create_task(ticker())
    t0 = time.time()
    await transcribe(AUDIO_FIXTURE)
    elapsed = time.time() - t0
    stop = True
    await ticker_task

    # За время транскрипции (несколько секунд) тикер должен был
    # сработать многократно — если бы to_thread не применялся, цикл был
    # бы заблокирован синхронным subprocess.run, и tick_count остался бы
    # на 0 или 1.
    expected_min_ticks = max(2, int(elapsed / 0.2) - 2)
    assert tick_count >= expected_min_ticks, (
        f"событийный цикл почти не тикал во время transcribe() ({tick_count} раз "
        f"за {elapsed:.1f}с) — похоже, вызов блокирующий, не через to_thread"
    )


# =====================================================================
# Ошибки — файл не существует / не аудио
# =====================================================================


async def test_transcribe_nonexistent_file_raises():
    with pytest.raises(TranscriptionError):
        await transcribe("/tmp/this-file-does-not-exist-ksp-navigator-test.m4a")


async def test_transcribe_non_audio_file_raises(tmp_path):
    garbage = tmp_path / "not_audio.txt"
    garbage.write_text("это не аудиофайл, а текст")
    with pytest.raises(TranscriptionError):
        await transcribe(garbage)
