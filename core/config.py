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
    db_backend: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    # Ф3: общий секрет, которым Supabase Auth подписывает JWT кабинета.
    # Не задан — вход по почте просто не работает; «пропустить на всякий
    # случай» здесь недопустимо (web/jwt.py).
    supabase_jwt_secret: str | None

    telegram_bot_token: str

    # Имя бота без «@» — из него собирается ссылка t.me/<имя>?start=... для
    # входа в кабинет подтверждением в боте (core/login_codes.py). Токен
    # для этого не годится: в ссылке нужно публичное имя, а не секрет. Не
    # задано — вход через бота честно отвечает, что не настроен, и
    # остальные двери работают как работали.
    telegram_bot_name: str | None

    # Цепочка провайдеров LLM, в порядке попыток (core/llm_client.py, блок Б2).
    # llm_providers[имя] = {"api_key": ..., "model": ..., "base_url": ...}.
    # base_url может быть None — тогда llm_client.py берёт свой дефолт.
    llm_provider_order: tuple[str, ...]
    llm_providers: dict[str, dict[str, str | None]]

    webapp_url: str | None
    webapp_port: int

    # Ф2 (FRONTEND_PLAN.md): откуда браузеру разрешено дёргать /api/v1/*.
    # Список живёт здесь, а не в web/api.py, чтобы менять его правкой .env,
    # а не кода: адрес на Vercel появится позже, домен — ещё позже.
    cors_origins: tuple[str, ...]

    # Ф14: экран аналитики продукта. Выключен по умолчанию — случайно
    # открытый доступ к нему это инцидент, а не мелочь. Даже включённый,
    # он открывается только аккаунтам из admin_access.
    analytics_enabled: bool

    # М7.1 (PLAN_STAGE2.md): куда слать уведомление о завершившемся
    # инциденте живучести (core/incidents.py). Не задан — некому слать,
    # бот не падает из-за этого (тот же принцип, что webapp_url).
    admin_telegram_chat_id: int | None
    admin_password: str | None

    stt_backend: str
    xai_api_key: str | None
    stt_language: str

    log_level: str


