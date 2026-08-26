"""
core/transcriber.py — транскрипция аудио урока через whisper.cpp
(блоки К1.2 и К3, PLAN_STAGE2.md).

Зачем модуль: единственное место, где проект зовёт whisper.cpp и ffmpeg —
оба внешние бинарники, вызываются через subprocess (CLAUDE.md, правило 2:
whisper.cpp разрешён именно так, не как Python-зависимость). Проверка
окружения (К1.2) — ленивая, в момент первого реального вызова: весь
остальной проект обязан работать на машине без whisper вообще, отсутствие
модели или бинарника не должно мешать боту стартовать и не должно мешать
/generate, /teacher и остальным командам этапа 1.

Что осознанно не делает: не декодирует и не режет аудио на части — это
делает ffmpeg (единственный конвертер, приводящий любой входной формат к
16 кГц моно WAV, которого ждёт whisper.cpp). Не склеивает несколько
частей одного урока в один транскрипт — это делает вызывающий код
(bot/handlers.py, make_transcribe_handler, блок К3.2): transcribe()
берёт РОВНО один файл, склейка нескольких — на уровень выше. Не решает,
что делать с готовым транскриптом (конспект, КСП) — это
core/konspekt_generator.py (блок К4) и bot/handlers.py. Не удаляет
исходный аудиофайл — удаление (К2.4) делает вызывающий код в своём
finally, transcribe() только читает то, что ей передали.

На что опирается: whisper-cli, ffmpeg и ffprobe из Homebrew
(scripts/setup_mac.sh, ffprobe ставится вместе с ffmpeg), core.config.settings
(WHISPER_BINARY/WHISPER_MODEL_PATH/WHISPER_LANGUAGE).
"""

import asyncio
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from core.config import settings


class TranscriberEnvironmentError(Exception):
    """whisper.cpp или ffmpeg не готовы к работе — бинарник не найден,
    модель не скачана. Понятная русская ошибка вместо голого исключения
    subprocess, которую bot/handlers.py покажет пользователю в /konspekt
    (блок К4) без падения всего бота (К1.2, КГ)."""


def check_environment() -> None:
    """Ленивая проверка (К1.2): вызывается ПЕРВОЙ строкой transcribe()
    (и может вызываться отдельно, например для диагностической команды),
    а не при импорте модуля и не при старте бота. Ничего не возвращает —
    либо тихо проходит, либо бросает TranscriberEnvironmentError с
    понятным текстом, что именно не так и как починить."""
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise TranscriberEnvironmentError(
            "ffmpeg не найден — без него запись не привести к формату, "
            "который понимает whisper. Установите: brew install ffmpeg, "
            "или запустите scripts/setup_mac.sh."
        )

    # Ловушка плана (К1.1): не зашивать абсолютный путь /opt/homebrew/bin
    # в код — искать через shutil.which, тем же способом, что уже сделано
    # для soffice в core/pdf_export.py. settings.whisper_binary может быть
    # именем команды ("whisper-cli") ИЛИ уже готовым абсолютным путём —
    # shutil.which принимает оба случая корректно.
    whisper_path = shutil.which(settings.whisper_binary)
    if whisper_path is None:
        raise TranscriberEnvironmentError(
            f"whisper не найден ({settings.whisper_binary!r} нет в PATH) — "
            "поставьте: brew install whisper-cpp, или запустите scripts/setup_mac.sh."
        )

    if not settings.whisper_model_path:
        raise TranscriberEnvironmentError(
            "путь к модели whisper не задан — впишите WHISPER_MODEL_PATH в .env "
            "(модель скачивает scripts/setup_mac.sh в ~/whisper-models/)."
        )

    model_path = Path(settings.whisper_model_path)
    if not model_path.exists():
        raise TranscriberEnvironmentError(
            f"модель whisper не найдена по пути {model_path} — проверьте "
            "WHISPER_MODEL_PATH в .env, или запустите scripts/setup_mac.sh заново."
        )


class TranscriptionError(Exception):
    """ffmpeg или whisper.cpp отказали на конкретном файле (испорченный
    аудиофайл, таймаут, неожиданный код возврата)."""


# --- К3.1: параметры вызова, проверены живым прогоном 26.08.2026 ---
#
# Флаги whisper-cli — не изобретены, а взяты из реального прогона
# (KPI_STAGE1.md, "Замер транскрипции"): -l ru (обязателен, по умолчанию
# en), -nt (без таймкодов — конспекту нужна связная проза, не субтитры),
# -otxt -of <путь без расширения> (ловушка 4: имя выходного файла не
# угадывается, задаётся явно).
_FFMPEG_TIMEOUT_MIN_SECONDS = 30
_FFMPEG_TIMEOUT_PER_SECOND_OF_AUDIO = 0.2  # конвертация в разы быстрее реального времени

# Живой замер (KPI_STAGE1.md): 8-11х быстрее реального времени на M2 с
# Metal. Таймаут — не в обрез под этот замер, а с большим запасом на
# случай худшего дня (диск занят бэкапом, другая нагрузка на Mac):
# 0.5 от длины записи даёт 2х реального времени, это в 4-5 раз хуже уже
# измеренного, и всё равно не должно превышаться в норме.
_WHISPER_TIMEOUT_MIN_SECONDS = 120  # с запасом на разовую компиляцию Metal-шейдеров (~18с)
_WHISPER_TIMEOUT_PER_SECOND_OF_AUDIO = 0.5
_WHISPER_THREADS = 8


