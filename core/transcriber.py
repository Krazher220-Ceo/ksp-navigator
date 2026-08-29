"""Транскрипция аудио урока через REST API xAI.

Возвращает единый результат для обработчика бота и измеряет длительность
исходного файла локальным ffprobe. Не склеивает записи, не создаёт конспект
и не удаляет аудио; это остаётся ответственностью обработчика очереди.
"""

import asyncio
import subprocess
from pathlib import Path

import httpx

from core.config import settings


XAI_STT_URL = "https://api.x.ai/v1/stt"


class TranscriptionError(Exception):
    """xAI или проверка длительности отказали для конкретного аудиофайла."""


def _probe_duration_seconds(audio_path: Path) -> int:
    """Длительность аудио в секундах через ffprobe для БД и сообщений."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise TranscriptionError(f"ffprobe не смог определить длительность {audio_path.name}: {stderr}")
    try:
        return int(float(result.stdout.decode("utf-8", errors="replace").strip()))
    except ValueError as exc:
        raise TranscriptionError(f"ffprobe вернул нечисловую длительность для {audio_path.name}") from exc


async def transcribe(audio_path: Path | str, language: str | None = None, prompt: str | None = None) -> dict:
    """Расшифровывает один аудиофайл через xAI и возвращает текст с длительностью."""
    return await _transcribe_xai(Path(audio_path), language or settings.stt_language, prompt)


async def _transcribe_xai(audio_path: Path, language: str, prompt: str | None) -> dict:
    """Расшифровка через xAI без молчаливого отката на локальный режим."""
    if not settings.xai_api_key:
        raise TranscriptionError("не задан XAI_API_KEY для облачной расшифровки")
    if not audio_path.exists():
        raise TranscriptionError(f"аудиофайл не найден: {audio_path}")

    duration_seconds = await asyncio.to_thread(_probe_duration_seconds, audio_path)
    data = {"format": "true", "language": language}
    if prompt and len(prompt) <= 50:
        data["keyterm"] = prompt
    try:
        with audio_path.open("rb") as audio_file:
            files = {"file": (audio_path.name, audio_file, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    XAI_STT_URL,
                    headers={"Authorization": f"Bearer {settings.xai_api_key}"},
                    data=data,
                    files=files,
                )
        response.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        raise TranscriptionError(f"xAI не смог расшифровать запись: {exc}") from exc

    try:
        text = str(response.json()["text"]).strip()
    except (ValueError, TypeError, KeyError) as exc:
        raise TranscriptionError("xAI вернул ответ без текста расшифровки") from exc
    if not text:
        raise TranscriptionError("xAI вернул пустую расшифровку")
    return {"text": text, "duration_seconds": duration_seconds, "language": language}


async def probe_duration_seconds(audio_path: Path | str) -> int:
    """Асинхронно определяет длительность для оценки времени в обработчике."""
    return await asyncio.to_thread(_probe_duration_seconds, Path(audio_path))
