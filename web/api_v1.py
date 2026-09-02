"""
web/api_v1.py — версионированный API для собственного фронтенда (блок Ф2).

Зачем модуль: Mini App работает в проде на /api/*, и трогать эти
эндпоинты нельзя. Кабинету на Next.js нужен свой префикс, который можно
менять, не ломая Telegram, — /api/v1/*.

Что осознанно не делает: не дублирует core/*. Эндпоинт здесь — тонкая
обёртка: проверил доступ, вызвал функцию из core/, вернул JSON. Никакой
арифметики: числа считает core, иначе бот и кабинет разойдутся.

Здесь живут /health (Ф2), вход с регистрацией (Ф4), дэшборд (Ф5),
конспект урока (Ф6), сборка КСП (Ф7), классы (Ф8) и история
документов (Ф9).

Правило, которое нельзя нарушать: каждый новый эндпоинт получает
Depends(current_user) или Depends(verify_init_data). Сервер публично
доступен через Cloudflare Tunnel, и эндпоинт без авторизации — дыра.
Исключение ровно одно, /health, и оно перечислено в
web/api_v1.PUBLIC_PATHS, чтобы про него знал и человек, и тест.
"""

import json
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import asyncio

import httpx
from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from bot import texts
from core import accounts
from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import execute, query
from core.adal_azamat import PROJECTS as ADAL_AZAMAT_PROJECTS
from core.docx_builder import CANONICAL_HOD_UROKA_COLUMNS, COLUMN_LABELS
from core.ksp_generator import (
    FUNCTIONAL_LITERACY_TYPES,
    MAX_VIDY_DEYATELNOSTI,
    TIP_UROKA_OPTIONS,
    WORK_FORMS,
    LessonOptions,
    guess_objective_code,
)
from core.limits import WEB_AUDIO_MAX_BYTES
from core.pdf_export import convert_docx_to_pdf
from core.queue import SOURCE_WEB, enqueue
from core.templates import list_templates
from core.values import VALUES
from web.auth import ROLE_STUDENT, ROLE_TEACHER, CurrentUser, current_user, require_consent
from web.errors import (
    CODE_BAD_REQUEST,
    CODE_CONSENT_REQUIRED,
    CODE_NOT_FOUND,
    CODE_SERVER_UNAVAILABLE,
    ApiError,
    error_body,
)

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


# =====================================================================
# Ф6: конспект урока через веб
#
# Главный сценарий продукта: запись урока -> расшифровка -> конспект.
# Веб-загрузка идёт ТЕМ ЖЕ путём, что и загрузка из Telegram: файл ложится
# в storage/uploads, задача 'transcribe' ставится в ту же очередь, и тот
# же обработчик удаляет аудио сразу после расшифровки. Второго пути, где
# файл сохраняется «на всякий случай», в проекте нет и быть не может —
# это прямое нарушение текста согласия.
# =====================================================================

# Расширения, которые умеет прочитать ffmpeg по дороге в xAI STT. Список
# закрытый: принимать «что угодно» значит принять .exe и узнать об этом
# от воркера через минуту.
РАСШИРЕНИЯ_АУДИО = {".m4a", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".webm", ".mp4", ".aac", ".flac"}

# Сколько байт читаем за раз. Файл не собирается в памяти целиком:
# запись урока — это десятки мегабайт, и держать их в оперативке ради
# одного запроса незачем.
РАЗМЕР_КУСКА = 1024 * 1024


def _расширение(имя: str | None) -> str:
    return Path(имя or "").suffix.lower()


