"""
web/api_v1.py — версионированный API для собственного фронтенда (блок Ф2).

Зачем модуль: Mini App работает в проде на /api/*, и трогать эти
эндпоинты нельзя. Кабинету на Next.js нужен свой префикс, который можно
менять, не ломая Telegram, — /api/v1/*.

Что осознанно не делает: не дублирует core/*. Эндпоинт здесь — тонкая
обёртка: проверил доступ, вызвал функцию из core/, вернул JSON. Никакой
арифметики: числа считает core, иначе бот и кабинет разойдутся.

Чего здесь пока нет: конспект, КСП, классы и история — они приезжают
своими блоками (Ф6–Ф9). Сейчас здесь живут /health (Ф2), вход с
регистрацией (Ф4) и дэшборд (Ф5).

Правило, которое нельзя нарушать: каждый новый эндпоинт получает
Depends(current_user) или Depends(verify_init_data). Сервер публично
доступен через Cloudflare Tunnel, и эндпоинт без авторизации — дыра.
Исключение ровно одно, /health, и оно перечислено в
web/api_v1.PUBLIC_PATHS, чтобы про него знал и человек, и тест.
"""

from datetime import datetime

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from bot import texts
from core import accounts
from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import query
from web.auth import ROLE_STUDENT, ROLE_TEACHER, CurrentUser, current_user, require_consent
from web.errors import CODE_NOT_FOUND, CODE_SERVER_UNAVAILABLE, ApiError, error_body

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


# =====================================================================
# Ф4: вход, согласие и регистрация
#
# Порядок в каждом действии один и тот же: опознали человека
# (current_user), проверили согласие (require_consent), позвали функцию
# из core/accounts.py, вернули JSON. Ни одной формулы здесь нет — они не
# нужны, а если появятся, значит логика уехала не туда.
# =====================================================================


