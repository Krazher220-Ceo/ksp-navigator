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
16 кГц моно WAV, которого ждёт whisper.cpp). Не решает, что делать с
готовым транскриптом (конспект, КСП) — это core/konspekt_generator.py
(блок К4) и bot/handlers.py.

На что опирается: whisper-cli и ffmpeg из Homebrew (scripts/setup_mac.sh),
core.config.settings (WHISPER_BINARY/WHISPER_MODEL_PATH/WHISPER_LANGUAGE).
"""

import shutil
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