async def _сохранить_запись(
    request: Request, имя_файла: str | None, разрешённые: set[str] | None = None
) -> Path:
    """Пишет тело запроса в storage/uploads и возвращает путь.

    Файл идёт потоком и обрывается на превышении предела: клиент, который
    решит прислать гигабайт, не должен ни занять память, ни забить диск.
    Недописанный файл при обрыве удаляется здесь же.
    """
    допустимые = разрешённые if разрешённые is not None else РАСШИРЕНИЯ_АУДИО
    по_умолчанию = ".jpg" if разрешённые is not None else ".m4a"
    расширение = _расширение(имя_файла)
    if расширение and расширение not in допустимые:
        raise ApiError(
            415, CODE_BAD_REQUEST,
            texts.API_PHOTO_UNSUPPORTED if разрешённые is not None else texts.API_AUDIO_UNSUPPORTED,
        )

    путь = settings.uploads_dir / f"web-{uuid.uuid4().hex}{расширение or по_умолчанию}"
    записано = 0
    try:
        with открыть_на_запись(путь) as файл:
            async for кусок in request.stream():
                записано += len(кусок)
                if записано > WEB_AUDIO_MAX_BYTES:
                    raise ApiError(
                        413, CODE_BAD_REQUEST,
                        texts.API_AUDIO_TOO_LARGE.format(limit_mb=WEB_AUDIO_MAX_BYTES // (1024 * 1024)),
                    )
                файл.write(кусок)
    except BaseException:
        путь.unlink(missing_ok=True)
        raise

    if записано == 0:
        путь.unlink(missing_ok=True)
        raise ApiError(400, CODE_BAD_REQUEST, texts.API_AUDIO_EMPTY)
    return путь


def открыть_на_запись(путь: Path):
    """Отдельная функция ровно затем, чтобы тест мог подменить запись на
    диск, не подменяя весь эндпоинт."""
    return путь.open("wb")


@router.post("/lesson/upload")
async def lesson_upload(
    request: Request,
    человек: CurrentUser = Depends(current_user),
    имя_файла: str | None = Header(default=None, alias="X-Filename"),
    режим: str = Header(default="student", alias="X-Konspekt-Mode"),
) -> dict:
    """
    Принимает запись урока и ставит её в очередь.

    Обработку здесь НЕ запускает: расшифровка занимает десятки секунд, а
    HTTP-запрос, который столько ждёт, обрывается по дороге у первого же
    мобильного оператора. Задача уходит в core/queue.py — ту же очередь,
    что у бота, — и кабинет спрашивает её статус.

    Файл приходит телом запроса, без multipart. Так не понадобилась
    отдельная зависимость ради одного поля, а поток пишется на диск
    кусками и обрывается на превышении предела.
    """
    await require_consent(человек)

    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        raise ApiError(403, CODE_CONSENT_REQUIRED, texts.API_PROFILE_REQUIRED)
    if режим not in {"student", "teacher"}:
        raise ApiError(422, CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason="неизвестный режим обработки"))

    путь = await _сохранить_запись(request, имя_файла)

    # chat_id — чтобы бот сообщил о ходе работы тому, у кого Telegram
    # привязан. Не привязан — None, и обработчик просто ничего не шлёт;
    # человек следит за задачей в кабинете.
    профиль = (
        accounts.find_teacher_by_auth_user(человек.auth_user_id)
        if человек.auth_user_id
        else accounts.find_teacher_by_telegram(человек.telegram_user_id)
    )
    chat_id = профиль.get("telegram_user_id") if профиль else None

    task_id = enqueue(
        "transcribe",
        {
            "teacher_id": teacher_id,
            "audio_paths": [str(путь)],
            "mode": режим,
            "source": SOURCE_WEB,
        },
        chat_id=chat_id,
    )
    return {"task_id": task_id, "status": "queued", "size_bytes": путь.stat().st_size}


# Статусы очереди наружу отдаются как есть: pending и processing — это
# «queued» и «running» на языке кабинета, и переименовывать их в базе
# ради этого не нужно.
СТАТУС_НАРУЖУ = {"pending": "queued", "processing": "running", "done": "done", "failed": "failed"}


def _student_id(человек: CurrentUser) -> int | None:
    """Идентификатор ученика, если человек вошёл как ученик."""
    ученик = (
        accounts.find_student_by_auth_user(человек.auth_user_id)
        if человек.auth_user_id
        else accounts.find_student_by_telegram(человек.telegram_user_id)
    )
    return ученик["id"] if ученик else None


def _задача_этого_педагога(task_id: str, teacher_id: int | None) -> dict:
    """Задача, если она принадлежит этому педагогу. Иначе 404."""
    return _своя_задача(task_id, teacher_id=teacher_id, student_id=None)


