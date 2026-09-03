"""Транскрипция аудио урока через REST API xAI.

Возвращает единый результат для обработчика бота и измеряет длительность
исходного файла локальным ffprobe. Не склеивает записи, не создаёт конспект
и не удаляет аудио; это остаётся ответственностью обработчика очереди.
"""

import asyncio
import re
import subprocess
from pathlib import Path

import httpx

from core.config import settings


XAI_STT_URL = "https://api.x.ai/v1/stt"

# MediaRecorder браузера пишет webm потоково и не может задним числом
# дописать длительность в заголовок (Segment Info) — это свойство самого
# формата записи с вкладки, а не повреждённый файл. ffprobe в быстром
# режиме тогда отвечает 'N/A' с returncode=0 (проверено воспроизведением:
# `ffmpeg ... -f webm - > file` без seek на диск даёт тот же эффект, что
# и запись из браузера). Резервный способ — полное декодирование через
# ffmpeg с включённой статистикой: он читает файл до конца и печатает в
# stderr последнюю метку time=ЧЧ:ММ:СС.СС, из которой и берётся
# длительность. Вызываются как внешние бинарники — тем же приёмом
# subprocess, каким в проекте уже вызывается LibreOffice/soffice
# (core/ksp_parser.py) — без новых зависимостей.
_DECODE_TIME_RE = re.compile(r"time=(\d+):(\d\d):(\d\d\.\d\d)")


class TranscriptionError(Exception):
    """xAI или проверка длительности отказали для конкретного аудиофайла."""


def _probe_duration_seconds(audio_path: Path) -> int:
    """Длительность аудио в секундах для БД и сообщений.

    Сначала быстрый путь — заголовок контейнера (ffprobe). Не нашлось —
    медленный, но надёжный: полное декодирование (ffmpeg)."""
    duration = _probe_duration_from_header(audio_path)
    if duration is not None:
        return duration
    return _probe_duration_by_decoding(audio_path)


def _probe_duration_from_header(audio_path: Path) -> int | None:
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
    except ValueError:
        return None


def _probe_duration_by_decoding(audio_path: Path) -> int:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-stats", "-i", str(audio_path), "-f", "null", "-"],
        capture_output=True,
        timeout=120,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    matches = _DECODE_TIME_RE.findall(stderr)
    if not matches:
        raise TranscriptionError(
            f"не удалось определить длительность {audio_path.name} даже полным декодированием: "
            f"{stderr.strip()}"
        )
    hours, minutes, seconds = matches[-1]
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds))


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
        ответ = response.json()
        text = str(ответ["text"]).strip()
    except (ValueError, TypeError, KeyError) as exc:
        raise TranscriptionError("xAI вернул ответ без текста расшифровки") from exc
    if not text:
        raise TranscriptionError("xAI вернул пустую расшифровку")

    # xAI отдаёт время каждого слова (start/end в секундах). До 02.09.2026
    # мы это выбрасывали и показывали расшифровку сплошным полотном.
    # Время здесь настоящее, измеренное — не наша оценка по длине текста,
    # и именно поэтому его можно показывать человеку.
    слова = ответ.get("words")
    if not isinstance(слова, list):
        слова = []

    return {
        "text": text,
        "duration_seconds": duration_seconds,
        "language": language,
        "words": слова,
    }


async def probe_duration_seconds(audio_path: Path | str) -> int:
    """Асинхронно определяет длительность для оценки времени в обработчике."""
    return await asyncio.to_thread(_probe_duration_seconds, Path(audio_path))