def _probe_duration_seconds(audio_path: Path) -> int:
    """Длительность аудио в секундах через ffprobe — нужна ДО запуска
    whisper, чтобы выставить пропорциональный таймаут (ловушка 2: не
    константа в 60с, как у PDF)."""
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


def _convert_to_wav(audio_path: Path, wav_path: Path, duration_seconds: int) -> None:
    """16 кГц моно WAV — единственный формат, который надёжно понимает
    whisper.cpp независимо от того, что прислал учитель (голосовое,
    mp3, m4a, что угодно)."""
    timeout = max(_FFMPEG_TIMEOUT_MIN_SECONDS, int(duration_seconds * _FFMPEG_TIMEOUT_PER_SECOND_OF_AUDIO))
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"ffmpeg не уложился в {timeout}с на конвертации {audio_path.name}") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise TranscriptionError(f"ffmpeg не смог сконвертировать {audio_path.name}: {stderr[-500:]}")
    if not wav_path.exists():
        raise TranscriptionError(f"ffmpeg отработал без ошибки, но файл {wav_path} не появился")


def _run_whisper(wav_path: Path, language: str, duration_seconds: int, prompt: str | None) -> str:
    """Возвращает распознанный текст. Ловушка 3: непустой stderr — не
    признак провала, whisper.cpp пишет туда прогресс и техническую
    информацию (загрузка модели, Metal-инициализация) наравне с
    ошибками — смотрим только на код возврата."""
    output_stem = wav_path.with_suffix("")  # -of требует путь БЕЗ расширения (ловушка 4)
    timeout = max(_WHISPER_TIMEOUT_MIN_SECONDS, int(duration_seconds * _WHISPER_TIMEOUT_PER_SECOND_OF_AUDIO))

    command = [
        settings.whisper_binary,
        "-m", settings.whisper_model_path,
        "-f", str(wav_path),
        "-l", language,
        "-nt",
        "-otxt",
        "-of", str(output_stem),
        "-t", str(_WHISPER_THREADS),
    ]
    # К3.3: подсказка словарём терминов — опция, ВЫКЛЮЧЕНА по умолчанию
    # (prompt=None). Проверено 26.08.2026 (KPI_STAGE1.md): чинит
    # терминологию на чистой записи, но добавляет галлюцинации на записи
    # похуже — не включаем без явного запроса вызывающего кода.
    if prompt:
        command.extend(["--prompt", prompt])

    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(
            f"whisper не уложился в {timeout}с на файле длительностью {duration_seconds}с"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise TranscriptionError(f"whisper вернул код {result.returncode}: {stderr[-500:]}")

    output_path = output_stem.with_suffix(".txt")
    if not output_path.exists():
        raise TranscriptionError(
            f"whisper отработал без ошибки (код 0), но файл {output_path} не появился"
        )
    return output_path.read_text(encoding="utf-8").strip()


def _transcribe_sync(audio_path: Path, language: str, prompt: str | None) -> dict:
    """Вся синхронная, блокирующая работа одним куском — вызывается
    ТОЛЬКО через asyncio.to_thread (ловушка 1: эта ошибка в проекте уже
    была дважды, с soffice при .doc->.docx и при экспорте PDF; здесь
    минуты, не секунды — синхронный вызов заморозил бы весь бот)."""
    check_environment()
    duration_seconds = _probe_duration_seconds(audio_path)

    with tempfile.TemporaryDirectory(prefix="ksp_navigator_whisper_") as tmp_dir:
        wav_path = Path(tmp_dir) / f"{uuid.uuid4()}.wav"
        _convert_to_wav(audio_path, wav_path, duration_seconds)
        text = _run_whisper(wav_path, language, duration_seconds, prompt)
        # wav_path и его .txt лежат в TemporaryDirectory — убираются сами
        # при выходе из блока with, отдельно чистить не нужно (в отличие
        # от исходного audio_path — его удаление К2.4, забота
        # вызывающего кода в bot/handlers.py, не этой функции).

    return {"text": text, "duration_seconds": duration_seconds, "language": language}


async def transcribe(audio_path: Path | str, language: str | None = None, prompt: str | None = None) -> dict:
    """Расшифровывает ОДИН аудиофайл. Возвращает {"text", "duration_seconds",
    "language"}. language=None — берётся settings.whisper_language (по
    умолчанию "ru"). prompt — К3.3, необязательная подсказка словарём
    терминов, по умолчанию выключена (см. _run_whisper)."""
    resolved_language = language or settings.whisper_language
    return await asyncio.to_thread(_transcribe_sync, Path(audio_path), resolved_language, prompt)


async def probe_duration_seconds(audio_path: Path | str) -> int:
    """Публичная асинхронная обёртка над _probe_duration_seconds — для
    вызывающего кода вне этого модуля (bot/handlers.py, оценка времени
    расшифровки ДО её начала, блок К3.2). Тоже через to_thread: ffprobe —
    отдельный процесс, вызов синхронный и блокирующий, пусть и короткий."""
    return await asyncio.to_thread(_probe_duration_seconds, Path(audio_path))