def _своя_задача(task_id: str, teacher_id: int | None, student_id: int | None) -> dict:
    """
    Задача, если она принадлежит этому человеку. Иначе 404.

    Владелец берётся из payload: колонки teacher_id в tasks нет. У задач
    педагога это teacher_id, у сверки тетради — student_id: сверку
    заводит ученик, и следить за ней должен он же.

    Чужая задача отдаёт 404, а не 403: не подтверждаем даже факт
    существования чужой записи (решение Б9.2).
    """
    строки = query("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not строки:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    задача = dict(строки[0])
    try:
        payload = json.loads(задача["payload"]) if задача["payload"] else {}
    except (ValueError, TypeError):
        payload = {}

    свой = (
        (teacher_id is not None and payload.get("teacher_id") == teacher_id)
        or (student_id is not None and payload.get("student_id") == student_id)
    )
    if not свой:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    задача["payload_разобран"] = payload
    return задача


@router.get("/task/{task_id}")
async def task_status(task_id: str, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Статус задачи: queued, running, done или failed.

    Провал приходит сюда вместе с причиной — это и есть канал, которым о
    нём узнаёт тот, у кого нет Telegram. Гарантию уведомления в
    core/queue.py это не подменяет и не обходит: она по-прежнему шлёт
    сообщение всем, у кого чат есть.
    """
    задача = _своя_задача(task_id, _teacher_id(человек), _student_id(человек))
    статус = СТАТУС_НАРУЖУ.get(задача["status"], задача["status"])

    ответ: dict = {
        "task_id": task_id,
        "status": статус,
        "type": задача["type"],
        "retries": задача["retries"],
    }
    if статус == "failed":
        # Текст ошибки пишет обработчик, и он уже по-русски: тексты
        # провалов лежат в bot/texts.py, как и всё остальное.
        ответ["error"] = задача["error"] or texts.ERROR_UNEXPECTED
    if статус == "done" and задача["result"]:
        try:
            ответ["result"] = json.loads(задача["result"])
        except (ValueError, TypeError):
            ответ["result"] = None
    return ответ


@router.get("/konspekt/{konspekt_id}")
async def konspekt(konspekt_id: str, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Готовый конспект и расшифровка, по которой он собран.

    Чужой конспект — 404, тем же правилом, что и чужая задача. Текст
    расшифровки отдаётся целиком: его видит только сам педагог, ни
    администрация, ни ученики доступа не имеют.
    """
    teacher_id = _teacher_id(человек)
    строки = query("SELECT * FROM konspekty WHERE id = ?", (konspekt_id,))
    if not строки or teacher_id is None or строки[0]["teacher_id"] != teacher_id:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    запись = dict(строки[0])

    try:
        содержимое = json.loads(запись["content_json"]) if запись["content_json"] else {}
    except (ValueError, TypeError):
        содержимое = {}

    транскрипт = None
    if запись.get("transcript_id"):
        строки_т = query(
            "SELECT text, duration_seconds FROM transcripts WHERE id = ?", (запись["transcript_id"],)
        )
        if строки_т:
            транскрипт = {
                "text": строки_т[0]["text"],
                "duration_seconds": строки_т[0]["duration_seconds"],
            }

    return {
        "konspekt_id": konspekt_id,
        "tema": запись.get("tema"),
        "mode": запись.get("mode"),
        "content": содержимое,
        "transcript": транскрипт,
        "has_docx": bool(запись.get("docx_path")),
    }


# =====================================================================
# Ф7: мастер сборки КСП
#
# Мастер спрашивает то же, что спрашивает бот, и теми же словами. Списки
# вариантов приходят из core/ — придумывать их во фронтенде нельзя: тип
# урока, ценности «Адал азамат» и виды деятельности заданы приказом и
# методичками, а не вкусом верстальщика.
# =====================================================================


@router.get("/ksp/options")
async def ksp_options(человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Всё, из чего мастер строит форму: шаблоны, справочники и порядок
    колонок «Хода урока».

    Порядок колонок отдаётся сервером, а не зашивается в вёрстку.
    Он закреплён приложением 4 приказа МОН РК №130 в редакции от
    30.04.2025 № 98 — оценивание идёт ПЕРЕД ресурсами, — и один раз в
    проекте его уже путали. Единственное место, где он записан, —
    core/docx_builder.py; кабинет обязан показывать тот же.
    """
    teacher_id = _teacher_id(человек)
    # teacher_id=-1 у списка шаблонов означает «профиля нет»: встроенные
    # шаблоны при этом всё равно видны. Контракт старый, менять его тут
    # нельзя — он держит ту же логику в Mini App.
    шаблоны = list_templates(teacher_id if teacher_id is not None else -1)

    return {
        "templates": шаблоны,
        "tip_uroka": TIP_UROKA_OPTIONS,
        "cennosti": [{"key": ключ, "name": знач["name"], "goal": знач["goal"]} for ключ, знач in VALUES.items()],
        "adal_azamat_projects": [
            {"key": ключ, "name": знач["name"], "direction": знач["direction"]}
            for ключ, знач in ADAL_AZAMAT_PROJECTS.items()
        ],
        # Виды деятельности в core/ разложены на две группы — формы
        # работы и виды функциональной грамотности. Кабинет показывает их
        # так же двумя группами, а не одной кучей: это разные вопросы.
        "work_forms": WORK_FORMS,
        "functional_literacy": FUNCTIONAL_LITERACY_TYPES,
        "max_vidy_deyatelnosti": MAX_VIDY_DEYATELNOSTI,
        "hod_uroka_columns": [
            {"key": колонка, "label": COLUMN_LABELS.get(колонка, колонка)}
            for колонка in CANONICAL_HOD_UROKA_COLUMNS
        ],
    }


@router.get("/ktp/entries")
async def ktp_entries(человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Темы из КТП этого педагога — быстрый путь мастера.

    Выбрал тему из своего календарного плана, и раздел с кодом цели
    подставились сами. Ничего не додумываем: чего в КТП нет, того нет и
    в ответе.
    """
    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        return {"entries": []}
    # Колонка раздела в базе называется section — «раздел» это её
    # человеческое имя, и переименовывать её ради красоты незачем.
    строки = query(
        "SELECT id, lesson_number, section, topic, objective_code, hours, planned_date, quarter "
        "FROM ktp_entries WHERE teacher_id = ? ORDER BY id",
        (teacher_id,),
    )
    return {"entries": [dict(строка) for строка in строки]}


@router.get("/ktp/objective")
async def ktp_objective(topic: str, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Код цели обучения по теме урока, если он есть в КТП.

    Ищет тот же core.ksp_generator.guess_objective_code, что и бот:
    сначала точное совпадение темы, потом вхождение подстрокой. Никаких
    эмбеддингов — это этап 3, и он отложен.
    """
    teacher_id = _teacher_id(человек)
    код = guess_objective_code(teacher_id, topic) if teacher_id is not None else None
    return {"objective_code": код}


@router.post("/ksp/generate")
async def ksp_generate(
    topic: str = Body(...),
    razdel: str = Body(...),
    subject: str = Body(...),
    klass: str = Body(...),
    duration_minutes: int = Body(...),
    template_id: int = Body(...),
    objective_code: str | None = Body(default=None),
    ktp_entry_id: int | None = Body(default=None),
    options: dict | None = Body(default=None),
    konspekt_id: str | None = Body(default=None),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Ставит сборку черновика КСП в очередь — ту же, что у бота.

    Здесь не генерируется ничего: генерация занимает от двадцати пяти до
    тридцати четырёх секунд по замерам, плюс ретраи при отказе
    провайдера. Место такому — в очереди.

    Недостающие поля не дописываются заглушками: чего мастер не спросил,
    то уходит пустым, и в документе останется пусто. Придуманный раздел
    хуже пустого — его никто не заметит и не поправит.
    """
    await require_consent(человек)
    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        raise ApiError(403, CODE_CONSENT_REQUIRED, texts.API_PROFILE_REQUIRED)

    # Опции проходят через сам LessonOptions: он же и обрезает список
    # видов деятельности до трёх — «до трёх» это ограничение, а не
    # пожелание, и проверять его во фронтенде значило бы завести вторую
    # копию правила.
    try:
        разобранные = LessonOptions(**(options or {}))
    except TypeError as ошибка:
        raise ApiError(422, CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason=f"настройки урока: {ошибка}")) from None

    konspekt_text = None
    if konspekt_id:
        строки = query(
            "SELECT teacher_id, content_json FROM konspekty WHERE id = ?", (konspekt_id,)
        )
        if not строки or строки[0]["teacher_id"] != teacher_id:
            raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
        try:
            содержимое = json.loads(строки[0]["content_json"] or "{}")
        except (ValueError, TypeError):
            содержимое = {}
        konspekt_text = содержимое.get("transcript_text") or json.dumps(содержимое, ensure_ascii=False)

    профиль = (
        accounts.find_teacher_by_auth_user(человек.auth_user_id)
        if человек.auth_user_id
        else accounts.find_teacher_by_telegram(человек.telegram_user_id)
    )
    chat_id = профиль.get("telegram_user_id") if профиль else None

    task_id = enqueue(
        "generate_ksp",
        {
            "teacher_id": teacher_id,
            "template_id": template_id,
            "topic": topic.strip(),
            "razdel": razdel.strip(),
            "subject": subject.strip(),
            "klass": klass.strip(),
            "duration_minutes": duration_minutes,
            "objective_code": objective_code,
            "ktp_entry_id": ktp_entry_id,
            "options": asdict(разобранные),
            "textbook_photo_paths": [],
            "konspekt_text": konspekt_text,
            "source": SOURCE_WEB,
        },
        chat_id=chat_id,
    )
    return {"task_id": task_id, "status": "queued"}


# =====================================================================
# Ф8: классы и ученики
#
# Массовой рассылки конспекта классу здесь нет и не появится. Это прямое
# решение автора: продукт, раздающий детям полные конспекты, отвечает на
# вопрос «зачем тогда ходить в школу» неправильным образом. Отправка —
# всегда одному ученику и всегда рукой педагога.
# =====================================================================


def _класс_или_404(class_id: int, teacher_id: int | None) -> dict:
    класс = accounts.get_class(class_id, teacher_id) if teacher_id is not None else None
    if класс is None:
        # Чужой класс — 404, а не 403: не подтверждаем даже факт его
        # существования (то же правило, что у generated_ksp, Б9.2).
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    return класс


@router.get("/classes")
async def classes(человек: CurrentUser = Depends(current_user)) -> dict:
    """Классы педагога с числом учеников в каждом."""
    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        return {"classes": []}
    return {"classes": accounts.list_classes(teacher_id)}


@router.post("/classes")
async def create_class(
    name: str = Body(...),
    subject: str | None = Body(default=None),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """Заводит класс и сразу выдаёт код приглашения."""
    await require_consent(человек)
    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        raise ApiError(403, CODE_CONSENT_REQUIRED, texts.API_PROFILE_REQUIRED)
    имя = name.strip()
    if not имя:
        raise ApiError(422, CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason="не заполнено «название класса»"))
    класс = accounts.create_class(teacher_id, имя, (subject or "").strip() or None)
    return {"class": {**класс, "students_count": 0}}


@router.post("/classes/{class_id}/code")
async def reissue_code(class_id: int, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Перевыпуск кода приглашения.

    Старый код перестаёт действовать сразу — ради этого перевыпуск и
    существует: код продиктовали не тому классу или он ушёл дальше, чем
    хотелось.
    """
    await require_consent(человек)
    teacher_id = _teacher_id(человек)
    _класс_или_404(class_id, teacher_id)
    новый = accounts.regenerate_invite_code(class_id, teacher_id)
    return {"invite_code": новый}


@router.delete("/classes/{class_id}")
async def delete_class(class_id: int, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Удаляет класс. Учеников не трогает — только их связь с этим классом.

    Тот же ребёнок может состоять у другого педагога, и стереть его
    вместе с классом значило бы выкинуть чужие данные.
    """
    await require_consent(человек)
    teacher_id = _teacher_id(человек)
    класс = _класс_или_404(class_id, teacher_id)
    accounts.delete_class(class_id, teacher_id)
    return {"deleted": True, "name": класс["name"]}


@router.get("/classes/{class_id}/students")
async def class_students(class_id: int, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Ученики класса: имя, когда вступил, сколько сверок и когда последняя
    активность.

    Больше об ученике не хранится ничего. Ни ИИН, ни фамилии в
    документах, ни даты рождения, ни оценок — их незачем хранить, и
    отдавать отсюда нечего.
    """
    teacher_id = _teacher_id(человек)
    _класс_или_404(class_id, teacher_id)
    ученики = accounts.list_class_students(class_id)
    return {
        "students": [
            {
                "id": ученик["id"],
                "name": ученик["name"],
                "joined_at": ученик["joined_at"],
                "sverki": ученик["sverki"],
                "last_activity": ученик["last_activity"],
                # Отправить конспект можно только тому, у кого есть
                # Telegram: доставляет его бот.
                "can_receive": ученик["telegram_id"] is not None,
            }
            for ученик in ученики
        ]
    }


@router.post("/classes/{class_id}/send-konspekt")
async def send_konspekt(
    class_id: int,
    student_id: int = Body(...),
    konspekt_id: str = Body(...),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Отправляет конспект ОДНОМУ ученику — тому, кто пропустил урок.

    Массовой рассылки классу нет и не будет: это решение автора, а не
    недоделка. Целиком конспект уходит только рукой педагога и только
    адресно.

    Доставляет бот, поэтому ученику нужен Telegram. Файл отправляется
    тот же, что собрал обработчик очереди, — пересобирать по запросу
    нельзя, разойдётся с тем, что педагог уже видел.
    """
    await require_consent(человек)
    teacher_id = _teacher_id(человек)
    _класс_или_404(class_id, teacher_id)

    ученик = accounts.student_in_class(class_id, student_id)
    if ученик is None:
        raise ApiError(404, CODE_NOT_FOUND, texts.SEND_KONSPEKT_STUDENT_NOT_FOUND)
    if not ученик.get("telegram_id"):
        raise ApiError(422, CODE_BAD_REQUEST, texts.SEND_KONSPEKT_STUDENT_NO_TELEGRAM)

    строки = query("SELECT * FROM konspekty WHERE id = ?", (konspekt_id,))
    if not строки or строки[0]["teacher_id"] != teacher_id:
        raise ApiError(404, CODE_NOT_FOUND, texts.SEND_KONSPEKT_NOT_FOUND)
    конспект = dict(строки[0])

    путь = Path(конспект.get("docx_path") or "")
    if not путь.name or not путь.exists():
        raise ApiError(404, CODE_NOT_FOUND, texts.SEND_KONSPEKT_FILE_MISSING)

    профиль = (
        accounts.find_teacher_by_auth_user(человек.auth_user_id)
        if человек.auth_user_id
        else accounts.find_teacher_by_telegram(человек.telegram_user_id)
    )
    подпись = texts.SEND_KONSPEKT_CAPTION.format(
        teacher_name=(профиль or {}).get("name") or "", tema=конспект.get("tema") or ""
    )

    await _отправить_документ_в_telegram(ученик["telegram_id"], путь, подпись)

    имя = ученик.get("name") or texts.SEND_KONSPEKT_STUDENT_NO_NAME.format(id=student_id)
    return {"sent": True, "message": texts.SEND_KONSPEKT_SENT.format(student_name=имя)}


async def _отправить_документ_в_telegram(chat_id: int, путь: Path, подпись: str) -> None:
    """
    Отправка файла ученику через Bot API.

    Напрямую по HTTP, а не через aiogram: у веб-процесса экземпляра бота
    нет, а поднимать его ради одной отправки — значит завести второго
    бота на тот же токен. httpx в проекте уже есть, новой зависимости не
    появилось.
    """
    адрес = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    try:
        with путь.open("rb") as файл:
            async with httpx.AsyncClient(timeout=60) as клиент:
                ответ = await клиент.post(
                    адрес,
                    data={"chat_id": str(chat_id), "caption": подпись},
                    files={"document": (путь.name, файл, DOCX_MEDIA_TYPE)},
                )
        ответ.raise_for_status()
    except httpx.HTTPError:
        # Причину наружу не отдаём: ответ Telegram — не то, что должен
        # читать педагог. В лог она попадёт сама, исключением.
        raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None


# =====================================================================
# Ф9: история документов и скачивание
#
# История — это то, что уже собрано, и то, что собрать не вышло. Второе
# не менее важно первого: провалившаяся задача должна быть видна и
# повторяема, а не теряться молча.
# =====================================================================

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Виды документов, которые в системе действительно есть. КТП сюда не
# входит: он собирается в файл и раскладывается по ktp_entries, но
# отдельной строки документа для него в базе нет. Завести её — значит
# менять схему, а это решение автора.
ВИД_КОНСПЕКТ = "konspekt"
ВИД_КСП = "ksp"

# PDF есть только у конспекта. У КСП его нет и не возвращаем: КСП правят
# перед утверждением и печатают из .docx.
ВИДЫ_С_PDF = {ВИД_КОНСПЕКТ}

# Повторить можно не всё. У расшифровки и сверки исходник — аудио и фото —
# удаляется сразу после обработки, и при провале тоже: повторять нечего.
# Тексты про это уже написаны в боте, здесь они переиспользуются.
ПОВТОР_ЗАПРЕЩЁН = {
    "transcribe": texts.KONSPEKT_TRANSCRIBE_RETRY_DISABLED,
    "sverka_tetradi": texts.SVERKA_RETRY_DISABLED,
}

ЗАГОЛОВКИ_ЗАДАЧ = {
    "generate_ksp": "Черновик КСП",
    "generate_ktp": "КТП на учебный год",
    "generate_konspekt": "Конспект урока",
    "transcribe": "Расшифровка записи",
    "sverka_tetradi": "Сверка тетради",
    "parse_ksp": "Разбор ваших КСП",
}


def _документ(вид: str, doc_id: str, teacher_id: int | None) -> dict:
    """Строка документа, если она принадлежит этому педагогу.

    Чужой документ — 404, а не 403: не подтверждаем даже факт его
    существования. Проверка владения на сервере и всегда: идентификатор
    угадать несложно, и «его же никто не знает» защитой не является.
    """
    таблица = {ВИД_КОНСПЕКТ: "konspekty", ВИД_КСП: "generated_ksp"}.get(вид)
    if таблица is None:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    строки = query(f"SELECT * FROM {таблица} WHERE id = ?", (doc_id,))
    if not строки or teacher_id is None or строки[0]["teacher_id"] != teacher_id:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    return dict(строки[0])


@router.get("/history")
async def history(
    kind: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Всё, что педагог собрал, и всё, что собрать не вышло.

    Конспекты и КСП идут из своих таблиц, провалившиеся задачи — из
    очереди, и всё складывается в один список по времени: человек ищет
    «что я делал в понедельник», а не «покажи таблицу konspekty».

    Фильтры — по виду документа и по датам. Фильтра по классу нет: связи
    «документ → класс» в базе не существует, и рисовать её из воздуха
    значило бы приписать урок не тому классу.
    """
    teacher_id = _teacher_id(человек)
    if teacher_id is None:
        return {"items": [], "counts": {"konspekt": 0, "ksp": 0, "failed": 0}}

    записи: list[dict] = []

    конспекты = query(
        "SELECT k.id, k.tema, k.mode, k.docx_path, k.created_at, t.duration_seconds, "
        "       e.objective_code "
        "FROM konspekty k "
        "LEFT JOIN transcripts t ON t.id = k.transcript_id "
        "LEFT JOIN ktp_entries e ON e.id = k.ktp_entry_id "
        "WHERE k.teacher_id = ? ORDER BY k.created_at DESC",
        (teacher_id,),
    )
    for строка in конспекты:
        записи.append({
            "kind": ВИД_КОНСПЕКТ,
            "id": строка["id"],
            "title": строка["tema"] or "Конспект урока",
            "objective_code": строка["objective_code"],
            "created_at": строка["created_at"],
            "status": "ready",
            "duration_seconds": строка["duration_seconds"],
            "has_docx": bool(строка["docx_path"]),
            "has_pdf": bool(строка["docx_path"]),
        })

    ксп = query(
        "SELECT g.id, g.created_at, g.docx_path, e.topic, e.objective_code "
        "FROM generated_ksp g LEFT JOIN ktp_entries e ON e.id = g.ktp_entry_id "
        "WHERE g.teacher_id = ? ORDER BY g.created_at DESC",
        (teacher_id,),
    )
    for строка in ксп:
        записи.append({
            "kind": ВИД_КСП,
            "id": строка["id"],
            "title": строка["topic"] or "Черновик КСП",
            "objective_code": строка["objective_code"],
            "created_at": строка["created_at"],
            # Слово «черновик» обязательно везде, где речь о
            # сгенерированном документе.
            "status": "draft",
            "duration_seconds": None,
            "has_docx": bool(строка["docx_path"]),
            "has_pdf": False,
        })

    провалы = query(
        "SELECT id, type, error, created_at, payload FROM tasks "
        "WHERE status = 'failed' ORDER BY created_at DESC"
    )
    for строка in провалы:
        try:
            payload = json.loads(строка["payload"]) if строка["payload"] else {}
        except (ValueError, TypeError):
            payload = {}
        if payload.get("teacher_id") != teacher_id:
            continue
        записи.append({
            "kind": "failed",
            "id": строка["id"],
            "title": payload.get("topic") or ЗАГОЛОВКИ_ЗАДАЧ.get(строка["type"], строка["type"]),
            "objective_code": payload.get("objective_code"),
            "created_at": строка["created_at"],
            "status": "failed",
            "task_type": строка["type"],
            "error": строка["error"],
            "can_retry": строка["type"] not in ПОВТОР_ЗАПРЕЩЁН,
            "has_docx": False,
            "has_pdf": False,
        })

    if kind in {ВИД_КОНСПЕКТ, ВИД_КСП, "failed"}:
        записи = [з for з in записи if з["kind"] == kind]
    if since:
        записи = [з for з in записи if str(з["created_at"] or "")[:10] >= since]
    if until:
        записи = [з for з in записи if str(з["created_at"] or "")[:10] <= until]

    записи.sort(key=lambda з: str(з["created_at"] or ""), reverse=True)
    return {
        "items": записи,
        "counts": {
            "konspekt": len(конспекты),
            "ksp": len(ксп),
            "failed": sum(1 for з in записи if з["kind"] == "failed"),
        },
    }


@router.get("/download/{kind}/{doc_id}")
async def download(
    kind: str,
    doc_id: str,
    format: str = Query(default="docx"),
    человек: CurrentUser = Depends(current_user),
) -> FileResponse:
    """
    Отдаёт готовый файл документа.

    Файл собран один раз обработчиком очереди — здесь он только
    отдаётся. Пересобирать по запросу нельзя: два пути сборки одного
    документа разойдутся, и педагог получит из веба не то, что уже видел
    в Telegram.

    PDF есть только у конспекта и делается из того же .docx силами
    LibreOffice. У КСП PDF нет и не будет.
    """
    документ = _документ(kind, doc_id, _teacher_id(человек))
    путь = Path(документ.get("docx_path") or "")
    if not путь.name or not путь.exists():
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)

    if format == "docx":
        return FileResponse(путь, filename=путь.name, media_type=DOCX_MEDIA_TYPE)
    if format != "pdf":
        raise ApiError(422, CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason="неизвестный формат файла"))
    if kind not in ВИДЫ_С_PDF:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)

    pdf = путь.with_suffix(".pdf")
    if not pdf.exists():
        # Конвертация — внешний процесс LibreOffice на несколько секунд.
        # В отдельном потоке: сервер однопоточный и на это время перестал
        # бы отвечать всем остальным.
        try:
            pdf = await asyncio.to_thread(convert_docx_to_pdf, путь)
        except Exception:
            # LibreOffice может быть не установлен — это не повод падать.
            raise ApiError(503, CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE) from None
    return FileResponse(pdf, filename=pdf.name, media_type="application/pdf")


@router.post("/task/{task_id}/retry")
async def retry_task(task_id: str, человек: CurrentUser = Depends(current_user)) -> dict:
    """
    Повторяет провалившуюся задачу — той же очередью.

    Повторить можно не всё: у расшифровки и сверки исходник удаляется
    сразу после обработки. В таком случае возвращается тот же текст, что
    говорит бот, — с объяснением и следующим шагом, а не сухим отказом.
    """
    await require_consent(человек)
    задача = _задача_этого_педагога(task_id, _teacher_id(человек))
    if задача["status"] != "failed":
        raise ApiError(422, CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason="эта задача не провалена"))

    запрет = ПОВТОР_ЗАПРЕЩЁН.get(задача["type"])
    if запрет:
        raise ApiError(422, CODE_BAD_REQUEST, запрет)

    # Счётчик попыток обнуляется: это новая попытка по решению человека,
    # а не продолжение прежней серии автоматических ретраев.
    execute("UPDATE tasks SET status = 'pending', retries = 0, error = NULL WHERE id = ?", (task_id,))
    return {"task_id": task_id, "status": "queued"}


# =====================================================================
# Ф10: экран ученика — сверка тетради
#
# Ученик получает РАЗНИЦУ: чего в его тетради нет против записи урока.
# Полной расшифровки и полного конспекта он не получает ни при каком
# действии — целиком конспект уходит только если его отправил учитель,
# вручную. Это условие допуска в школу (MASTER.md 0.9 п.3), а не
# пожелание, и держится оно на коде, а не на формулировке промпта.
# =====================================================================

# Тот же предел, что у записи урока: фото тетради весит куда меньше, но
# отдельное число здесь означало бы второе место, где его правят.
СНИМКОВ_ТЕТРАДИ_ЗА_РАЗ = 1

РАСШИРЕНИЯ_ФОТО = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


@router.get("/student/classes")
async def student_classes(человек: CurrentUser = Depends(current_user)) -> dict:
    """Классы, в которых состоит ученик."""
    student_id = _student_id(человек)
    if student_id is None:
        return {"classes": []}
    строки = query(
        "SELECT c.id, c.name, c.subject, t.name AS teacher_name, c.teacher_id "
        "FROM class_members m JOIN classes c ON c.id = m.class_id "
        "JOIN teachers t ON t.id = c.teacher_id "
        "WHERE m.student_id = ? ORDER BY m.joined_at",
        (student_id,),
    )
    return {"classes": [dict(строка) for строка in строки]}


@router.get("/student/lessons")
async def student_lessons(
    class_id: int = Query(...),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Уроки, с которыми можно сверить тетрадь.

    Отдаётся только то, что нужно для выбора: дата, тема и домашнее
    задание. **Текста расшифровки здесь нет и быть не может** — ученик не
    получает её ни при каком действии.

    Домашнее задание — исключение, названное в макете отдельным блоком:
    это одна строка из конспекта, а не конспект.
    """
    student_id = _student_id(человек)
    if student_id is None:
        return {"lessons": []}

    # Класс обязан быть своим: идентификатор пришёл от клиента.
    свой = query(
        "SELECT c.teacher_id FROM class_members m JOIN classes c ON c.id = m.class_id "
        "WHERE m.student_id = ? AND c.id = ?",
        (student_id, class_id),
    )
    if not свой:
        raise ApiError(404, CODE_NOT_FOUND, texts.SVERKA_LESSON_NOT_FOUND)
    teacher_id = свой[0]["teacher_id"]

    строки = query(
        "SELECT t.id, t.created_at, k.tema, k.content_json "
        "FROM transcripts t LEFT JOIN konspekty k ON k.transcript_id = t.id "
        "WHERE t.teacher_id = ? ORDER BY t.created_at DESC LIMIT 12",
        (teacher_id,),
    )
    уроки = []
    for строка in строки:
        домашнее = None
        if строка["content_json"]:
            try:
                содержимое = json.loads(строка["content_json"])
                домашнее = (содержимое.get("konspekt_uchenika") or {}).get("domashnee_zadanie") or None
            except (ValueError, TypeError, AttributeError):
                домашнее = None
        уроки.append({
            "transcript_id": строка["id"],
            "created_at": строка["created_at"],
            "topic": строка["tema"] or texts.SVERKA_LESSON_NO_TOPIC,
            "homework": домашнее,
        })
    return {"lessons": уроки}


@router.post("/student/sverka")
async def student_sverka(
    request: Request,
    transcript_id: str = Header(..., alias="X-Transcript-Id"),
    имя_файла: str | None = Header(default=None, alias="X-Filename"),
    человек: CurrentUser = Depends(current_user),
) -> dict:
    """
    Принимает фото тетради и ставит сверку в очередь.

    Фото удаляется сразу после сверки — и при успехе, и при провале, это
    зашито в обработчик очереди. Веб идёт тем же путём, что Telegram:
    второго пути, где фото сохраняется, в проекте нет.

    Оценка по результату не ставится и ставиться не будет: рукописная
    кириллица распознаётся на 30–70%, и этой точности хватает на
    подсказку, но не на суждение о человеке.
    """
    await require_consent(человек)
    student_id = _student_id(человек)
    if student_id is None:
        raise ApiError(403, CODE_CONSENT_REQUIRED, texts.SVERKA_NOT_A_STUDENT)

    # Урок обязан принадлежать педагогу одного из классов ЭТОГО ученика:
    # transcript_id пришёл от клиента, доверять ему нельзя.
    свои_педагоги = {
        строка["teacher_id"] for строка in query(
            "SELECT c.teacher_id FROM class_members m JOIN classes c ON c.id = m.class_id "
            "WHERE m.student_id = ?",
            (student_id,),
        )
    }
    урок = query("SELECT teacher_id FROM transcripts WHERE id = ?", (transcript_id,))
    if not урок or урок[0]["teacher_id"] not in свои_педагоги:
        raise ApiError(404, CODE_NOT_FOUND, texts.SVERKA_LESSON_NOT_FOUND)

    расширение = _расширение(имя_файла)
    if расширение and расширение not in РАСШИРЕНИЯ_ФОТО:
        raise ApiError(415, CODE_BAD_REQUEST, texts.API_PHOTO_UNSUPPORTED)
    # Имя без расширения дальше подставит .jpg — фото с телефона иначе и
    # не приходит, а разрешать что угодно нельзя.
    путь = await _сохранить_запись(request, имя_файла or "tetrad.jpg", разрешённые=РАСШИРЕНИЯ_ФОТО)

    ученик = query("SELECT telegram_id FROM students WHERE id = ?", (student_id,))
    chat_id = ученик[0]["telegram_id"] if ученик else None

    task_id = enqueue(
        "sverka_tetradi",
        {
            "student_id": student_id,
            "transcript_id": transcript_id,
            "photo_path": str(путь),
            "source": SOURCE_WEB,
        },
        chat_id=chat_id,
    )
    return {"task_id": task_id, "status": "queued"}
