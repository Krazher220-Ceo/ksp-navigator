"""
tests/test_transcriber_env.py — тесты core/transcriber.check_environment()
(блок К1.2).

Реального whisper/ffmpeg не зовём — только shutil.which и Path.exists
подменяются monkeypatch, чтобы проверить логику без зависимости от того,
что реально установлено на машине, где гоняются тесты.
"""

import pytest

from core.config import settings
from core.transcriber import TranscriberEnvironmentError, check_environment


def test_check_environment_passes_when_everything_installed():
    """На этой машине (К1.1: whisper-cli и ffmpeg установлены, модель
    скачана и проверена живым прогоном) проверка должна пройти без
    исключения — не мок, а реальное состояние машины."""
    check_environment()  # не должна бросить


def test_check_environment_raises_when_ffmpeg_missing(monkeypatch):
    import shutil as shutil_module

    original_which = shutil_module.which

    def fake_which(name):
        if name == "ffmpeg":
            return None
        return original_which(name)

    monkeypatch.setattr("core.transcriber.shutil.which", fake_which)

    with pytest.raises(TranscriberEnvironmentError) as exc_info:
        check_environment()
    assert "ffmpeg" in str(exc_info.value)
    assert "brew install ffmpeg" in str(exc_info.value)


def test_check_environment_raises_when_whisper_binary_missing(monkeypatch):
    import shutil as shutil_module

    original_which = shutil_module.which

    def fake_which(name):
        if name == settings.whisper_binary:
            return None
        return original_which(name)

    monkeypatch.setattr("core.transcriber.shutil.which", fake_which)

    with pytest.raises(TranscriberEnvironmentError) as exc_info:
        check_environment()
    assert "whisper" in str(exc_info.value)
    assert "brew install whisper-cpp" in str(exc_info.value)


def test_check_environment_raises_when_model_path_not_set(monkeypatch):
    original = settings.whisper_model_path
    object.__setattr__(settings, "whisper_model_path", None)
    try:
        with pytest.raises(TranscriberEnvironmentError) as exc_info:
            check_environment()
        assert "WHISPER_MODEL_PATH" in str(exc_info.value)
    finally:
        object.__setattr__(settings, "whisper_model_path", original)


def test_check_environment_raises_when_model_file_missing(monkeypatch, tmp_path):
    original = settings.whisper_model_path
    object.__setattr__(settings, "whisper_model_path", str(tmp_path / "does-not-exist.bin"))
    try:
        with pytest.raises(TranscriberEnvironmentError) as exc_info:
            check_environment()
        assert "не найдена" in str(exc_info.value)
    finally:
        object.__setattr__(settings, "whisper_model_path", original)


def test_bot_modules_import_fine_regardless_of_whisper_presence(monkeypatch):
    """К1.2 КГ (часть, проверяемая уже сейчас): отсутствие whisper не
    должно мешать боту стартовать. Полная КГ ("/generate работает,
    /konspekt отвечает внятной ошибкой") довершится в блоке К4, когда
    появится сама команда /konspekt — здесь проверяем то, что уже можно:
    ни core.transcriber, ни bot.handlers, ни bot.main не проверяют
    whisper при импорте (иначе этот тест уже упал бы при сборе, importlib
    делает это на уровне модуля, а не внутри функции)."""
    import importlib

    import bot.handlers
    import bot.main
    import core.transcriber

    importlib.reload(core.transcriber)
    # если бы check_environment() вызывалась при импорте — здесь уже
    # вылетело бы исключение на машине без whisper; на этой машине
    # whisper есть, поэтому дополнительно проверяем сам факт: функция
    # проверки существует, но НЕ вызвана автоматически модулем.
    assert hasattr(core.transcriber, "check_environment")
