"""
core/config.py — загрузка конфигурации проекта из .env.

Зачем модуль: единая точка входа для всех путей и настроек проекта,
чтобы каждый следующий модуль не собирал пути и не читал переменные
окружения сам по себе.

Что осознанно не делает: не проверяет валидность ключей LLM-провайдеров
(это задача core/llm_client.py — ошибка провайдера должна всплывать там,
где он реально вызывается) и не содержит бизнес-логики генерации КСП.

На что опирается: python-dotenv для чтения .env, стандартная библиотека
logging. При импорте создаёт недостающие рабочие папки и настраивает
логирование — модуль подключается один раз в самом начале работы бота,
воркера очереди или веб-сервера.
"""

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта — папка на уровень выше core/
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    """Читает переменную окружения; пустая строка считается отсутствием значения."""
    value = os.environ.get(name)
    return value if value else default


def _fail(message: str) -> None:
    """Останавливает запуск с понятной ошибкой на русском языке.

    Ошибка конфигурации должна быть видна сразу при старте, а не через
    десять минут работы бота на первом же реальном запросе.
    """
    print(f"ОШИБКА КОНФИГУРАЦИИ: {message}", file=sys.stderr)
    raise SystemExit(1)


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    storage_dir: Path
    uploads_dir: Path
    generated_dir: Path
    builtin_templates_dir: Path
    logs_dir: Path
    db_path: Path

    telegram_bot_token: str
    anthropic_api_key: str | None
    openai_api_key: str | None
    llm_primary: str
    llm_fallback: str
    llm_model_primary: str | None
    llm_model_fallback: str | None

    webapp_url: str | None
    webapp_port: int

    log_level: str


def _resolve_db_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def _build_settings() -> Settings:
    storage_dir = BASE_DIR / "storage"
    uploads_dir = storage_dir / "uploads"
    generated_dir = storage_dir / "generated"
    builtin_templates_dir = storage_dir / "builtin_templates"
    logs_dir = BASE_DIR / "logs"

    for directory in (storage_dir, uploads_dir, generated_dir, builtin_templates_dir, logs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    telegram_bot_token = _env("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        _fail(
            "не задан TELEGRAM_BOT_TOKEN. Скопируйте .env.example в .env "
            "и впишите токен, полученный у @BotFather."
        )

    webapp_port_raw = _env("WEBAPP_PORT", "8000")
    try:
        webapp_port = int(webapp_port_raw)
    except ValueError:
        _fail(f"WEBAPP_PORT должен быть целым числом, получено: {webapp_port_raw!r}")

    return Settings(
        base_dir=BASE_DIR,
        storage_dir=storage_dir,
        uploads_dir=uploads_dir,
        generated_dir=generated_dir,
        builtin_templates_dir=builtin_templates_dir,
        logs_dir=logs_dir,
        db_path=_resolve_db_path(_env("DB_PATH", "storage/app.db")),
        telegram_bot_token=telegram_bot_token,
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        llm_primary=_env("LLM_PRIMARY", "anthropic"),
        llm_fallback=_env("LLM_FALLBACK", "openai"),
        llm_model_primary=_env("LLM_MODEL_PRIMARY"),
        llm_model_fallback=_env("LLM_MODEL_FALLBACK"),
        webapp_url=_env("WEBAPP_URL"),
        webapp_port=webapp_port,
        log_level=_env("LOG_LEVEL", "INFO"),
    )


def _setup_logging(settings: "Settings") -> None:
    """Настраивает логирование одновременно в файл logs/app.log и в консоль."""
    log_file = settings.logs_dir / "app.log"
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


settings = _build_settings()
_setup_logging(settings)
