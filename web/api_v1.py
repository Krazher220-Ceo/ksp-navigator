"""
web/api_v1.py — версионированный API для собственного фронтенда (блок Ф2).

Зачем модуль: Mini App работает в проде на /api/*, и трогать эти
эндпоинты нельзя. Кабинету на Next.js нужен свой префикс, который можно
менять, не ломая Telegram, — /api/v1/*.

Что осознанно не делает: не дублирует core/*. Эндпоинт здесь — тонкая
обёртка: проверил доступ, вызвал функцию из core/, вернул JSON. Никакой
арифметики: числа считает core, иначе бот и кабинет разойдутся.

Чего здесь пока нет: всё, кроме /health. Дэшборд, конспект, КСП, классы
и история приезжают своими блоками (Ф5–Ф9) — этот блок кладёт под них
префикс, единый формат ошибки и CORS.

Правило, которое нельзя нарушать: каждый новый эндпоинт получает
Depends(current_user) или Depends(verify_init_data). Сервер публично
доступен через Cloudflare Tunnel, и эндпоинт без авторизации — дыра.
Исключение ровно одно, /health, и оно перечислено в
web/api_v1.PUBLIC_PATHS, чтобы про него знал и человек, и тест.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from bot import texts
from core.config import settings
from core.db import query
from web.errors import CODE_SERVER_UNAVAILABLE, error_body

API_VERSION = "v1"

router = APIRouter(prefix="/api/v1")

# Единственный путь без авторизации. Список закрытый: он же — белый
# список для теста, который следит, что остальные эндпоинты защищены.
PUBLIC_PATHS = frozenset({"/api/v1/health"})


def _database_state() -> tuple[bool, str]:
    """Доступна ли база и какой у неё бэкенд.

    Проверка нарочно самая дешёвая из осмысленных: SELECT 1 доходит до
    настоящего соединения и у SQLite, и у Supabase через RPC, но не
    трогает данные и не зависит от того, заведён ли хоть один профиль.
    """
    try:
        query("SELECT 1 AS ok")
    except Exception:
        # Причину наружу не отдаём: адрес и текст ошибки базы — не то,
        # что стоит показывать в публичном эндпоинте. В лог она попадёт
        # там, где случилась.
        return False, settings.db_backend
    return True, settings.db_backend


@router.get("/health")
async def health() -> JSONResponse:
    """Ф2: живость сервера. Без авторизации — это точка мониторинга.

    База недоступна — 503, чтобы watchdog заметил по коду состояния, а
    человек прочитал в теле готовый русский текст.
    """
    available, backend = _database_state()
    тело = {
        "api_version": API_VERSION,
        "database": {"available": available, "backend": backend},
    }
    if not available:
        тело.update(error_body(CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE))
        return JSONResponse(status_code=503, content=тело)
    return JSONResponse(status_code=200, content=тело)