@router.get("/me")
async def me(человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Кто вошёл, что он уже принял и есть ли у него профиль.

    Это первый запрос кабинета после входа: по нему фронтенд решает, что
    показать — согласие, регистрацию, дэшборд педагога или экран ученика.
    Решает по ответу сервера, а не по собственным догадкам.
    """
    согласие = accounts.has_given_consent(
        telegram_user_id=человек.telegram_user_id, auth_user_id=человек.auth_user_id
    )
    профиль = None
    if человек.role == ROLE_TEACHER:
        строка = (
            accounts.find_teacher_by_auth_user(человек.auth_user_id)
            if человек.auth_user_id
            else accounts.find_teacher_by_telegram(человек.telegram_user_id)
        )
        if строка:
            профиль = {
                "name": строка.get("name"),
                "subject": строка.get("subject"),
                "school": строка.get("school"),
                "city": строка.get("city"),
            }
    return {
        "user_id": человек.user_id,
        "telegram_user_id": человек.telegram_user_id,
        "role": человек.role,
        "consent_given": согласие,
        "profile": профиль,
    }


@router.post("/consent")
async def consent(человек: CurrentUser = Depends(current_user)) -> dict:
    """Записывает согласие. Текст показывает фронтенд — он один и тот же
    у бота и у веба (bot/texts.py, CONSENT_TEXT и STUDENT_CONSENT_TEXT)."""
    accounts.record_consent(
        telegram_user_id=человек.telegram_user_id, auth_user_id=человек.auth_user_id
    )
    return {"consent_given": True}


@router.post("/teacher")
async def register_teacher(
    name: str = Body(...),
    subject: str = Body(...),
    school: str | None = Body(default=None),
    city: str | None = Body(default=None),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Заводит профиль педагога — тем же путём, что /teacher в боте.

    Согласие проверяется до записи, а не после: человек, не принявший
    условия, не должен оставить о себе строку в базе.
    """
    await require_consent(человек)
    профиль = accounts.create_teacher(
        name=name.strip(),
        subject=subject.strip(),
        school=(school or "").strip() or None,
        city=(city or "").strip() or None,
        auth_user_id=человек.auth_user_id,
        telegram_user_id=человек.telegram_user_id if человек.auth_user_id is None else None,
    )
    return {
        "role": ROLE_TEACHER,
        "profile": {
            "name": профиль.get("name"),
            "subject": профиль.get("subject"),
            "school": профиль.get("school"),
            "city": профиль.get("city"),
        },
    }


@router.post("/class/preview")
async def class_preview(
    code: str = Body(..., embed=True),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Что за класс скрыт за кодом приглашения.

    Отдельный шаг перед вступлением: ребёнок услышал код вслух и должен
    увидеть, куда именно вступает, — «класс такой-то, педагог такой-то».
    Ошибочный код здесь и заканчивается: 404 с текстом «код не найден» и
    без выброса в меню (требование блока У3, дословно).
    """
    класс = accounts.find_class_by_invite_code(code)
    if класс is None:
        raise ApiError(404, CODE_NOT_FOUND, texts.STUDENT_JOIN_CODE_NOT_FOUND)
    return {"class_name": класс["name"], "teacher_name": класс["teacher_name"]}


@router.post("/class/join")
async def class_join(
    code: str = Body(..., embed=True),
    name: str | None = Body(default=None),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Вступление в класс по коду.

    Об ученике сохраняется только имя и идентификатор. Ни ИИН, ни
    фамилии в документах, ни даты рождения, ни оценок — их незачем
    хранить, и место под них здесь не предусмотрено.
    """
    await require_consent(человек)
    класс = accounts.find_class_by_invite_code(code)
    if класс is None:
        raise ApiError(404, CODE_NOT_FOUND, texts.STUDENT_JOIN_CODE_NOT_FOUND)

    ученик = accounts.ensure_student(
        name=(name or "").strip() or None,
        auth_user_id=человек.auth_user_id,
        telegram_id=человек.telegram_user_id if человек.auth_user_id is None else None,
    )
    вступил = accounts.join_class(класс["id"], ученик["id"])
    сообщение = (
        texts.STUDENT_JOIN_SUCCESS.format(class_name=класс["name"], teacher_name=класс["teacher_name"])
        if вступил
        else texts.STUDENT_JOIN_ALREADY_MEMBER.format(class_name=класс["name"])
    )
    return {
        "role": ROLE_STUDENT,
        "joined": вступил,
        "class_name": класс["name"],
        "teacher_name": класс["teacher_name"],
        "message": сообщение,
    }


# =====================================================================
# Ф5: дэшборд
# =====================================================================


def _teacher_id(человек: CurrentUser) -> int | None:
    """Идентификатор профиля педагога, либо None — профиля ещё нет.

    None здесь не ошибка, а нормальное состояние: человек вошёл, но
    /teacher ещё не заполнил. core.dashboard.collect() это знает и
    возвращает общее, а не пустоту — ранний выход «профиля нет, показать
    нечего» уже был ошибкой в Mini App, повторять её нельзя.
    """
    профиль = (
        accounts.find_teacher_by_auth_user(человек.auth_user_id)
        if человек.auth_user_id
        else accounts.find_teacher_by_telegram(человек.telegram_user_id)
    )
    return профиль["id"] if профиль else None


@router.get("/dashboard")
async def dashboard(человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Дэшборд педагога.

    Отдаёт РОВНО то, что вернул core.dashboard.collect(), плюс время
    сборки. Ни одного числа здесь не считается и не переформатируется:
    бот и кабинет обязаны показывать одинаковое, а считает это один
    модуль. Появится тут сложение — разойдутся при первой же правке.

    Время сборки идёт двумя полями: машинным ISO и готовой подписью
    «ЧЧ:ММ». Подпись собирает сервер, потому что показать надо время тех
    данных, которые он отдал, а не момент, когда браузер это нарисовал.
    """
    собрано = datetime.now()
    данные = collect_dashboard(_teacher_id(человек))
    return {
        **данные,
        "generated_at": собрано.isoformat(timespec="seconds"),
        "generated_at_label": собрано.strftime("%H:%M"),
    }
