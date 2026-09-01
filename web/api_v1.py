"""
web/api_v1.py — версионированный API для собственного фронтенда (блок Ф2).

Зачем модуль: Mini App работает в проде на /api/*, и трогать эти
эндпоинты нельзя. Кабинету на Next.js нужен свой префикс, который можно
менять, не ломая Telegram, — /api/v1/*.

Что осознанно не делает: не дублирует core/*. Эндпоинт здесь — тонкая
обёртка: проверил доступ, вызвал функцию из core/, вернул JSON. Никакой
арифметики: числа считает core, иначе бот и кабинет разойдутся.

Чего здесь пока нет: КСП, классы и история — они приезжают своими
блоками (Ф7–Ф9). Сейчас здесь живут /health (Ф2), вход с регистрацией
(Ф4), дэшборд (Ф5) и конспект урока (Ф6).

Правило, которое нельзя нарушать: каждый новый эндпоинт получает
Depends(current_user) или Depends(verify_init_data). Сервер публично
доступен через Cloudflare Tunnel, и эндпоинт без авторизации — дыра.
Исключение ровно одно, /health, и оно перечислено в
web/api_v1.PUBLIC_PATHS, чтобы про него знал и человек, и тест.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, Request
from fastapi.responses import FileResponse, JSONResponse

from bot import texts
from core import accounts
from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import query
from core.limits import WEB_AUDIO_MAX_BYTES
from core.queue import SOURCE_WEB, enqueue
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


async def _сохранить_запись(request: Request, имя_файла: str | None) -> Path:
    """Пишет тело запроса в storage/uploads и возвращает путь.

    Файл идёт потоком и обрывается на превышении предела: клиент, который
    решит прислать гигабайт, не должен ни занять память, ни забить диск.
    Недописанный файл при обрыве удаляется здесь же.
    """
    расширение = _расширение(имя_файла)
    if расширение and расширение not in РАСШИРЕНИЯ_АУДИО:
        raise ApiError(415, CODE_BAD_REQUEST, texts.API_AUDIO_UNSUPPORTED)

    путь = settings.uploads_dir / f"web-{uuid.uuid4().hex}{расширение or '.m4a'}"
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


def _задача_этого_педагога(task_id: str, teacher_id: int | None) -> dict:
    """Задача, если она принадлежит этому педагогу. Иначе 404.

    Чужая задача отдаёт 404, а не 403: мы не подтверждаем даже факт
    существования чужой записи — решение блока Б9.2, оно же действует для
    generated_ksp.
    """
    строки = query("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not строки:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    задача = dict(строки[0])
    try:
        payload = json.loads(задача["payload"]) if задача["payload"] else {}
    except (ValueError, TypeError):
        payload = {}
    if teacher_id is None or payload.get("teacher_id") != teacher_id:
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
    задача = _задача_этого_педагога(task_id, _teacher_id(человек))
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


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/konspekt/{konspekt_id}/docx")
async def konspekt_docx(konspekt_id: str, человек: CurrentUser = Depends(current_user)) -> FileResponse:
    """
    Отдаёт .docx конспекта — тот самый файл, который бот шлёт в чат.

    Собран он был один раз, обработчиком очереди; здесь только отдаётся.
    Пересобирать по запросу нельзя: два пути сборки одного документа
    разойдутся, и педагог получит из веба не то, что уже видел в
    Telegram.

    PDF отсюда не отдаётся: его конвертирует LibreOffice, это десятки
    секунд и внешний процесс — такому место в очереди, а не в обработчике
    запроса. Скачивание PDF приезжает блоком Ф9 вместе с историей.
    """
    teacher_id = _teacher_id(человек)
    строки = query("SELECT teacher_id, tema, docx_path FROM konspekty WHERE id = ?", (konspekt_id,))
    if not строки or teacher_id is None or строки[0]["teacher_id"] != teacher_id:
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)

    путь = Path(строки[0]["docx_path"] or "")
    if not путь.name or not путь.exists():
        # Файл собран, но с диска исчез — честный 404 с тем же текстом:
        # клиенту незачем различать «не ваш» и «потерялся».
        raise ApiError(404, CODE_NOT_FOUND, texts.API_NOT_FOUND)
    return FileResponse(путь, filename=путь.name, media_type=DOCX_MEDIA_TYPE)