def _resolve_db_path(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


DEFAULT_LLM_PROVIDER_ORDER = ("deepseek", "gemini", "grok", "openai", "anthropic")

# Кабинет в разработке живёт на localhost:3000 (next dev). Боевой адрес на
# Vercel дописывается в CORS_ORIGINS через запятую, когда появится.
DEFAULT_CORS_ORIGINS = ("http://localhost:3000",)


def _parse_cors_origins(raw: str | None) -> tuple[str, ...]:
    """Разбирает CORS_ORIGINS ("https://x.vercel.app,http://localhost:3000").

    Пустая переменная — не ошибка: остаётся только адрес разработки.
    Звёздочка запрещена намеренно: сервер публично доступен через
    Cloudflare Tunnel, и «разрешить всем» здесь означало бы разрешить
    любому сайту дёргать API из браузера вошедшего человека.

    Завершающий слэш срезается. Браузер присылает в Origin голый адрес
    без пути ("https://x.vercel.app"), поэтому запись со слэшем на конце
    не совпадает НИКОГДА — и выглядит это не как отказ, а как «сайт
    открылся, но ни одна кнопка не работает». Ровно на это ушёл день
    02.09.2026: в CORS_ORIGINS стоял адрес со слэшем.
    """
    if not raw:
        return DEFAULT_CORS_ORIGINS
    origins = tuple(part.strip().rstrip("/") for part in raw.split(",") if part.strip().rstrip("/"))
    if "*" in origins:
        _fail("CORS_ORIGINS не может содержать '*' — перечислите адреса явно")
    return origins or DEFAULT_CORS_ORIGINS


def _parse_provider_order(raw: str | None) -> tuple[str, ...]:
    """Разбирает LLM_PROVIDERS ("deepseek,gemini,...") в кортеж имён.

    Пустая или отсутствующая переменная — не ошибка, просто дефолтный
    порядок. Список расширяемый: новое имя здесь не требует правки этого
    модуля, только core/llm_client.py должен знать протокол этого имени.
    """
    if not raw:
        return DEFAULT_LLM_PROVIDER_ORDER
    names = [part.strip().lower() for part in raw.split(",")]
    return tuple(name for name in names if name)


def _build_provider_settings(order: tuple[str, ...]) -> dict[str, dict[str, str | None]]:
    """Для каждого провайдера из цепочки читает {ИМЯ}_API_KEY/{ИМЯ}_MODEL/{ИМЯ}_BASE_URL.

    Провайдер без api_key не считается ошибкой конфигурации — core/llm_client.py
    просто пропускает его в цепочке (не настроен, это штатно).
    """
    providers: dict[str, dict[str, str | None]] = {}
    for name in order:
        prefix = name.upper()
        providers[name] = {
            "api_key": _env(f"{prefix}_API_KEY"),
            "model": _env(f"{prefix}_MODEL"),
            "base_url": _env(f"{prefix}_BASE_URL"),
        }
    return providers


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

    llm_provider_order = _parse_provider_order(_env("LLM_PROVIDERS"))

    stt_backend = (_env("STT_BACKEND", "xai") or "xai").lower()
    if stt_backend != "xai":
        _fail("STT_BACKEND должен быть 'xai': локальная расшифровка удалена")

    db_backend = (_env("DB_BACKEND", "sqlite") or "sqlite").lower()
    if db_backend not in {"sqlite", "supabase"}:
        _fail("DB_BACKEND должен быть 'sqlite' или 'supabase'")
    supabase_url = _env("SUPABASE_URL")
    supabase_service_role_key = _env("SUPABASE_SERVICE_ROLE_KEY")
    if db_backend == "supabase" and (not supabase_url or not supabase_service_role_key):
        _fail("для DB_BACKEND=supabase нужны SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY")

    admin_chat_id_raw = _env("ADMIN_TELEGRAM_CHAT_ID")
    admin_telegram_chat_id = None
    if admin_chat_id_raw:
        try:
            admin_telegram_chat_id = int(admin_chat_id_raw)
        except ValueError:
            _fail(f"ADMIN_TELEGRAM_CHAT_ID должен быть целым числом, получено: {admin_chat_id_raw!r}")

    return Settings(
        base_dir=BASE_DIR,
        storage_dir=storage_dir,
        uploads_dir=uploads_dir,
        generated_dir=generated_dir,
        builtin_templates_dir=builtin_templates_dir,
        logs_dir=logs_dir,
        db_path=_resolve_db_path(_env("DB_PATH", "storage/app.db")),
        db_backend=db_backend,
        supabase_url=supabase_url,
        supabase_service_role_key=supabase_service_role_key,
        supabase_jwt_secret=_env("SUPABASE_JWT_SECRET"),
        telegram_bot_token=telegram_bot_token,
        telegram_bot_name=(_env("TELEGRAM_BOT_NAME") or "").lstrip("@") or None,
        llm_provider_order=llm_provider_order,
        llm_providers=_build_provider_settings(llm_provider_order),
        webapp_url=_env("WEBAPP_URL"),
        webapp_port=webapp_port,
        cors_origins=_parse_cors_origins(_env("CORS_ORIGINS")),
        analytics_enabled=(_env("ANALYTICS_ENABLED", "0") or "0").lower() in {"1", "true", "yes"},
        admin_telegram_chat_id=admin_telegram_chat_id,
        admin_password=_env("ADMIN_PASSWORD"),
        stt_backend=stt_backend,
        xai_api_key=_env("XAI_API_KEY"),
        stt_language=_env("STT_LANGUAGE", "ru"),
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
    # М0.2: httpx на уровне INFO логирует полный URL каждого запроса. У
    # Gemini ключ теперь передаётся заголовком (core/llm_client.py, М0.1),
    # но это защита первого уровня — уровень логирования снижаем и здесь,
    # в единой точке настройки логирования, а не в bot/main.py, чтобы
    # защита не зависела от того, кто именно импортировал core.config
    # (бот, веб-API, скрипт миграции, ручной прогон в консоли).
    logging.getLogger("httpx").setLevel(logging.WARNING)


settings = _build_settings()
_setup_logging(settings)
