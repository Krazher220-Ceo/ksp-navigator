"""
bot/handlers.py — все команды Telegram-бота (блок Б8).

Зачем модуль: единственное место, где Telegram-сообщения превращаются в
вызовы core/*. Никакой бизнес-логики здесь нет — парсинг, генерация,
работа с БД целиком в core/, здесь только маршрутизация и диалоги (FSM).

Что осознанно не делает: не содержит ни одной строки текста напрямую —
все формулировки в bot/texts.py (PLAN_STAGE1.md, Б8.1), редактировать
их можно не трогая логику. /upload_ktp обрабатывается СИНХРОННО (не
через очередь): это быстрый разбор файла без LLM. /generate_ktp,
наоборот, идёт через очередь (третий тип задачи, tasks.type, блок Р4.3,
PLAN_STAGE1_EXT.md, миграция — scripts/migrate_add_generate_ktp_task_type.py) —
это генерация через LLM, может занимать больше минуты, ей нужны ретраи
и гарантия уведомления, тем же путём, что generate_ksp.

На что опирается: aiogram 3 (Router, FSM), core.db, core.ksp_parser,
core.ktp_parser, core.templates, core.ksp_generator, core.ktp_generator,
core.textbook_ocr, core.queue. Хендлеры задач очереди
(parse_ksp/generate_ksp/generate_ktp) — фабрики, которым нужен экземпляр
Bot для отправки файлов/сообщений; создаются в bot/main.py, где Bot уже
существует. Распознавание фото учебника (Р6.1) — тоже внутри хендлера
задачи generate_ksp, не отдельным типом задачи: это подготовительный шаг
перед генерацией, а не самостоятельная работа.
"""

import asyncio
import json
import logging
import re
import secrets
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from bot import keyboards, texts
from bot.navigation import go_back, go_to
from bot.states import (
    ClassCreate,
    Generate,
    GenerateKTP,
    Konspekt,
    StudentJoin,
    SverkaCheck,
    TeacherProfile,
    UploadKSP,
    UploadKTP,
    UploadTemplate,
)
from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import SupabaseDatabaseError, execute, query
from core.generation_defaults import collect as collect_generation_defaults
from core.limits import (
    DAILY_COUNT_LIMITS, LimitExceeded, check_count_limit, check_token_limit,
    get_usage_today, grant_admin_access, record_student_usage, record_usage,
)
from core.konspekt_compare import KonspektCompareError, compare_notebook_to_transcript
from core.ksp_generator import (
    MAX_VIDY_DEYATELNOSTI,
    LessonOptions,
    generate_and_save_ksp,
    guess_objective_code,
)
from core.ksp_parser import (
    KSPConversionError,
    KSPParseError,
    build_style_profile,
    parse_ksp,
    save_style_profile,
)
from core.ktp_generator import generate_and_save_ktp
from core.ktp_parser import KTPParseError, parse_ktp_file, save_ktp_entries
from core.llm_client import LLMClient, LLMError
from core.konspekt_builder import build_konspekt_docx, build_konspekt_filename
from core.konspekt_generator import CELI_NOT_STATED_NOTE, KonspektGenerationError, generate_konspekt
from core.transcriber import TranscriptionError, probe_duration_seconds, transcribe
from core.pdf_export import PdfExportError, convert_docx_to_pdf
from core.queue import MAX_RETRIES, enqueue
from core.templates import get_template, list_templates, save_user_template
from core.textbook_ocr import TextbookOCRError, recognize_textbook_page
from core.adal_azamat import find_project_key_by_name, get_project, list_projects
from core.values import find_value_key_by_name, get_value, list_values

logger = logging.getLogger(__name__)

router = Router()

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MIN_KSP_FILES = 2
MAX_KSP_FILES = 5
SUPPORTED_KSP_EXTENSIONS = {".doc", ".docx"}
SUPPORTED_KTP_EXTENSIONS = {".docx", ".xlsx"}
TEMPLATE_SELECTION_TTL = timedelta(minutes=30)
LONG_KONSPEKT_TRANSCRIPT_SECONDS = 60 * 60


# =====================================================================
# Общие хелперы
# =====================================================================


def _get_teacher(telegram_user_id: int, db_path=None) -> dict | None:
    rows = query("SELECT * FROM teachers WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path)
    return dict(rows[0]) if rows else None


def _has_style_profile(teacher_id: int, db_path=None) -> bool:
    rows = query("SELECT id FROM style_profiles WHERE teacher_id = ?", (teacher_id,), db_path=db_path)
    return bool(rows)


# Э3 (PLAN.md): положительный ответ has_given_consent кэшируется в памяти
# процесса — раньше _consent_gate (ниже) ходил в Supabase на КАЖДОЕ
# входящее сообщение и нажатие кнопки, хотя согласие, однажды данное, само
# по себе не отзывается. Кэшируется ТОЛЬКО "дано": "не дано" не кладётся в
# кэш и перепроверяется каждый раз — иначе отозванное через
# /delete_my_data согласие продолжало бы молча считаться данным до
# перезапуска процесса. Обычный set рядом с функцией, без библиотек и
# TTL — процесс и так перезапускается launchd, этого достаточно.
_consent_given_cache: set[int] = set()


def has_given_consent(telegram_user_id: int, db_path=None) -> bool:
    """Ю3: есть ли строка в consents — отдельной таблице, не в teachers
    (см. storage/schema.sql, там же полное обоснование). Положительный
    ответ кэшируется в памяти процесса (Э3, см. _consent_given_cache)."""
    if telegram_user_id in _consent_given_cache:
        return True
    rows = query(
        "SELECT 1 FROM consents WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path
    )
    given = bool(rows)
    if given:
        _consent_given_cache.add(telegram_user_id)
    return given


def record_consent(telegram_user_id: int, db_path=None) -> None:
    execute(
        "INSERT INTO consents (telegram_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(telegram_user_id) DO UPDATE SET given_at = excluded.given_at",
        (telegram_user_id,),
        db_path=db_path,
    )
    _consent_given_cache.add(telegram_user_id)


def _check_file_size(document) -> str | None:
    """Б8.3: файл больше 20 МБ — Telegram Bot API его всё равно не даст
    скачать, лучше сказать сразу и понятно, чем упасть на download()."""
    if document.file_size and document.file_size > MAX_FILE_SIZE_BYTES:
        return texts.ERROR_FILE_TOO_LARGE.format(size_mb=document.file_size / (1024 * 1024))
    return None


async def _require_teacher(message: Message) -> dict | None:
    """Б8.3: нет профиля учителя -> предложить /teacher, не падать."""
    teacher = _get_teacher(message.from_user.id)
    if teacher is None:
        await message.answer(texts.ERROR_NO_TEACHER_PROFILE)
    return teacher


# =====================================================================
# Ю3 (PLAN.md) — согласие до начала работы, единым middleware на весь
# router. Проверка в каждом из ~20 обработчиков команд была бы тем же
# форком, от которого предостерегает ловушка 2.6 — один пропущенный
# хендлер, и правило закона молча не выполняется именно там. Тот же
# принцип, что у _global_error_handler в bot/main.py: одно место
# применения правила, а не N копий проверки.
#
# Middleware НЕ покрывает тесты, которые вызывают хендлеры напрямую
# (весь стиль тестов в tests/test_bot_handlers.py) — они минуют router
# целиком. Поэтому у _consent_gate есть отдельные тесты, вызывающие её
# саму (tests/test_bot_handlers.py, блок Ю3).
# =====================================================================

_CONSENT_EXEMPT_CALLBACK_DATA = {
    "consent_accept",
    "consent_decline",
    # У3: выбор роли и согласие ученика происходят ДО того, как согласие
    # вообще может быть дано — тем же принципом, что consent_accept/decline
    # выше. role_teacher исключён из той же осторожности: до него человек
    # мог не читать вообще ничего, и блокировать выбор роли нечем.
    "role_teacher",
    "role_student",
    "student_consent_accept",
    "student_consent_decline",
}


async def _consent_gate(handler, event, data):
    """Различает Message и CallbackQuery по наличию атрибутов, не
    isinstance от aiogram — тесты этого файла везде дублируют события
    лёгкими объектами (FakeMessage/FakeCallbackQuery), не настоящими
    классами aiogram, и isinstance их не узнал бы."""
    from_user = getattr(event, "from_user", None)
    if from_user is None:
        return await handler(event, data)

    is_callback = hasattr(event, "data") and hasattr(event, "message")
    if is_callback:
        if event.data in _CONSENT_EXEMPT_CALLBACK_DATA:
            return await handler(event, data)
    else:
        text = getattr(event, "text", None) or ""
        if text.split()[0:1] == ["/start"]:
            return await handler(event, data)

    target = event.message if is_callback else event

    try:
        consented = has_given_consent(from_user.id)
    except SupabaseDatabaseError:
        # Э3: недоступность базы НЕ пропускает пользователя вперёд "на
        # всякий случай" — это значило бы дать работать без проверки
        # согласия, ровно то, что запрещает Ю3. Честно говорим, что
        # временно не можем ответить, а не молчим и не делаем вид, что
        # всё в порядке.
        logger.warning(
            "_consent_gate: не удалось проверить согласие telegram_user_id=%s — база недоступна",
            from_user.id,
            exc_info=True,
        )
        await target.answer(texts.CONSENT_CHECK_UNAVAILABLE)
        if is_callback:
            await event.answer()
        return None

    if consented:
        return await handler(event, data)

    await target.answer(texts.CONSENT_REQUIRED_REDIRECT)
    if is_callback:
        await event.answer()
    return None


router.message.outer_middleware(_consent_gate)
router.callback_query.outer_middleware(_consent_gate)


_LIMIT_OPERATION_LABELS = {"generate_ksp": "КСП", "generate_ktp": "КТП"}


async def _check_and_report_limits(message: Message, telegram_user_id: int, operation: str) -> bool:
    """М6.2: проверка обоих лимитов (по количеству операции и общего
    потолка токенов) ДО постановки задачи в очередь. Возвращает False и
    сама сообщает пользователю причину отказа, если хоть один лимит
    исчерпан — вызывающий код просто прерывает диалог, не решая, что
    писать (М6.3: не "лимит исчерпан", а сколько потрачено и когда
    сбросится)."""
    try:
        check_count_limit(telegram_user_id, operation)
        check_token_limit(telegram_user_id)
    except LimitExceeded as exc:
        reset_time = exc.reset_at.strftime("%H:%M")
        if exc.period == "week":
            text = texts.LIMIT_WEEKLY_COUNT_EXCEEDED.format(
                operation_label=_LIMIT_OPERATION_LABELS.get(operation, operation),
                used=exc.used, limit=exc.limit,
                reset_date=exc.reset_at.strftime("%d.%m.%Y"), reset_time=reset_time,
            )
        elif operation in DAILY_COUNT_LIMITS and exc.limit == DAILY_COUNT_LIMITS[operation]:
            text = texts.LIMIT_COUNT_EXCEEDED.format(
                operation_label=_LIMIT_OPERATION_LABELS.get(operation, operation),
                used=exc.used, limit=exc.limit, reset_time=reset_time,
            )
        else:
            text = texts.LIMIT_TOKENS_EXCEEDED.format(used=exc.used, limit=exc.limit, reset_time=reset_time)
        await message.answer(text, reply_markup=keyboards.MAIN_MENU)
        return False
    return True


@router.message(Command("admin"))
async def cmd_admin(message: Message, bot: Bot) -> None:
    """Л1: персонально и на сутки снимает лимиты после проверки пароля."""
    configured_password = settings.admin_password
    if not configured_password:
        await message.answer(texts.ADMIN_UNAVAILABLE, reply_markup=keyboards.MAIN_MENU)
        return

    _, _, supplied_password = (message.text or "").partition(" ")
    if not supplied_password:
        await message.answer(texts.ADMIN_USAGE, reply_markup=keyboards.MAIN_MENU)
        return

    try:
        await bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        logger.warning("Не удалось удалить сообщение с паролем команды /admin", exc_info=True)

    if not secrets.compare_digest(supplied_password.strip().encode(), configured_password.encode()):
        await message.answer(texts.ADMIN_DENIED, reply_markup=keyboards.MAIN_MENU)
        return

    expires_at = grant_admin_access(message.from_user.id)
    await message.answer(
        texts.ADMIN_GRANTED.format(expires_at=expires_at.strftime("%d.%m.%Y %H:%M")),
        reply_markup=keyboards.MAIN_MENU,
    )


# =====================================================================
# /start — У3 (PLAN.md): незнакомый человек выбирает роль (педагог или
# ученик) до того, как увидит что бы то ни было ещё. Уже согласившиеся
# (в том числе не успевшие пройти /teacher — сегодняшнее поведение,
# ломать нельзя) роль не выбирают заново: они по определению педагоги,
# единственная роль, для которой согласие уже было получено раньше этого
# блока. Известный ученик (строка в students) распознаётся отдельно и
# первым — про него бот не должен даже пытаться думать "педагог".
# =====================================================================


def _is_student(telegram_id: int, db_path=None) -> bool:
    return bool(query("SELECT 1 FROM students WHERE telegram_id = ?", (telegram_id,), db_path=db_path))


def _ensure_student_row(telegram_id: int, full_name: str | None, db_path=None) -> None:
    existing = query("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,), db_path=db_path)
    if not existing:
        execute("INSERT INTO students (telegram_id, name) VALUES (?, ?)", (telegram_id, full_name), db_path=db_path)


def _student_classes(telegram_id: int, db_path=None) -> list[dict]:
    rows = query(
        "SELECT c.name AS name, t.name AS teacher_name FROM class_members cm "
        "JOIN classes c ON c.id = cm.class_id "
        "JOIN students s ON s.id = cm.student_id "
        "JOIN teachers t ON t.id = c.teacher_id "
        "WHERE s.telegram_id = ? ORDER BY cm.joined_at",
        (telegram_id,),
        db_path=db_path,
    )
    return [dict(row) for row in rows]


async def _send_student_home(message: Message, telegram_id: int) -> None:
    classes = _student_classes(telegram_id)
    if not classes:
        await message.answer(texts.STUDENT_HOME_NO_CLASSES, reply_markup=ReplyKeyboardRemove())
        return
    listing = "\n".join(f"• {c['name']} ({c['teacher_name']})" for c in classes)
    await message.answer(texts.STUDENT_HOME_WITH_CLASSES.format(classes_list=listing), reply_markup=ReplyKeyboardRemove())


# =====================================================================
# Находка 1 AUDIT.md — педагогические команды не существуют для ученика.
#
# Требование блока У3 (PLAN.md) дословно: «Ученику доступны только его
# команды. Педагогические (/generate, /ktp, /upload) для него не
# существуют — не "нет доступа", а не показываются». До этой правки
# роль не проверял ни один хендлер, кроме cmd_start: ребёнок, набравший
# /menu, получал полное меню педагога, а подсказка «Сначала заведите
# профиль: /teacher» из _require_teacher действительно заводила ему
# профиль педагога — после чего он мог создать класс, выдать код
# приглашения другим детям и тратить генерации.
#
# Сделано тем же способом, что _consent_gate выше: одно место применения
# правила, а не проверка в каждом из ~20 хендлеров (та же причина —
# ловушка 2.6: один забытый хендлер, и правило молча не выполняется
# именно там). Регистрация ПОСЛЕ _consent_gate, поэтому согласие
# проверяется первым, а роль — вторым.
#
# Проверяются только точки входа педагога: команды не из списка
# разрешённых ученику и тексты кнопок постоянного меню. Свободный текст
# (код приглашения, подпись к фото) пропускается без единого запроса к
# базе — иначе на каждое сообщение ученика лёг бы лишний поход в
# Supabase, ровно то, от чего уходил блок Э3.
#
# Callback-кнопки не фильтруются сознательно: inline-клавиатуры педагога
# ученику никто не присылает, а те, где идентификатор приходит из
# callback_data, и так перепроверяют владение (блок У2).
# =====================================================================

_STUDENT_ALLOWED_COMMANDS = {"start", "join", "sverka", "cancel", "back", "delete_my_data"}


def _command_name(text: str) -> str | None:
    """"/generate@my_bot тема" -> "generate"; не команда -> None."""
    if not text.startswith("/"):
        return None
    first_word = text.split(maxsplit=1)[0]
    return first_word[1:].split("@", 1)[0].lower() or None


def _is_teacher_entry_point(text: str) -> bool:
    """Сообщение — попытка воспользоваться функцией педагога?"""
    command = _command_name(text)
    if command is not None:
        return command not in _STUDENT_ALLOWED_COMMANDS
    return text in keyboards.MAIN_MENU_BUTTON_TEXTS


async def _student_gate(handler, event, data):
    """Не пускает ученика в педагогические хендлеры.

    Различает Message и CallbackQuery по наличию атрибутов, а не через
    isinstance — по той же причине, что и _consent_gate: тесты дублируют
    события лёгкими объектами, настоящие классы aiogram там не
    используются.
    """
    from_user = getattr(event, "from_user", None)
    if from_user is None:
        return await handler(event, data)

    is_callback = hasattr(event, "data") and hasattr(event, "message")
    if is_callback:
        return await handler(event, data)

    text = getattr(event, "text", None) or ""
    if not _is_teacher_entry_point(text):
        return await handler(event, data)

    try:
        student = _is_student(from_user.id)
    except SupabaseDatabaseError:
        logger.warning(
            "_student_gate: не удалось определить роль telegram_user_id=%s — база недоступна",
            from_user.id,
            exc_info=True,
        )
        await event.answer(texts.ROLE_CHECK_UNAVAILABLE)
        return None

    if not student:
        return await handler(event, data)

    # Ученику показываем не отказ в доступе, а то, что он действительно
    # может: его классы и /sverka. Клавиатура педагога при этом убирается
    # (ReplyKeyboardRemove внутри _send_student_home) — если она осталась
    # у него с прошлых версий бота, здесь она и исчезнет.
    await event.answer(texts.STUDENT_TEACHER_COMMAND_UNAVAILABLE)
    await _send_student_home(event, from_user.id)
    return None


router.message.outer_middleware(_student_gate)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    telegram_id = message.from_user.id

    if _is_student(telegram_id):
        if not has_given_consent(telegram_id):
            await message.answer(texts.STUDENT_CONSENT_TEXT, reply_markup=keyboards.student_consent_keyboard())
            return
        await _send_student_home(message, telegram_id)
        return

    if has_given_consent(telegram_id):
        await message.answer(texts.START, reply_markup=keyboards.MAIN_MENU)
        return

    if _get_teacher(telegram_id) is not None:
        # Защитная ветка: teachers-строка есть, а согласия почему-то нет
        # (например, данные заведены руками до блока Ю3) — такому
        # человеку роль не в чем спрашивать, он явно уже педагог.
        await message.answer(texts.CONSENT_TEXT, reply_markup=keyboards.consent_keyboard())
        return

    await message.answer(texts.ROLE_CHOICE_TEXT, reply_markup=keyboards.role_choice_keyboard())


@router.callback_query(F.data == "role_teacher")
async def role_teacher_chosen(callback: CallbackQuery) -> None:
    telegram_id = callback.from_user.id
    if not has_given_consent(telegram_id):
        await callback.message.answer(texts.CONSENT_TEXT, reply_markup=keyboards.consent_keyboard())
    else:
        await callback.message.answer(texts.START, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


@router.callback_query(F.data == "role_student")
async def role_student_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    telegram_id = callback.from_user.id
    _ensure_student_row(telegram_id, getattr(callback.from_user, "full_name", None))
    if not has_given_consent(telegram_id):
        await callback.message.answer(texts.STUDENT_CONSENT_TEXT, reply_markup=keyboards.student_consent_keyboard())
    else:
        await go_to(state, StudentJoin.waiting_for_code)
        await _ask_join_code(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "consent_accept")
async def consent_accepted(callback: CallbackQuery) -> None:
    record_consent(callback.from_user.id)
    await callback.message.answer(texts.START, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


@router.callback_query(F.data == "consent_decline")
async def consent_declined(callback: CallbackQuery) -> None:
    await callback.message.answer(texts.CONSENT_DECLINED)
    await callback.answer()


@router.callback_query(F.data == "student_consent_accept")
async def student_consent_accepted(callback: CallbackQuery, state: FSMContext) -> None:
    record_consent(callback.from_user.id)
    await go_to(state, StudentJoin.waiting_for_code)
    await _ask_join_code(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "student_consent_decline")
async def student_consent_declined(callback: CallbackQuery) -> None:
    await callback.message.answer(texts.CONSENT_DECLINED)
    await callback.answer()


# =====================================================================
# /menu — вернуть свёрнутую клавиатуру меню (М1.1, восстановлена по
# находке 6 аудита этапа 2: команда была в списке плана, но не написана).
#
# Постоянное меню — ReplyKeyboardMarkup, и пользователь может свернуть её
# кнопкой в клиенте Telegram. До этой команды развернуть обратно было
# нечем, кроме /start с полным приветствием на пол-экрана.
# =====================================================================


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        # Тот же смысл, что у нажатия кнопки меню (menu_button_pressed):
        # просьба показать меню посреди диалога — это выход из диалога.
        # Аудио, уже скачанное в /konspekt, при этом не бросаем (находка 2).
        await _discard_pending_audio(state)
        await state.clear()
        await message.answer(texts.MENU_DIALOG_INTERRUPTED)
    await message.answer(texts.MENU_SHOWN, reply_markup=keyboards.MAIN_MENU)


# =====================================================================
# /cancel — общий сброс любого диалога
# =====================================================================


async def _discard_pending_audio(state: FSMContext) -> None:
    """Удаляет части записи, скачанные в /konspekt, но так и не ушедшие в
    очередь (аудит этапа 2, находка 2).

    Пути к частям живут только в данных FSM, а state.clear() их стирает —
    после этого файлы на диске не знает никто и не удалит никогда. Для
    .docx это стоило 40 КБ и терпелось с этапа 1, но часть урока — до
    20 МБ, а частей до MAX_KONSPEKT_PARTS: один брошенный диалог мог
    оставить десятки мегабайт мусора на машине, которая работает
    круглосуточно.
    Это же прямо нарушало KPI MASTER.md «аудиофайлов на диске после
    обработки: 0».

    Вызывать ДО state.clear(), иначе удалять будет уже нечего. Путь
    /done сюда не попадает и не должен: там файлы уходят в задачу
    очереди, и удалит их она сама (К2.4)."""
    data = await state.get_data()
    for path_str in data.get("audio_paths") or []:
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            logger.warning("не удалось удалить брошенную часть записи %s", path_str)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(texts.CANCEL_NOTHING_TO_CANCEL)
        return
    await _discard_pending_audio(state)
    await state.clear()
    await message.answer(texts.CANCEL_DONE)


# =====================================================================
# М2.1 — постоянное меню: единственный обработчик всех его кнопок
#
# Регистрируется здесь, сразу после /cancel и ДО любых хендлеров с
# фильтром по состоянию (Generate.*, GenerateKTP.*, UploadKSP.* и
# остальные ниже по файлу). Ловушка плана (М2.1/М3.2): aiogram матчит
# хендлеры в порядке регистрации, и если этот хендлер окажется НИЖЕ
# состояний диалога, текст кнопки меню будет съеден как обычный ответ
# пользователя на вопрос диалога (тема урока, класс и т.д.) — тем же
# способом, каким уже решена ровно эта проблема для /cancel выше.
#
# _MENU_BUTTON_HANDLERS заполняется в самом низу этого файла, когда все
# cmd_* функции уже определены — Python резолвит имя при вызове (внутри
# тела async-функции), а не при её определении, так что вперёд смотреть
# не нужно.
# =====================================================================


@router.message(F.text.in_(keyboards.MAIN_MENU_BUTTON_TEXTS))
async def menu_button_pressed(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is not None:
        await _discard_pending_audio(state)  # находка 2 аудита: не бросать аудио на диске
        await state.clear()
        await message.answer(texts.MENU_DIALOG_INTERRUPTED)
    handler = _MENU_BUTTON_HANDLERS[message.text]
    await handler(message, state)


# =====================================================================
# М3.2 — «Назад»: один обработчик на все диалоги, зарегистрирован здесь,
# ДО любых хендлеров с фильтром по состоянию — та же ловушка порядка
# регистрации, что и у menu_button_pressed выше (М2.1/М3.2).
#
# _BACK_ASK_HANDLERS заполняется внизу файла, когда все функции "спросить
# шаг заново" уже определены — тем же способом, что и _MENU_BUTTON_HANDLERS.
#
# UploadKSP.collecting_files и Konspekt.collecting_audio — исключения из
# общего правила "Назад = предыдущий шаг": там всего один шаг и файлы
# копятся. Раньше это было веткой в этом же обработчике под именем
# "Назад" (М3.3), но кнопка со стрелкой назад, безвозвратно удаляющая
# присланный файл, вводила в заблуждение — блок Н1 (PLAN.md) завёл этим
# двум состояниям отдельные подписи кнопок (texts.BUTTON_REMOVE_LAST_FILE,
# texts.BUTTON_REMOVE_LAST_PART) и отдельные обработчики ниже, у своих
# состояний. Общий back_button_pressed их больше не касается.
# =====================================================================


async def _handle_go_back(message: Message, state: FSMContext) -> None:
    previous = await go_back(state)
    if previous is None:
        await message.answer(texts.NAV_BACK_TO_MENU, reply_markup=keyboards.MAIN_MENU)
        return
    ask_again = _BACK_ASK_HANDLERS.get(previous)
    if ask_again is None:
        # Защитная ветка: состояние есть в стеке, но для него не заведена
        # функция "спросить заново" — не должно происходить в проде, но
        # честная ошибка лучше молчаливого зависания диалога.
        logger.error("нет обработчика 'спросить заново' для состояния %s из стека навигации", previous)
        await message.answer(texts.MENU_DIALOG_INTERRUPTED)
        await state.clear()
        return
    await ask_again(message, state)


@router.message(Command("back"))
@router.message(F.text == texts.BUTTON_BACK)
async def back_button_pressed(message: Message, state: FSMContext) -> None:
    await _handle_go_back(message, state)


@router.callback_query(F.data == "nav_back")
async def back_callback_pressed(callback: CallbackQuery, state: FSMContext) -> None:
    await _handle_go_back(callback.message, state)
    await callback.answer()


@router.message(F.text == texts.BUTTON_CANCEL)
async def cancel_button_pressed(message: Message, state: FSMContext) -> None:
    await cmd_cancel(message, state)


# =====================================================================
# У3 — /join: код приглашения. Регистрируется ПОСЛЕ menu_button_pressed/
# back_button_pressed/cancel_button_pressed выше (грабля 2.4, PLAN.md) —
# StudentJoin.waiting_for_code матчит ЛЮБОЕ сообщение в этом состоянии;
# будь этот блок выше, текст кнопки "Отменить" был бы съеден как
# попытка ввести код приглашения, а не как нажатие "Отменить".
# =====================================================================


async def _ask_join_code(message: Message, state: FSMContext, error: str | None = None) -> None:
    text = f"{error}\n\n{texts.STUDENT_JOIN_ASK_CODE}" if error else texts.STUDENT_JOIN_ASK_CODE
    await message.answer(text, reply_markup=keyboards.cancel_only_keyboard())


@router.message(Command("join"))
async def cmd_join(message: Message, state: FSMContext) -> None:
    """Не в BOT_COMMANDS (М1) и не в MAIN_MENU — команда ученика, а не
    педагога, и в подсказках педагога ей не место (У3: "педагогические
    команды не показываются", то же верно и в обратную сторону).
    Команда работает при прямом наборе — Telegram не требует, чтобы
    команда была в списке подсказок, чтобы она отвечала."""
    telegram_id = message.from_user.id
    _ensure_student_row(telegram_id, getattr(message.from_user, "full_name", None))
    await go_to(state, StudentJoin.waiting_for_code)
    await _ask_join_code(message, state)


def _find_class_by_invite_code(code: str, db_path=None) -> dict | None:
    rows = query(
        "SELECT c.id, c.name, c.subject, t.name AS teacher_name "
        "FROM classes c JOIN teachers t ON t.id = c.teacher_id "
        "WHERE c.invite_code = ?",
        (code,),
        db_path=db_path,
    )
    return dict(rows[0]) if rows else None


@router.message(StudentJoin.waiting_for_code)
async def student_join_code_received(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code:
        await _ask_join_code(message, state)
        return
    class_row = _find_class_by_invite_code(code)
    if class_row is None:
        # У3, дословно: сказать "код не найден" и предложить ввести
        # заново, НЕ выкидывая в главное меню — состояние не меняется.
        await _ask_join_code(message, state, error=texts.STUDENT_JOIN_CODE_NOT_FOUND)
        return
    await state.update_data(
        class_id=class_row["id"], class_name=class_row["name"], teacher_name=class_row["teacher_name"]
    )
    await go_to(state, StudentJoin.waiting_for_confirmation)
    await message.answer(
        texts.STUDENT_JOIN_CONFIRM.format(class_name=class_row["name"], teacher_name=class_row["teacher_name"]),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=texts.STUDENT_JOIN_CONFIRM_BUTTON, callback_data="student_join_confirm"),
                    InlineKeyboardButton(text=texts.STUDENT_JOIN_CANCEL_BUTTON, callback_data="student_join_cancel"),
                ]
            ]
        ),
    )


@router.callback_query(StudentJoin.waiting_for_confirmation, F.data == "student_join_confirm")
async def student_join_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    class_id = data["class_id"]
    class_name = data["class_name"]
    teacher_name = data["teacher_name"]
    telegram_id = callback.from_user.id

    student_id = query("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,))[0]["id"]
    already = query(
        "SELECT 1 FROM class_members WHERE class_id = ? AND student_id = ?", (class_id, student_id)
    )
    await state.clear()

    if already:
        await callback.message.answer(
            texts.STUDENT_JOIN_ALREADY_MEMBER.format(class_name=class_name), reply_markup=ReplyKeyboardRemove()
        )
        await callback.answer()
        return

    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))
    await callback.message.answer(
        texts.STUDENT_JOIN_SUCCESS.format(class_name=class_name, teacher_name=teacher_name),
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.answer()


@router.callback_query(StudentJoin.waiting_for_confirmation, F.data == "student_join_cancel")
async def student_join_cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.STUDENT_JOIN_CANCELLED)
    await callback.answer()


# =====================================================================
# /sverka (У4, PLAN.md) — ядро продукта ученика: фото тетради -> список
# того, чего не хватает по сравнению с записью урока. Регистрируется
# ПОСЛЕ menu_button_pressed/back_button_pressed/cancel_button_pressed
# (грабля 2.4, та же причина, что у /join выше) — SverkaCheck.waiting_for_photo
# матчит любое сообщение в этом состоянии, включая текст кнопки "Отменить".
#
# Выбор класса и урока — inline-кнопки без FSM: они не принимают
# свободный текст, а go_to/go_back для них не нужен — каждый шаг просто
# показывает новое сообщение с кнопками, "Отменить" всегда работает
# глобально независимо от того, заведено состояние или нет.
# =====================================================================


def _student_classes_with_ids(telegram_id: int, db_path=None) -> list[dict]:
    rows = query(
        "SELECT c.id AS class_id, c.name AS class_name, c.teacher_id AS teacher_id "
        "FROM class_members cm "
        "JOIN classes c ON c.id = cm.class_id "
        "JOIN students s ON s.id = cm.student_id "
        "WHERE s.telegram_id = ? ORDER BY cm.joined_at",
        (telegram_id,),
        db_path=db_path,
    )
    return [dict(row) for row in rows]


def _recent_transcripts_for_teacher(teacher_id: int, limit: int = 8, db_path=None) -> list[dict]:
    rows = query(
        "SELECT t.id AS id, t.text AS text, t.created_at AS created_at, k.topic AS topic "
        "FROM transcripts t LEFT JOIN ktp_entries k ON k.id = t.ktp_entry_id "
        "WHERE t.teacher_id = ? ORDER BY t.created_at DESC LIMIT ?",
        (teacher_id, limit),
        db_path=db_path,
    )
    return [dict(row) for row in rows]


def _sverka_lesson_label(row: dict) -> str:
    date_str = str(row["created_at"])[:10]
    topic = row["topic"] or texts.SVERKA_LESSON_NO_TOPIC
    return texts.SVERKA_LESSON_ROW_BUTTON.format(date=date_str, topic=topic)[:64]


async def _show_sverka_lessons(message: Message, teacher_id: int) -> None:
    lessons = _recent_transcripts_for_teacher(teacher_id)
    if not lessons:
        # У4, дословно: сказать прямо, что записи ещё нет — не пустой
        # результат сверки.
        await message.answer(texts.SVERKA_NO_TRANSCRIPT)
        return
    rows = [
        [InlineKeyboardButton(text=_sverka_lesson_label(row), callback_data=f"sverka_lesson:{row['id']}")]
        for row in lessons
    ]
    await message.answer(texts.SVERKA_ASK_LESSON, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("sverka"))
async def cmd_sverka(message: Message) -> None:
    """Не в BOT_COMMANDS/MAIN_MENU — команда ученика (тот же принцип,
    что у /join)."""
    telegram_id = message.from_user.id
    if not _is_student(telegram_id):
        await message.answer(texts.SVERKA_NOT_A_STUDENT)
        return
    classes = _student_classes_with_ids(telegram_id)
    if not classes:
        await message.answer(texts.SVERKA_NO_CLASSES)
        return
    if len(classes) == 1:
        await _show_sverka_lessons(message, classes[0]["teacher_id"])
        return
    rows = [
        [InlineKeyboardButton(text=c["class_name"], callback_data=f"sverka_class:{c['class_id']}")] for c in classes
    ]
    await message.answer(texts.SVERKA_ASK_CLASS, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sverka_class:"))
async def sverka_class_chosen(callback: CallbackQuery) -> None:
    class_id = int(callback.data.split(":", 1)[1])
    telegram_id = callback.from_user.id
    classes = {c["class_id"]: c for c in _student_classes_with_ids(telegram_id)}
    class_row = classes.get(class_id)
    if class_row is None:
        await callback.answer(texts.CLASS_NOT_FOUND, show_alert=True)
        return
    await _show_sverka_lessons(callback.message, class_row["teacher_id"])
    await callback.answer()


@router.callback_query(F.data.startswith("sverka_lesson:"))
async def sverka_lesson_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    transcript_id = callback.data.split(":", 1)[1]
    telegram_id = callback.from_user.id

    # Перепроверка владения: transcript_id пришёл в callback_data от
    # клиента, доверять ему без проверки нельзя — учитель должен быть
    # учителем одного из классов ЭТОГО ученика.
    teacher_ids = {c["teacher_id"] for c in _student_classes_with_ids(telegram_id)}
    rows = query("SELECT teacher_id FROM transcripts WHERE id = ?", (transcript_id,))
    if not rows or rows[0]["teacher_id"] not in teacher_ids:
        await callback.answer(texts.SVERKA_LESSON_NOT_FOUND, show_alert=True)
        return

    await go_to(state, SverkaCheck.waiting_for_photo)
    await state.update_data(transcript_id=transcript_id)
    await callback.message.answer(texts.SVERKA_ASK_PHOTO, reply_markup=keyboards.cancel_only_keyboard())
    await callback.answer()


@router.message(SverkaCheck.waiting_for_photo, F.photo)
async def sverka_photo_received(message: Message, state: FSMContext, bot: Bot) -> None:
    largest = message.photo[-1]
    size_error = _check_file_size(largest)
    if size_error:
        await message.answer(size_error)
        return

    data = await state.get_data()
    transcript_id = data["transcript_id"]
    telegram_id = message.from_user.id
    student_row = query("SELECT id FROM students WHERE telegram_id = ?", (telegram_id,))
    if not student_row:
        await state.clear()
        return
    student_id = student_row[0]["id"]

    dest = settings.uploads_dir / f"{uuid.uuid4()}.jpg"
    await bot.download(largest, destination=dest)
    await state.clear()

    enqueue(
        "sverka_tetradi",
        {"student_id": student_id, "transcript_id": transcript_id, "photo_path": str(dest)},
        chat_id=message.chat.id,
    )
    await message.answer(texts.SVERKA_PROCESSING, reply_markup=ReplyKeyboardRemove())


@router.message(SverkaCheck.waiting_for_photo)
async def sverka_wrong_input(message: Message) -> None:
    await message.answer(texts.SVERKA_ASK_PHOTO, reply_markup=keyboards.cancel_only_keyboard())


# Ровно тот же приём, что TRANSCRIBE_REAL_ATTEMPT_LIMIT (bot/handlers.py,
# К2.4): фото удаляется в finally и при провале тоже (У4, ловушка) — а
# значит повторная попытка после первого провала читала бы уже
# несуществующий файл. Вместо честного повторного вызова OCR/сравнения
# retries > 0 сразу и честно отказывает, не пытаясь читать удалённое
# фото. Ретраи очереди при этом не бесполезны: они всё равно нужны для
# гарантии уведомления (KPI, MASTER.md п.1.7) — просто без повторной
# реальной попытки.
SVERKA_REAL_ATTEMPT_LIMIT = 0


def make_sverka_handler(bot: Bot):
    """Задача очереди (У4): OCR фото тетради + LLM-сравнение с
    расшифровкой урока — оба вызова к LLM, той же причиной, что
    распознавание фото учебника в make_generate_ksp_handler живёт в
    очереди, а не синхронно в диалоге: может занять до минуты, нужны
    ретраи и гарантия уведомления."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]
        photo_path = Path(payload["photo_path"])

        if task.get("retries", 0) > SVERKA_REAL_ATTEMPT_LIMIT:
            photo_path.unlink(missing_ok=True)
            raise KonspektCompareError(texts.SVERKA_RETRY_DISABLED)

        try:
            transcript_rows = query("SELECT text FROM transcripts WHERE id = ?", (payload["transcript_id"],))
            if not transcript_rows:
                raise KonspektCompareError(
                    f"транскрипт {payload['transcript_id']} не найден — не может сверить с ним тетрадь"
                )
            transcript_text = transcript_rows[0]["text"]

            image_bytes = photo_path.read_bytes()
            notebook_text = await recognize_textbook_page(image_bytes, "image/jpeg")

            llm_client = LLMClient()
            try:
                missing_items = await compare_notebook_to_transcript(
                    transcript_text, notebook_text, llm_client=llm_client
                )
            finally:
                record_student_usage(chat_id)
                await llm_client.aclose()
        finally:
            # У4, ловушка (та же, что у аудио, грабля 2.8): фото хранится
            # ровно столько, сколько нужно для сверки — и при успехе, и
            # при провале.
            photo_path.unlink(missing_ok=True)

        if missing_items:
            lines = "\n".join(texts.SVERKA_RESULT_ITEM.format(item=item) for item in missing_items)
            text = f"{texts.SVERKA_RESULT_HEADER}\n{lines}"
        else:
            text = texts.SVERKA_NOTHING_MISSING
        await bot.send_message(chat_id, text)
        return {"missing_items": missing_items}

    return handler


# =====================================================================
# /teacher
# =====================================================================


async def _ask_teacher_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.TEACHER_ASK_NAME
    if data.get("name"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["name"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_teacher_subject(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.TEACHER_ASK_SUBJECT
    if data.get("subject"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["subject"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_teacher_school(message: Message, state: FSMContext) -> None:
    await message.answer(texts.TEACHER_ASK_SCHOOL, reply_markup=keyboards.back_cancel_keyboard())


@router.message(Command("teacher"))
async def cmd_teacher(message: Message, state: FSMContext) -> None:
    await go_to(state, TeacherProfile.waiting_for_name)
    await _ask_teacher_name(message, state)


@router.message(TeacherProfile.waiting_for_name)
async def teacher_name_received(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await _ask_teacher_name(message, state)
        return
    await state.update_data(name=name)
    await go_to(state, TeacherProfile.waiting_for_subject)
    await _ask_teacher_subject(message, state)


@router.message(TeacherProfile.waiting_for_subject)
async def teacher_subject_received(message: Message, state: FSMContext) -> None:
    subject = (message.text or "").strip()
    if not subject:
        await _ask_teacher_subject(message, state)
        return
    await state.update_data(subject=subject)
    await go_to(state, TeacherProfile.waiting_for_school)
    await _ask_teacher_school(message, state)


@router.message(TeacherProfile.waiting_for_school)
async def teacher_school_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    school = None if text in ("-", "") else text

    data = await state.get_data()
    name = data["name"]
    subject = data["subject"]
    telegram_user_id = message.from_user.id

    existing = _get_teacher(telegram_user_id)
    if existing:
        # school не перетирается пустым: учитель мог заполнить его раньше
        # и сейчас просто обновляет ФИО/предмет, отправив "-" по инерции —
        # не должны молча стереть то, что уже было указано.
        if school is not None:
            execute(
                "UPDATE teachers SET name = ?, subject = ?, school = ? WHERE telegram_user_id = ?",
                (name, subject, school, telegram_user_id),
            )
        else:
            execute(
                "UPDATE teachers SET name = ?, subject = ? WHERE telegram_user_id = ?",
                (name, subject, telegram_user_id),
            )
        response = texts.TEACHER_UPDATED
    else:
        execute(
            "INSERT INTO teachers (name, subject, school, telegram_user_id) VALUES (?, ?, ?, ?)",
            (name, subject, school, telegram_user_id),
        )
        response = texts.TEACHER_CREATED

    await state.clear()
    await message.answer(response.format(name=name, subject=subject), reply_markup=keyboards.MAIN_MENU)


# =====================================================================
# /upload_ksp — 2-5 файлов, ставит parse_ksp в очередь
# =====================================================================


@router.message(Command("upload_ksp"))
async def cmd_upload_ksp(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.set_state(UploadKSP.collecting_files)
    await state.update_data(teacher_id=teacher["id"], file_paths=[])
    await message.answer(texts.UPLOAD_KSP_PROMPT, reply_markup=keyboards.upload_ksp_collecting_keyboard())


async def _upload_ksp_remove_last_file(message: Message, state: FSMContext) -> None:
    """Н1: в этом диалоге один шаг, файлы копятся — кнопка «Убрать
    последний файл» удаляет последний загруженный файл, а не переключает
    состояние (переключать некуда, шаг один)."""
    data = await state.get_data()
    file_paths = list(data.get("file_paths", []))
    if not file_paths:
        await message.answer(
            texts.UPLOAD_KSP_NOTHING_TO_REMOVE, reply_markup=keyboards.upload_ksp_collecting_keyboard()
        )
        return

    removed_path = file_paths.pop()
    await state.update_data(file_paths=file_paths)
    try:
        Path(removed_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("не удалось удалить файл %s при отмене загрузки КСП", removed_path)

    await message.answer(
        texts.UPLOAD_KSP_LAST_FILE_REMOVED.format(filename=Path(removed_path).name, count=len(file_paths)),
        reply_markup=keyboards.upload_ksp_collecting_keyboard(),
    )


@router.message(UploadKSP.collecting_files, F.text == texts.BUTTON_REMOVE_LAST_FILE)
async def upload_ksp_remove_last_file_pressed(message: Message, state: FSMContext) -> None:
    await _upload_ksp_remove_last_file(message, state)


@router.message(UploadKSP.collecting_files, F.document)
async def upload_ksp_file_received(message: Message, state: FSMContext, bot: Bot) -> None:
    document = message.document

    size_error = _check_file_size(document)
    if size_error:
        await message.answer(size_error)
        return

    filename = document.file_name or "file"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_KSP_EXTENSIONS:
        await message.answer(
            texts.ERROR_UNSUPPORTED_FORMAT.format(
                extension=ext or "(без расширения)",
                supported=", ".join(sorted(SUPPORTED_KSP_EXTENSIONS)),
            )
        )
        return

    data = await state.get_data()
    file_paths = data.get("file_paths", [])
    if len(file_paths) >= MAX_KSP_FILES:
        await message.answer(texts.UPLOAD_KSP_MAX_REACHED)
        return

    dest = settings.uploads_dir / f"{uuid.uuid4()}{ext}"
    await bot.download(document, destination=dest)

    file_paths.append(str(dest))
    await state.update_data(file_paths=file_paths)
    await message.answer(
        texts.UPLOAD_KSP_FILE_ACCEPTED.format(n=len(file_paths), max=MAX_KSP_FILES, filename=filename),
        reply_markup=keyboards.upload_ksp_collecting_keyboard(),
    )


@router.message(UploadKSP.collecting_files, Command("done"))
async def upload_ksp_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    file_paths = data.get("file_paths", [])

    if len(file_paths) < MIN_KSP_FILES:
        await message.answer(
            texts.UPLOAD_KSP_TOO_FEW.format(count=len(file_paths)),
            reply_markup=keyboards.upload_ksp_collecting_keyboard(),
        )
        return

    enqueue(
        "parse_ksp",
        {"teacher_id": data["teacher_id"], "file_paths": file_paths},
        chat_id=message.chat.id,
    )
    await state.clear()
    await message.answer(texts.UPLOAD_KSP_QUEUED.format(count=len(file_paths)), reply_markup=keyboards.MAIN_MENU)


@router.message(UploadKSP.collecting_files)
async def upload_ksp_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_KSP_PROMPT, reply_markup=keyboards.upload_ksp_collecting_keyboard())


# =====================================================================
# /upload_ktp — один файл, разбирается синхронно (без LLM, без очереди)
# =====================================================================


@router.message(Command("upload_ktp"))
async def cmd_upload_ktp(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.set_state(UploadKTP.waiting_for_file)
    await state.update_data(teacher_id=teacher["id"])
    await message.answer(texts.UPLOAD_KTP_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


@router.message(UploadKTP.waiting_for_file, F.document)
async def upload_ktp_file_received(message: Message, state: FSMContext, bot: Bot) -> None:
    document = message.document

    size_error = _check_file_size(document)
    if size_error:
        await message.answer(size_error)
        return

    filename = document.file_name or "file"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_KTP_EXTENSIONS:
        await message.answer(
            texts.ERROR_UNSUPPORTED_FORMAT.format(
                extension=ext or "(без расширения)",
                supported=", ".join(sorted(SUPPORTED_KTP_EXTENSIONS)),
            )
        )
        return

    data = await state.get_data()
    teacher_id = data["teacher_id"]

    dest = settings.uploads_dir / f"{uuid.uuid4()}{ext}"
    await bot.download(document, destination=dest)

    try:
        entries = parse_ktp_file(dest)
        result = save_ktp_entries(teacher_id, entries)
    except KTPParseError as exc:
        await message.answer(texts.UPLOAD_KTP_PARSE_ERROR.format(error=str(exc)), reply_markup=keyboards.MAIN_MENU)
        await state.clear()
        return

    warning = ""
    if result["codes_not_found"]:
        warning = texts.UPLOAD_KTP_CODES_NOT_FOUND.format(n=result["codes_not_found"])
    replaced_note = ""
    if result["replaced"]:
        replaced_note = texts.UPLOAD_KTP_REPLACED.format(n=result["replaced"])
    await message.answer(
        texts.UPLOAD_KTP_SUCCESS.format(
            inserted=result["inserted"], replaced_note=replaced_note, codes_warning=warning
        ),
        reply_markup=keyboards.MAIN_MENU,
    )
    await state.clear()


@router.message(UploadKTP.waiting_for_file)
async def upload_ktp_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_KTP_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


# =====================================================================
# /templates — открыть Mini App
# =====================================================================


@router.message(Command("templates"))
async def cmd_templates(message: Message) -> None:
    if not settings.webapp_url:
        await message.answer(texts.TEMPLATES_NOT_CONFIGURED)
        return
    # Клавиатура именно reply, а не inline: Mini App возвращает выбор
    # шаблона через tg.sendData (web/static/app.js), а этот метод
    # Telegram работает ТОЛЬКО для Mini App, открытых кнопкой reply-
    # клавиатуры. С inline-кнопкой (как было) sendData молча ничего не
    # делает, и выбор шаблона никуда не доходил.
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.TEMPLATES_BUTTON, web_app=WebAppInfo(url=settings.webapp_url))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(texts.TEMPLATES_MESSAGE, reply_markup=keyboard)


@router.message(F.web_app_data)
async def templates_web_app_choice(message: Message, state: FSMContext) -> None:
    """Приём выбора шаблона из Mini App (экран «Шаблоны», блок Б10.1).

    Mini App присылает {"template_id": N} через tg.sendData. Раньше этого
    хендлера не было вообще: пользователь выбирал шаблон, Mini App
    закрывался — и на этом всё заканчивалось. Теперь выбор запускает
    обычный диалог /generate с уже выбранным шаблоном, то есть ведёт
    туда же, куда и выбор шаблона внутри бота."""
    teacher = await _require_teacher(message)
    if teacher is None:
        return

    try:
        payload = json.loads(message.web_app_data.data)
    except (ValueError, TypeError):
        await message.answer(texts.TEMPLATES_ACTION_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

    if not isinstance(payload, dict):
        await message.answer(texts.TEMPLATES_ACTION_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

    if payload.get("action") == "upload_template":
        await state.clear()
        await state.set_state(UploadTemplate.waiting_for_file)
        await state.update_data(teacher_id=teacher["id"])
        await message.answer(texts.UPLOAD_TEMPLATE_PROMPT, reply_markup=keyboards.back_cancel_keyboard())
        return

    try:
        template_id = int(payload["template_id"])
    except (ValueError, TypeError, KeyError):
        await message.answer(texts.TEMPLATES_ACTION_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

    # Шаблон должен быть доступен именно этому учителю: id приходит с
    # клиента, а значит доверять ему как своему нельзя.
    available = {t["id"]: t for t in list_templates(teacher["id"])}
    template = available.get(template_id)
    if template is None:
        await message.answer(texts.TEMPLATES_CHOSEN_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

    # app.js всегда сохраняет серверный fallback до попытки sendData.
    # Если sendData всё-таки доставил выбор, fallback уже не нужен и не
    # должен повторно всплыть при следующей /generate.
    execute("DELETE FROM template_selections WHERE telegram_user_id = ?", (message.from_user.id,))

    await state.clear()
    await state.set_state(Generate.waiting_for_topic)
    await state.update_data(
        teacher_id=teacher["id"], subject=teacher["subject"], template_id=template_id
    )
    await message.answer(
        texts.TEMPLATES_CHOSEN.format(template_name=template["name"]),
        reply_markup=keyboards.MAIN_MENU,
    )
    await message.answer(texts.GENERATE_ASK_TOPIC)


# =====================================================================
# /upload_template — свой образец оформления (F10, MASTER.md п.1.1.1)
#
# Шаблон и профиль стиля — разные сущности и не смешиваются: сюда идут
# образцы ФОРМЫ (в том числе найденные в интернете), в /upload_ksp —
# реальные КСП самого учителя, из которых строится манера письма.
# =====================================================================


@router.message(Command("upload_template"))
async def cmd_upload_template(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.set_state(UploadTemplate.waiting_for_file)
    await state.update_data(teacher_id=teacher["id"])
    await message.answer(texts.UPLOAD_TEMPLATE_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


@router.message(UploadTemplate.waiting_for_file, F.document)
async def upload_template_file_received(message: Message, state: FSMContext, bot: Bot) -> None:
    document = message.document

    size_error = _check_file_size(document)
    if size_error:
        await message.answer(size_error)
        return

    filename = document.file_name or "file"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_KSP_EXTENSIONS:
        await message.answer(
            texts.ERROR_UNSUPPORTED_FORMAT.format(
                extension=ext or "(без расширения)",
                supported=", ".join(sorted(SUPPORTED_KSP_EXTENSIONS)),
            )
        )
        return

    data = await state.get_data()
    dest = settings.uploads_dir / f"{uuid.uuid4()}{ext}"
    await bot.download(document, destination=dest)

    try:
        # Разбор .doc зовёт LibreOffice через subprocess — это блокирующая
        # операция, в отдельный поток, чтобы не морозить бот (см. правку
        # той же природы в make_parse_ksp_handler).
        template = await asyncio.to_thread(
            save_user_template, data["teacher_id"], dest, Path(filename).stem
        )
    except (KSPConversionError, KSPParseError) as exc:
        await message.answer(
            texts.UPLOAD_TEMPLATE_PARSE_ERROR.format(error=str(exc)), reply_markup=keyboards.MAIN_MENU
        )
        await state.clear()
        return

    await message.answer(
        texts.UPLOAD_TEMPLATE_SUCCESS.format(template_name=template["name"]), reply_markup=keyboards.MAIN_MENU
    )
    await state.clear()


@router.message(UploadTemplate.waiting_for_file)
async def upload_template_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_TEMPLATE_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


# =====================================================================
# /konspekt — приём аудио урока (блок К2.3, PLAN_STAGE2.md). Один шаг,
# файлы (части записи) копятся, как в UploadKSP — до /done. Дальше
# цепочка идёт САМА через очередь задач, без участия пользователя
# (MASTER.md 0.6, п.2 — "цепочка одна"): 'transcribe' (блок К3) по
# готовности сама ставит 'generate_konspekt' (блок К4, make_konspekt_handler
# ниже), учителю не нужно вызывать ничего отдельно.
#
# Команда добавлена в BOT_COMMANDS/MAIN_MENU только в блоке К4 (не в
# К2.3, когда была написана эта секция) — до того обработчика задачи
# 'transcribe' в очереди не было, и команда, отправленная раньше времени,
# поставила бы задачу, зависающую в pending навсегда (принцип М1.1/М2.1:
# рекламировать только то, что реально работает).
# =====================================================================

# Решение автора от 27.08.2026: не больше двух частей записи (было 10).
# Урок на 45 минут укладывается в один файл до 20 МБ — это проверено на
# настоящей записи (39 минут, 19,2 МБ), так что вторая часть нужна разве
# что на длинную пару. Ограничение также удерживает сетевую задачу в
# предсказуемом объёме: xAI получает не более двух аудиофайлов за урок.
MAX_KONSPEKT_PARTS = 2

# Voice в Telegram всегда .ogg (кодек opus) — безопасный дефолт, если по
# mime/имени файла угадать не удалось.
_AUDIO_MIME_EXTENSIONS = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _guess_audio_extension(mime_type: str | None, filename: str | None) -> str:
    if filename:
        ext = Path(filename).suffix.lower()
        if ext:
            return ext
    if mime_type:
        ext = _AUDIO_MIME_EXTENSIONS.get(mime_type)
        if ext:
            return ext
    return ".ogg"


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "длительность неизвестна"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} мин {secs} с" if minutes else f"{secs} с"


@router.message(Command("konspekt"))
async def cmd_konspekt(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await go_to(state, Konspekt.choosing_mode)
    await state.update_data(teacher_id=teacher["id"], audio_paths=[], audio_durations=[])
    await message.answer(
        texts.KONSPEKT_MODE_PROMPT,
        reply_markup=keyboards.konspekt_mode_keyboard(),
    )


@router.message(
    Konspekt.choosing_mode,
    F.text.in_({texts.KONSPEKT_MODE_STUDENT_BUTTON, texts.KONSPEKT_MODE_TEACHER_BUTTON}),
)
async def konspekt_mode_chosen(message: Message, state: FSMContext) -> None:
    """Сохраняет назначение записи до приёма первого аудиофайла."""
    mode = "student" if message.text == texts.KONSPEKT_MODE_STUDENT_BUTTON else "teacher"
    await state.update_data(mode=mode)
    await go_to(state, Konspekt.collecting_audio)
    await message.answer(
        texts.KONSPEKT_PROMPT.format(max_parts=MAX_KONSPEKT_PARTS),
        reply_markup=keyboards.konspekt_collecting_keyboard(),
    )


@router.message(Konspekt.choosing_mode)
async def konspekt_mode_wrong_input(message: Message) -> None:
    await message.answer(texts.KONSPEKT_MODE_INVALID, reply_markup=keyboards.konspekt_mode_keyboard())


async def _konspekt_remove_last_part(message: Message, state: FSMContext) -> None:
    """Кнопка «Убрать последнюю часть» в /konspekt (Н1, ранее — «Назад»,
    аудит этапа 2 находка 2). Шаг здесь один, переключать состояние
    некуда: ровно та же ситуация и ровно то же решение, что у
    _upload_ksp_remove_last_file. Файл удаляется с диска сразу, а не
    остаётся сиротой."""
    data = await state.get_data()
    paths = list(data.get("audio_paths", []))
    durations = list(data.get("audio_durations", []))
    if not paths:
        await message.answer(texts.KONSPEKT_NOTHING_TO_REMOVE, reply_markup=keyboards.konspekt_collecting_keyboard())
        return

    removed_path = paths.pop()
    if durations:
        durations.pop()
    await state.update_data(audio_paths=paths, audio_durations=durations)
    try:
        Path(removed_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("не удалось удалить часть записи %s при отмене", removed_path)

    await message.answer(
        texts.KONSPEKT_LAST_PART_REMOVED.format(count=len(paths)),
        reply_markup=keyboards.konspekt_collecting_keyboard(),
    )


@router.message(Konspekt.collecting_audio, F.text == texts.BUTTON_REMOVE_LAST_PART)
async def konspekt_remove_last_part_pressed(message: Message, state: FSMContext) -> None:
    await _konspekt_remove_last_part(message, state)


async def _konspekt_store_part(
    message: Message,
    state: FSMContext,
    bot: Bot,
    telegram_file,
    filename: str | None,
    mime_type: str | None,
    duration: int | None,
) -> None:
    """Общая логика приёма одной части записи — вызывается из трёх
    хендлеров (voice/audio/document), чтобы не дублировать её трижды."""
    size_error = _check_file_size(telegram_file)
    if size_error:
        # К2.3, ловушка (восстановлена по находке 7 аудита этапа 2): общий
        # текст этапа 1 советует «пришлите файл поменьше», а запись урока
        # короче не станет — дописываем то, что здесь реально помогает.
        # Проверку размера при этом переиспользуем, второй не заводим.
        await message.answer(
            size_error + texts.KONSPEKT_FILE_TOO_LARGE_HINT.format(max_parts=MAX_KONSPEKT_PARTS)
        )
        return

    data = await state.get_data()
    paths = data.get("audio_paths", [])
    if len(paths) >= MAX_KONSPEKT_PARTS:
        await message.answer(texts.KONSPEKT_MAX_PARTS_REACHED.format(max=MAX_KONSPEKT_PARTS))
        return

    ext = _guess_audio_extension(mime_type, filename)
    dest = settings.uploads_dir / f"{uuid.uuid4()}{ext}"
    await bot.download(telegram_file, destination=dest)

    paths.append(str(dest))
    durations = data.get("audio_durations", [])
    durations.append(duration)
    await state.update_data(audio_paths=paths, audio_durations=durations)

    await message.answer(
        texts.KONSPEKT_PART_ACCEPTED.format(
            n=len(paths), max=MAX_KONSPEKT_PARTS, duration=_format_duration(duration)
        ),
        reply_markup=keyboards.konspekt_collecting_keyboard(),
    )


@router.message(Konspekt.collecting_audio, F.voice)
async def konspekt_voice_received(message: Message, state: FSMContext, bot: Bot) -> None:
    voice = message.voice
    await _konspekt_store_part(message, state, bot, voice, None, voice.mime_type, voice.duration)


@router.message(Konspekt.collecting_audio, F.audio)
async def konspekt_audio_received(message: Message, state: FSMContext, bot: Bot) -> None:
    audio = message.audio
    await _konspekt_store_part(message, state, bot, audio, audio.file_name, audio.mime_type, audio.duration)


@router.message(Konspekt.collecting_audio, F.document)
async def konspekt_document_received(message: Message, state: FSMContext, bot: Bot) -> None:
    document = message.document
    mime_type = document.mime_type or ""
    if not mime_type.startswith("audio/"):
        await message.answer(texts.KONSPEKT_UNSUPPORTED_INPUT, reply_markup=keyboards.konspekt_collecting_keyboard())
        return
    await _konspekt_store_part(message, state, bot, document, document.file_name, mime_type, None)


@router.message(Konspekt.collecting_audio, F.text == texts.KONSPEKT_START_BUTTON)
@router.message(Konspekt.collecting_audio, Command("done"))
async def konspekt_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    paths = data.get("audio_paths", [])
    if not paths:
        await message.answer(texts.KONSPEKT_NO_PARTS_YET, reply_markup=keyboards.konspekt_collecting_keyboard())
        return

    enqueue(
        "transcribe",
        {"teacher_id": data["teacher_id"], "audio_paths": paths, "mode": data["mode"]},
        chat_id=message.chat.id,
    )
    await state.clear()
    await message.answer(texts.KONSPEKT_QUEUED.format(count=len(paths)), reply_markup=keyboards.MAIN_MENU)


@router.message(Konspekt.collecting_audio)
async def konspekt_wrong_input(message: Message) -> None:
    await message.answer(texts.KONSPEKT_UNSUPPORTED_INPUT, reply_markup=keyboards.konspekt_collecting_keyboard())


# =====================================================================
# /generate — FSM: тема -> код -> раздел -> класс -> продолжительность
#             -> доп. настройки (Р5) -> шаблон -> подтверждение -> очередь
#
# Спецификация (PLAN_STAGE1.md, Б8.2) в сокращённом виде перечисляет
# "тема -> код -> шаблон -> подтверждение", но generate_and_save_ksp
# (блок Б6) требует ещё razdel/klass/duration_minutes — их неоткуда
# взять, кроме как спросить. Не хотелось молча подставлять выдуманные
# значения (класс/раздел/минуты урока — это не то, что можно угадать).
# =====================================================================

# Р5.2/Р5.3: ключи строк доп. настроек -> поле LessonOptions. Один
# свободнотекстовый шаг, а не 8 последовательных вопросов — прямое
# требование блока Р5 ("не превращать диалог в анкету из 30 вопросов").
_EXTRA_OPTION_KEY_ALIASES = {
    "ценность": "cennost",
    "проект адал азамат": "adal_azamat_project",
    "виды деятельности": "vidy_deyatelnosti",
    "ооп": "ima_oop",
    "сор": "sor",
    "физкультминутка": "fizkultminutka",
    "предварительные знания": "predvaritelnye_znaniya",
    "тип урока": "tip_uroka",
    "межпредметные связи": "mezhpredmetnye_svyazi",
    "ориентация": "page_orientation",
}
_AFFIRMATIVE_VALUES = {"да", "есть", "нужна", "нужно", "нужны", "true", "1", "yes"}
_NEGATIVE_VALUES = {"нет", "отсутствует", "не нужна", "не нужно", "не нужны", "false", "0", "no"}
_LESSON_TYPES = {"комбинированный", "изучение нового материала", "закрепление", "контроль"}
_ALBUM_ORIENTATIONS = {"альбом", "альбомная", "альбомный", "album"}
_BOOK_ORIENTATIONS = {"книга", "книжная", "книжный", "book"}


def _parse_lesson_options_text(text: str) -> tuple[LessonOptions, list[str]]:
    """Разбирает свободнотекстовый ввод расширенных настроек урока в
    LessonOptions. Строки, ключ которых не распознан, возвращаются
    отдельным списком — вызывающий код честно говорит о них учителю,
    а не молчит и не выдумывает, что они значили."""
    options = LessonOptions()
    unrecognized: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            if line:
                unrecognized.append(line)
            continue

        key_part, _, value_part = line.partition(":")
        key_name = key_part.strip()
        field = _EXTRA_OPTION_KEY_ALIASES.get(key_name.lower())
        value = value_part.strip()
        if field is None or not value:
            unrecognized.append(line)
            continue

        if field == "cennost":
            key = find_value_key_by_name(value)
            if key is None:
                unrecognized.append(f"неизвестное значение «{value}» для «{key_name}»")
                continue
            options.cennost_key = key
        elif field == "adal_azamat_project":
            key = find_project_key_by_name(value)
            if key is None:
                unrecognized.append(f"неизвестное значение «{value}» для «{key_name}»")
                continue
            options.adal_azamat_project_key = key
        elif field == "vidy_deyatelnosti":
            options.vidy_deyatelnosti = [v.strip() for v in value.split(",") if v.strip()][:MAX_VIDY_DEYATELNOSTI]
        elif field in {"ima_oop", "sor", "fizkultminutka"}:
            normalized = value.lower()
            if normalized not in _AFFIRMATIVE_VALUES | _NEGATIVE_VALUES:
                unrecognized.append(
                    f"не понял значение «{value}» для «{key_name}» — напишите «да» или «нет»"
                )
                continue
            enabled = normalized in _AFFIRMATIVE_VALUES
            if field == "ima_oop":
                options.ima_oop = enabled
            elif field == "sor":
                options.sor_instead_of_reflection = enabled
            else:
                options.fizkultminutka = enabled
        elif field == "predvaritelnye_znaniya":
            options.predvaritelnye_znaniya = value
        elif field == "tip_uroka":
            if value.lower() not in _LESSON_TYPES:
                unrecognized.append(
                    f"не понял значение «{value}» для «{key_name}» — выберите один из указанных типов урока"
                )
                continue
            options.tip_uroka = value
        elif field == "mezhpredmetnye_svyazi":
            options.mezhpredmetnye_svyazi = [v.strip() for v in value.split(",") if v.strip()]
        elif field == "page_orientation":
            normalized = value.lower()
            if normalized in _ALBUM_ORIENTATIONS:
                options.page_orientation = "album"
            elif normalized in _BOOK_ORIENTATIONS:
                options.page_orientation = "book"
            else:
                unrecognized.append(
                    f"не понял значение «{value}» для «{key_name}» — напишите «альбомная» или «книжная»"
                )

    # __post_init__ уже отсёк vidy_deyatelnosti сверх лимита при создании
    # объекта конструктором, но поля выше присваивались после — доотсекаем.
    if len(options.vidy_deyatelnosti) > MAX_VIDY_DEYATELNOSTI:
        options.vidy_deyatelnosti = options.vidy_deyatelnosti[:MAX_VIDY_DEYATELNOSTI]

    return options, unrecognized


def _format_extra_options_summary(options_dict: dict | None) -> str:
    """Строка для сводки подтверждения — пусто, если ни одна доп.
    настройка не включена, чтобы не загромождать обычный (без Р5) путь."""
    if not options_dict:
        return ""
    options = LessonOptions(**options_dict)

    bits = []
    if options.cennost_key:
        value = get_value(options.cennost_key)
        if value:
            bits.append(f"ценность «{value['name']}»")
    if options.adal_azamat_project_key:
        project = get_project(options.adal_azamat_project_key)
        if project:
            bits.append(f"проект «{project['name']}»")
    if options.vidy_deyatelnosti:
        bits.append("виды деятельности: " + ", ".join(options.vidy_deyatelnosti))
    if options.ima_oop:
        bits.append("ООП")
    if options.sor_instead_of_reflection:
        bits.append("СОР вместо рефлексии")
    if options.fizkultminutka:
        bits.append("физкультминутка")
    if options.predvaritelnye_znaniya:
        bits.append(f"предзнания: {options.predvaritelnye_znaniya}")
    if options.tip_uroka:
        bits.append(f"тип урока: {options.tip_uroka}")
    if options.mezhpredmetnye_svyazi:
        bits.append("межпредм. связи: " + ", ".join(options.mezhpredmetnye_svyazi))
    if options.page_orientation == "album":
        bits.append("альбомная ориентация")

    if not bits:
        return ""
    return texts.GENERATE_EXTRA_OPTIONS_LINE.format(summary="; ".join(bits))


async def _ask_generate_topic(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_ASK_TOPIC
    if data.get("topic"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["topic"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_objective_code(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_ASK_OBJECTIVE_CODE
    if data.get("objective_code"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["objective_code"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_razdel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_ASK_RAZDEL
    if data.get("razdel"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["razdel"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_klass(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_ASK_KLASS
    if data.get("klass"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["klass"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_duration(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_ASK_DURATION
    if data.get("duration_minutes"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["duration_minutes"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_textbook_photos(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    count = len(data.get("textbook_photo_paths") or [])
    text = texts.GENERATE_ASK_TEXTBOOK_PHOTOS
    if count:
        text += texts.CURRENT_VALUE_NOTE.format(value=f"{count} фото принято", back="/done")
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_extra_options(message: Message, state: FSMContext) -> None:
    await message.answer(
        texts.GENERATE_OPTIONS_QUICK_PROMPT,
        reply_markup=keyboards.lesson_options_quick_keyboard(list_values(), list_projects()),
    )


async def _ask_generate_template(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    templates_list = list_templates(data["teacher_id"])
    if not templates_list:
        await message.answer(texts.GENERATE_NO_TEMPLATES, reply_markup=keyboards.MAIN_MENU)
        await state.clear()
        return

    rows = [
        [InlineKeyboardButton(text=t["name"], callback_data=f"gen_tpl:{t['id']}")]
        for t in templates_list
    ]
    await message.answer(texts.GENERATE_ASK_TEMPLATE, reply_markup=keyboards.with_back_row(rows))


@router.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return

    rows = query(
        "SELECT template_id, selected_at FROM template_selections WHERE telegram_user_id = ?",
        (message.from_user.id,),
    )
    # Выбор одноразовый: удаляем при первой /generate и при успехе, и при
    # истечении TTL, и если шаблон с тех пор стал недоступен.
    execute("DELETE FROM template_selections WHERE telegram_user_id = ?", (message.from_user.id,))

    selected_template = None
    if rows:
        selected_at = datetime.fromisoformat(str(rows[0]["selected_at"]).replace("Z", "+00:00"))
        if selected_at.tzinfo is None:
            selected_at = selected_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - selected_at <= TEMPLATE_SELECTION_TTL:
            available = {template["id"]: template for template in list_templates(teacher["id"])}
            selected_template = available.get(rows[0]["template_id"])

    await state.clear()
    await go_to(state, Generate.waiting_for_topic)
    state_data = {"teacher_id": teacher["id"], "subject": teacher["subject"]}
    if selected_template is not None:
        state_data["template_id"] = selected_template["id"]
        await message.answer(
            texts.TEMPLATES_CHOSEN.format(template_name=selected_template["name"]),
            reply_markup=keyboards.MAIN_MENU,
        )
    await state.update_data(**state_data)
    await _ask_generate_topic(message, state)


@router.callback_query(F.data.startswith("ksp_from_konspekt:"))
async def ksp_from_konspekt_pressed(callback: CallbackQuery, state: FSMContext) -> None:
    """К5: кнопка под готовым конспектом — сразу в /generate с
    предзаполненной темой (её уже определила модель по расшифровке,
    core.konspekt_generator) и текстом конспекта для build_prompt.

    Класс и код цели предзаполнить НЕЧЕМ и здесь не выдумываются: /konspekt
    (К2.3) их не спрашивает — только принимает аудио, в самой записи их
    тоже нет. Учитель вводит их как при обычном /generate — тот же диалог,
    просто с уже готовой темой (плановый текст К4.3/К5 предполагает
    предзаполнение и классом тоже; взять его неоткуда, а дописывать
    выдуманным значением — прямое нарушение Б6.2, отклонение записано в
    NIGHT_REPORT_STAGE2.md)."""
    konspekt_id = callback.data.split(":", 1)[1]

    teacher = _get_teacher(callback.from_user.id)
    if teacher is None:
        await callback.message.answer(texts.ERROR_NO_TEACHER_PROFILE)
        await callback.answer()
        return

    # Отсутствие записи и чужой konspekt_id дают один и тот же ответ — тот
    # же приём, что history_resend (не подтверждаем существование чужой
    # записи чужому пользователю).
    rows = query(
        "SELECT teacher_id, transcript_id, mode, content_json FROM konspekty WHERE id = ?",
        (konspekt_id,),
    )
    if not rows or rows[0]["teacher_id"] != teacher["id"]:
        await callback.answer(texts.KSP_FROM_KONSPEKT_NOT_FOUND, show_alert=True)
        return

    konspekt = rows[0]
    content = json.loads(konspekt["content_json"])
    konspekt_text = format_konspekt_text(content)

    if konspekt["mode"] == "teacher":
        transcript_rows = query(
            "SELECT text, duration_seconds FROM transcripts WHERE id = ? AND teacher_id = ?",
            (konspekt["transcript_id"], teacher["id"]),
        )
        if not transcript_rows:
            await callback.answer(texts.KSP_FROM_KONSPEKT_NOT_FOUND, show_alert=True)
            return

        transcript = transcript_rows[0]
        konspekt_text = transcript["text"]
        if (transcript["duration_seconds"] or 0) > LONG_KONSPEKT_TRANSCRIPT_SECONDS:
            await callback.message.answer(texts.KSP_FROM_KONSPEKT_LONG_TRANSCRIPT_WARNING)

    await state.clear()
    await go_to(state, Generate.waiting_for_topic)
    await state.update_data(
        teacher_id=teacher["id"],
        subject=teacher["subject"],
        konspekt_text=konspekt_text,
    )

    student_content = content.get("konspekt_uchenika", content)
    await _proceed_with_topic(callback.message, state, student_content["tema"])
    await callback.answer()


async def _proceed_with_topic(message: Message, state: FSMContext, topic: str) -> None:
    """Общая часть после того, как тема стала известна — что при обычном
    ручном вводе в /generate (generate_topic_received), что при
    предзаполнении темой из уже готового конспекта (К5,
    ksp_from_konspekt_pressed): код цели угадывается тем же способом в
    обоих случаях, не двумя разными."""
    data = await state.get_data()
    defaults = collect_generation_defaults(data["teacher_id"], topic)
    defaults["duration_minutes"] = 45
    defaults["sources"]["duration_minutes"] = "стандарт"
    if "template_id" not in defaults and all(defaults.get(field) is not None for field in ("razdel", "objective_code", "klass")):
        official = next((item for item in list_templates(data["teacher_id"]) if item["is_official"]), None)
        if official:
            defaults["template_id"] = official["id"]
            defaults["sources"]["template_id"] = "официальная форма №130"
    await state.update_data(**defaults)
    data = await state.get_data()

    if all(data.get(field) is not None for field in ("razdel", "objective_code", "klass", "duration_minutes", "template_id")):
        await _enter_generate_confirmation(message, state, data["template_id"])
        return

    code = data.get("objective_code") or guess_objective_code(data["teacher_id"], topic)
    await state.update_data(objective_code=code)

    if code:
        await message.answer(texts.GENERATE_OBJECTIVE_AUTO_FOUND.format(code=code))
        await go_to(state, Generate.waiting_for_razdel)
        await _ask_generate_razdel(message, state)
    else:
        await go_to(state, Generate.waiting_for_objective_code)
        await _ask_generate_objective_code(message, state)


# =====================================================================
# У5 (PLAN.md) — педагог отправляет готовый конспект пропустившему
# ученику. Кнопка «Отправить ученику» — на той же карточке готового
# конспекта, что и «Собрать КСП по этому конспекту» (make_konspekt_handler,
# ниже по файлу, в блоке обработчиков задач очереди).
#
# Решение принимает человек: ученик не может запросить конспект сам —
# для этого нет ни одной команды, — и автоматической рассылки классу
# нет и не появится в этом блоке (ловушка, дословно). Три шага
# (класс -> ученик -> отправка) — обычные inline-кнопки без FSM, тем
# же приёмом, что и у /sverka (блок У4): короче для короткого выбора,
# не текстовый ввод.
# =====================================================================


def _get_owned_student_konspekt(konspekt_id: str, teacher_id: int, db_path=None) -> dict | None:
    """Только mode='student' — учительский режим (голая расшифровка,
    блок К0) отправлять ученику нельзя ни при каком условии: это и есть
    полная запись урока, которую MASTER.md 0.9 п.3 запрещает выдавать."""
    rows = query(
        "SELECT id, teacher_id, tema, docx_path FROM konspekty WHERE id = ? AND teacher_id = ? AND mode = 'student'",
        (konspekt_id, teacher_id),
        db_path=db_path,
    )
    return dict(rows[0]) if rows else None


def _class_members_for_teacher(class_id: int, teacher_id: int, db_path=None) -> list[dict]:
    """JOIN на classes.teacher_id — тем же способом, что владение
    конспектом выше, отсекает чужой class_id, даже если он пришёл в
    callback_data подделанным."""
    rows = query(
        "SELECT s.id AS student_id, s.name AS name FROM class_members cm "
        "JOIN students s ON s.id = cm.student_id "
        "JOIN classes c ON c.id = cm.class_id "
        "WHERE cm.class_id = ? AND c.teacher_id = ? ORDER BY cm.joined_at",
        (class_id, teacher_id),
        db_path=db_path,
    )
    return [dict(row) for row in rows]


async def _show_send_konspekt_student_picker(message: Message, konspekt_id: str, class_id: int, teacher_id: int) -> None:
    members = _class_members_for_teacher(class_id, teacher_id)
    if not members:
        await message.answer(texts.SEND_KONSPEKT_NO_MEMBERS)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=m["name"] or texts.SEND_KONSPEKT_STUDENT_NO_NAME.format(id=m["student_id"]),
                callback_data=f"sks:{konspekt_id}:{m['student_id']}",
            )
        ]
        for m in members
    ]
    await message.answer(texts.SEND_KONSPEKT_ASK_STUDENT, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("sk:"))
async def send_konspekt_requested(callback: CallbackQuery) -> None:
    konspekt_id = callback.data.split(":", 1)[1]
    teacher = _get_teacher(callback.from_user.id)
    konspekt = _get_owned_student_konspekt(konspekt_id, teacher["id"]) if teacher else None
    if konspekt is None:
        await callback.answer(texts.SEND_KONSPEKT_NOT_FOUND, show_alert=True)
        return

    classes = query("SELECT id, name FROM classes WHERE teacher_id = ? ORDER BY created_at", (teacher["id"],))
    if not classes:
        await callback.message.answer(texts.SEND_KONSPEKT_NO_CLASSES)
        await callback.answer()
        return
    if len(classes) == 1:
        await _show_send_konspekt_student_picker(callback.message, konspekt_id, classes[0]["id"], teacher["id"])
        await callback.answer()
        return

    rows = [
        [InlineKeyboardButton(text=c["name"], callback_data=f"skc:{konspekt_id}:{c['id']}")] for c in classes
    ]
    await callback.message.answer(texts.SEND_KONSPEKT_ASK_CLASS, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("skc:"))
async def send_konspekt_class_chosen(callback: CallbackQuery) -> None:
    _, konspekt_id, class_id_text = callback.data.split(":", 2)
    teacher = _get_teacher(callback.from_user.id)
    konspekt = _get_owned_student_konspekt(konspekt_id, teacher["id"]) if teacher else None
    if konspekt is None:
        await callback.answer(texts.SEND_KONSPEKT_NOT_FOUND, show_alert=True)
        return
    await _show_send_konspekt_student_picker(callback.message, konspekt_id, int(class_id_text), teacher["id"])
    await callback.answer()


@router.callback_query(F.data.startswith("sks:"))
async def send_konspekt_student_chosen(callback: CallbackQuery, bot: Bot) -> None:
    _, konspekt_id, student_id_text = callback.data.split(":", 2)
    student_id = int(student_id_text)
    teacher = _get_teacher(callback.from_user.id)
    konspekt = _get_owned_student_konspekt(konspekt_id, teacher["id"]) if teacher else None
    if konspekt is None:
        await callback.answer(texts.SEND_KONSPEKT_NOT_FOUND, show_alert=True)
        return

    # Перепроверка владения студентом: student_id пришёл в callback_data
    # от клиента — список кнопок его показал сам бот, отфильтрованным по
    # классам ЭТОГО учителя, но доверять самому идентификатору без
    # проверки нельзя (тот же принцип, что konspekt/class выше). Без
    # этой проверки подделанный student_id отправил бы конспект чужому
    # ученику любого учителя в системе.
    student_rows = query(
        "SELECT s.telegram_id AS telegram_id, s.name AS name FROM students s "
        "JOIN class_members cm ON cm.student_id = s.id "
        "JOIN classes c ON c.id = cm.class_id "
        "WHERE s.id = ? AND c.teacher_id = ? LIMIT 1",
        (student_id, teacher["id"]),
    )
    if not student_rows:
        await callback.answer(texts.SEND_KONSPEKT_STUDENT_NOT_FOUND, show_alert=True)
        return

    if not konspekt["docx_path"] or not Path(konspekt["docx_path"]).exists():
        await callback.message.answer(texts.SEND_KONSPEKT_FILE_MISSING)
        await callback.answer()
        return

    caption = texts.SEND_KONSPEKT_CAPTION.format(teacher_name=teacher["name"], tema=konspekt["tema"])
    await bot.send_document(student_rows[0]["telegram_id"], FSInputFile(konspekt["docx_path"]), caption=caption)

    student_name = student_rows[0]["name"] or texts.SEND_KONSPEKT_STUDENT_NO_NAME.format(id=student_id)
    await callback.message.answer(texts.SEND_KONSPEKT_SENT.format(student_name=student_name))
    await callback.answer()


@router.message(Generate.waiting_for_topic)
async def generate_topic_received(message: Message, state: FSMContext) -> None:
    topic = (message.text or "").strip()
    if not topic:
        await _ask_generate_topic(message, state)
        return
    await _proceed_with_topic(message, state, topic)


@router.message(Generate.waiting_for_objective_code)
async def generate_objective_code_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    code = None if text in ("-", "") else text
    await state.update_data(objective_code=code)
    await go_to(state, Generate.waiting_for_razdel)
    await _ask_generate_razdel(message, state)


@router.message(Generate.waiting_for_razdel)
async def generate_razdel_received(message: Message, state: FSMContext) -> None:
    razdel = (message.text or "").strip()
    if not razdel:
        await _ask_generate_razdel(message, state)
        return
    await state.update_data(razdel=razdel)
    await go_to(state, Generate.waiting_for_klass)
    await _ask_generate_klass(message, state)


@router.message(Generate.waiting_for_klass)
async def generate_klass_received(message: Message, state: FSMContext) -> None:
    entered_klass = (message.text or "").strip()
    match = re.search(r"\d+", entered_klass)
    if match is None:
        await message.answer(texts.GENERATE_KLASS_NOT_A_NUMBER)
        return
    klass = match.group(0)
    if not klass:
        await _ask_generate_klass(message, state)
        return
    await state.update_data(klass=klass)
    await go_to(state, Generate.waiting_for_duration)
    await _ask_generate_duration(message, state)


@router.message(Generate.waiting_for_duration)
async def generate_duration_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_DURATION_NOT_A_NUMBER)
        return

    await state.update_data(duration_minutes=int(text))
    await go_to(state, Generate.waiting_for_textbook_photos)
    if not (await state.get_data()).get("textbook_photo_paths"):
        await state.update_data(textbook_photo_paths=[])
    await _ask_generate_textbook_photos(message, state)


_TEXTBOOK_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_TEXTBOOK_PHOTO_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
MAX_TEXTBOOK_PHOTOS = 3


@router.message(Generate.waiting_for_textbook_photos, Command("skip"))
async def generate_textbook_photos_skipped(message: Message, state: FSMContext) -> None:
    await state.update_data(textbook_photo_paths=[])
    await go_to(state, Generate.waiting_for_extra_options)
    await _ask_generate_extra_options(message, state)


@router.message(Generate.waiting_for_textbook_photos, Command("done"))
async def generate_textbook_photos_done(message: Message, state: FSMContext) -> None:
    await go_to(state, Generate.waiting_for_extra_options)
    await _ask_generate_extra_options(message, state)


@router.message(Generate.waiting_for_textbook_photos, F.photo)
async def generate_textbook_photo_received(message: Message, state: FSMContext, bot: Bot) -> None:
    """Р6.1: сжатое фото Telegram (message.photo) — самый частый случай,
    когда учитель просто фотографирует страницу и отправляет как обычно.
    Telegram сам пережимает такие фото в JPEG независимо от исходного
    формата, поэтому расширение всегда .jpg."""
    data = await state.get_data()
    paths = data.get("textbook_photo_paths", [])
    if len(paths) >= MAX_TEXTBOOK_PHOTOS:
        await message.answer(texts.GENERATE_TEXTBOOK_PHOTOS_MAX_REACHED)
        return

    largest = message.photo[-1]
    size_error = _check_file_size(largest)
    if size_error:
        await message.answer(size_error)
        return

    dest = settings.uploads_dir / f"{uuid.uuid4()}.jpg"
    await bot.download(largest, destination=dest)
    paths.append(str(dest))
    await state.update_data(textbook_photo_paths=paths)
    await message.answer(
        texts.GENERATE_TEXTBOOK_PHOTO_ACCEPTED.format(n=len(paths)), reply_markup=keyboards.back_cancel_keyboard()
    )


@router.message(Generate.waiting_for_textbook_photos, F.document)
async def generate_textbook_photo_document_received(message: Message, state: FSMContext, bot: Bot) -> None:
    """Фото, присланное файлом (не сжатым Telegram-фото) — так учитель
    сохраняет реальный PNG/WebP без пережатия в JPEG (Р6.1: "JPG/PNG/WebP")."""
    document = message.document
    filename = document.file_name or ""
    ext = Path(filename).suffix.lower()
    if ext not in _TEXTBOOK_PHOTO_EXTENSIONS:
        await message.answer(texts.GENERATE_TEXTBOOK_PHOTO_UNSUPPORTED_FORMAT)
        return

    data = await state.get_data()
    paths = data.get("textbook_photo_paths", [])
    if len(paths) >= MAX_TEXTBOOK_PHOTOS:
        await message.answer(texts.GENERATE_TEXTBOOK_PHOTOS_MAX_REACHED)
        return

    size_error = _check_file_size(document)
    if size_error:
        await message.answer(size_error)
        return

    dest = settings.uploads_dir / f"{uuid.uuid4()}{ext}"
    await bot.download(document, destination=dest)
    paths.append(str(dest))
    await state.update_data(textbook_photo_paths=paths)
    await message.answer(
        texts.GENERATE_TEXTBOOK_PHOTO_ACCEPTED.format(n=len(paths)), reply_markup=keyboards.back_cancel_keyboard()
    )


@router.message(Generate.waiting_for_textbook_photos)
async def generate_textbook_photos_wrong_input(message: Message) -> None:
    await message.answer(texts.GENERATE_TEXTBOOK_PHOTO_UNSUPPORTED_FORMAT, reply_markup=keyboards.back_cancel_keyboard())


@router.message(Generate.waiting_for_extra_options)
async def generate_extra_options_received(message: Message, state: FSMContext) -> None:
    """Р5.2/Р5.3: необязательный шаг. "-" (или пусто) — пропустить, ничего
    не меняется по сравнению с тем, что было до блока Р5. Формат —
    свободные строки "Ключ: значение", парсер терпим к порядку и
    отсутствию части строк (см. _parse_lesson_options_text)."""
    text = (message.text or "").strip()
    if text and text != "-":
        options, unrecognized = _parse_lesson_options_text(text)
        if unrecognized:
            await message.answer(
                texts.GENERATE_EXTRA_OPTIONS_UNRECOGNIZED_NOTE.format(lines=", ".join(unrecognized))
            )
            return
    else:
        options = LessonOptions()

    await state.update_data(options=asdict(options))

    data = await state.get_data()

    # Шаблон мог быть выбран заранее в Mini App (templates_web_app_choice) —
    # тогда спрашивать его второй раз незачем, сразу к подтверждению. Стек
    # навигации при этом фиксирует РЕАЛЬНЫЙ путь (extra_options -> confirmation,
    # без промежуточного template) — «Назад» с подтверждения в этом случае
    # вернёт сюда, а не на несуществующий для этого пути выбор шаблона
    # (М3.1, обоснование "почему стек, а не список").
    if data.get("template_id"):
        shown = await _enter_generate_confirmation(message, state, data["template_id"])
        if not shown:
            await message.answer(texts.GENERATE_NO_TEMPLATES, reply_markup=keyboards.MAIN_MENU)
            await state.clear()
        return

    await go_to(state, Generate.waiting_for_template)
    await _ask_generate_template(message, state)


@router.callback_query(Generate.waiting_for_extra_options, F.data.startswith("opt:"))
async def generate_option_button_pressed(callback: CallbackQuery, state: FSMContext) -> None:
    """Ф2: кнопки меняют тот же LessonOptions, что и прежний текстовый ввод."""
    choice = callback.data.split(":", 1)[1]
    if choice == "more":
        await callback.message.answer(texts.GENERATE_ASK_EXTRA_OPTIONS, reply_markup=keyboards.back_cancel_keyboard())
        await callback.answer()
        return
    if choice == "done":
        await generate_extra_options_received(type("Message", (), {"text": "-", "answer": callback.message.answer})(), state)
        await callback.answer()
        return
    data = await state.get_data()
    options = LessonOptions(**(data.get("options") or {}))
    if choice.startswith("value:"):
        options.cennost_key = None if choice == "value:none" else choice.split(":", 1)[1]
        await state.update_data(options=asdict(options))
        await callback.answer(texts.GENERATE_OPTIONS_VALUE_SELECTED)
        return
    if choice.startswith("project:"):
        options.adal_azamat_project_key = None if choice == "project:none" else choice.split(":", 1)[1]
        await state.update_data(options=asdict(options))
        await callback.answer(texts.GENERATE_OPTIONS_PROJECT_SELECTED)
        return
    lesson_types = {"type:combined": "комбинированный", "type:new": "изучение нового материала", "type:practice": "закрепление", "type:control": "контроль"}
    if choice in lesson_types:
        options.tip_uroka = lesson_types[choice]
        await state.update_data(options=asdict(options))
        await callback.answer(texts.GENERATE_OPTIONS_TYPE_SELECTED)
        return
    if choice in {"orientation:album", "orientation:book"}:
        options.page_orientation = "album" if choice.endswith("album") else "book"
        await state.update_data(options=asdict(options))
        await callback.answer(texts.GENERATE_OPTIONS_ORIENTATION_SELECTED)
        return
    field = {"ima_oop": "ima_oop", "sor": "sor_instead_of_reflection", "fiz": "fizkultminutka"}[choice]
    setattr(options, field, not getattr(options, field))
    await state.update_data(options=asdict(options))
    await callback.answer("Настройка изменена")


async def _render_generate_confirmation(message: Message, state: FSMContext) -> bool:
    """Строит и отправляет сводку + кнопки да/нет/назад по текущим данным
    состояния — БЕЗ переключения состояния. Используется и для входа в шаг
    (после _enter_generate_confirmation переключит состояние), и для
    повторного показа при «Назад» с более позднего шага (тогда состояние
    уже переключено самим go_back, второй раз переключать нельзя — иначе
    в стек попадёт дубль). Возвращает False, если шаблон не найден или не
    выбран вовсе (защитная ветка — на реальном пути такого не бывает,
    т.к. _enter_generate_confirmation не пускает сюда без template_id)."""
    data = await state.get_data()
    template_id = data.get("template_id")
    if template_id is None:
        return False
    template = get_template(template_id)
    if template is None:
        return False

    photo_paths = data.get("textbook_photo_paths") or []
    sources = data.get("sources") or {}
    def shown(field: str, value: object) -> str:
        source = sources.get(field)
        return f"{value}{texts.GENERATE_CONFIRM_SOURCE.format(source=source)}" if source else str(value)
    summary = texts.GENERATE_CONFIRM_SUMMARY.format(
        topic=data["topic"],
        razdel=shown("razdel", data["razdel"]),
        objective_code=shown("objective_code", data.get("objective_code") or "не указан"),
        klass=shown("klass", data["klass"]),
        duration=shown("duration_minutes", data["duration_minutes"]),
        template_name=shown("template_id", template["name"]),
        textbook_photos_line=texts.GENERATE_TEXTBOOK_PHOTOS_LINE.format(count=len(photo_paths)) if photo_paths else "",
        extra_options_line=_format_extra_options_summary(data.get("options")),
    )
    if not _has_style_profile(data["teacher_id"]):
        summary += texts.GENERATE_NO_STYLE_PROFILE_NOTE

    if data.get("sources"):
        rows = [[
            InlineKeyboardButton(text=texts.GENERATE_FAST_CONFIRM_BUTTON, callback_data="gen_confirm"),
            InlineKeyboardButton(text=texts.GENERATE_CHANGE_BUTTON, callback_data="gen_change"),
            InlineKeyboardButton(text=texts.GENERATE_CANCEL_BUTTON, callback_data="gen_cancel"),
        ]]
    else:
        rows = [[
            InlineKeyboardButton(text=texts.GENERATE_CONFIRM_BUTTON, callback_data="gen_confirm"),
            InlineKeyboardButton(text=texts.GENERATE_CANCEL_BUTTON, callback_data="gen_cancel"),
        ]]
    await message.answer(summary, reply_markup=keyboards.with_back_row(rows))
    return True


async def _enter_generate_confirmation(message: Message, state: FSMContext, template_id: int) -> bool:
    """Переход НА шаг подтверждения (в отличие от _render_generate_confirmation
    выше) — вызывается из forward-хода диалога (выбор шаблона инлайн-кнопкой,
    или шаблон уже был выбран в Mini App), сам кладёт текущее состояние в
    стек через go_to. Возвращает False, если шаблон за это время исчез —
    вызывающий код должен сообщить об этом и сам решить, что делать дальше
    (см. два разных сообщения об ошибке у двух мест вызова)."""
    template = get_template(template_id)
    if template is None:
        return False
    await go_to(state, Generate.waiting_for_confirmation)
    await state.update_data(template_id=template_id)
    return await _render_generate_confirmation(message, state)


@router.callback_query(Generate.waiting_for_template, F.data.startswith("gen_tpl:"))
async def generate_template_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    template_id = int(callback.data.split(":", 1)[1])

    shown = await _enter_generate_confirmation(callback.message, state, template_id)
    if not shown:
        await callback.answer("Такого шаблона больше нет, попробуйте /generate заново", show_alert=True)
        return

    await callback.answer()


@router.callback_query(Generate.waiting_for_confirmation, F.data == "gen_cancel")
async def generate_cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.GENERATE_CANCELLED, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


@router.callback_query(Generate.waiting_for_confirmation, F.data == "gen_change")
async def generate_change_requested(callback: CallbackQuery, state: FSMContext) -> None:
    """Ф1: оставляет рабочий подробный путь доступным до кнопочного Ф2."""
    await go_to(state, Generate.waiting_for_objective_code)
    await _ask_generate_objective_code(callback.message, state)
    await callback.answer()


@router.callback_query(Generate.waiting_for_confirmation, F.data == "gen_confirm")
async def generate_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    # М6.2: проверка ДО постановки в очередь — после генерации уже
    # потратили бы то, что хотели сэкономить.
    if not await _check_and_report_limits(callback.message, callback.from_user.id, "generate_ksp"):
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    payload = {
        "teacher_id": data["teacher_id"],
        "template_id": data["template_id"],
        "topic": data["topic"],
        "razdel": data["razdel"],
        "subject": data["subject"],
        "klass": data["klass"],
        "duration_minutes": data["duration_minutes"],
        "objective_code": data.get("objective_code"),
        "options": data.get("options"),  # Р5.2/Р5.3, словарь полей LessonOptions или None
        "textbook_photo_paths": data.get("textbook_photo_paths") or [],  # Р6.1
        "konspekt_text": data.get("konspekt_text"),  # К5
    }
    enqueue("generate_ksp", payload, chat_id=callback.message.chat.id)
    await state.clear()
    await callback.message.answer(texts.GENERATE_QUEUED, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


# =====================================================================
# /generate_ktp — FSM: предмет -> класс -> часов в неделю -> часов в год
#                 -> темы -> подтверждение -> очередь (блок Р4.3)
#
# Через очередь, не синхронно (в отличие от /upload_ktp): это генерация
# через LLM, а не разбор файла — на реальном прогоне занимала от 30 до
# больше 100 секунд для полного учебного года (ktp_generator.py). Держать
# это внутри одного хендлера значило бы держать диалог пользователя
# висящим без ретраев и без гарантии уведомления при сбое — то, ради чего
# вообще существует core/queue.py.
# =====================================================================


async def _ask_generate_ktp_predmet(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_KTP_ASK_PREDMET
    if data.get("predmet"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["predmet"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_ktp_klass(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_KTP_ASK_KLASS
    if data.get("klass"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["klass"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_ktp_hours_week(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_KTP_ASK_HOURS_WEEK
    if data.get("chasov_v_nedelu"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["chasov_v_nedelu"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_ktp_hours_year(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.GENERATE_KTP_ASK_HOURS_YEAR
    if data.get("chasov_v_god"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["chasov_v_god"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_ktp_topics(message: Message, state: FSMContext) -> None:
    await message.answer(texts.GENERATE_KTP_ASK_TOPICS, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_generate_ktp_confirmation(message: Message, state: FSMContext) -> None:
    """Строит сводку по текущим данным состояния — без переключения
    состояния. В отличие от Generate.waiting_for_confirmation здесь только
    один путь входа (после /generate_ktp темы всегда спрашиваются), поэтому
    не нужен отдельный "enter"-вариант с параметром — всё уже в data."""
    data = await state.get_data()
    topics = data.get("topics") or []
    summary = texts.GENERATE_KTP_CONFIRM_SUMMARY.format(
        predmet=data["predmet"],
        klass=data["klass"],
        hours_week=data["chasov_v_nedelu"],
        hours_year=data["chasov_v_god"],
        topics_count=len(topics) if topics else "не даны, составлю сам",
    )
    rows = [
        [
            InlineKeyboardButton(text=texts.GENERATE_KTP_CONFIRM_BUTTON, callback_data="genktp_confirm"),
            InlineKeyboardButton(text=texts.GENERATE_KTP_CANCEL_BUTTON, callback_data="genktp_cancel"),
        ]
    ]
    await message.answer(summary, reply_markup=keyboards.with_back_row(rows))


@router.message(Command("generate_ktp"))
async def cmd_generate_ktp(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.clear()
    await go_to(state, GenerateKTP.waiting_for_predmet)
    await state.update_data(teacher_id=teacher["id"])
    await _ask_generate_ktp_predmet(message, state)


@router.message(GenerateKTP.waiting_for_predmet)
async def generate_ktp_predmet_received(message: Message, state: FSMContext) -> None:
    predmet = (message.text or "").strip()
    if not predmet:
        await _ask_generate_ktp_predmet(message, state)
        return
    await state.update_data(predmet=predmet)
    await go_to(state, GenerateKTP.waiting_for_klass)
    await _ask_generate_ktp_klass(message, state)


@router.message(GenerateKTP.waiting_for_klass)
async def generate_ktp_klass_received(message: Message, state: FSMContext) -> None:
    klass = (message.text or "").strip()
    if not klass:
        await _ask_generate_ktp_klass(message, state)
        return
    await state.update_data(klass=klass)
    await go_to(state, GenerateKTP.waiting_for_hours_week)
    await _ask_generate_ktp_hours_week(message, state)


@router.message(GenerateKTP.waiting_for_hours_week)
async def generate_ktp_hours_week_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_KTP_HOURS_NOT_A_NUMBER)
        return
    await state.update_data(chasov_v_nedelu=int(text))
    await go_to(state, GenerateKTP.waiting_for_hours_year)
    await _ask_generate_ktp_hours_year(message, state)


@router.message(GenerateKTP.waiting_for_hours_year)
async def generate_ktp_hours_year_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_KTP_HOURS_NOT_A_NUMBER)
        return
    await state.update_data(chasov_v_god=int(text))
    await go_to(state, GenerateKTP.waiting_for_topics)
    await _ask_generate_ktp_topics(message, state)


@router.message(GenerateKTP.waiting_for_topics)
async def generate_ktp_topics_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    topics = [] if text in ("-", "") else [line.strip() for line in text.splitlines() if line.strip()]
    await state.update_data(topics=topics)
    await go_to(state, GenerateKTP.waiting_for_confirmation)
    await _ask_generate_ktp_confirmation(message, state)


@router.callback_query(GenerateKTP.waiting_for_confirmation, F.data == "genktp_cancel")
async def generate_ktp_cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.GENERATE_KTP_CANCELLED, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


@router.callback_query(GenerateKTP.waiting_for_confirmation, F.data == "genktp_confirm")
async def generate_ktp_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _check_and_report_limits(callback.message, callback.from_user.id, "generate_ktp"):
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    payload = {
        "teacher_id": data["teacher_id"],
        "predmet": data["predmet"],
        "klass": data["klass"],
        "chasov_v_nedelu": data["chasov_v_nedelu"],
        "chasov_v_god": data["chasov_v_god"],
        "topics": data.get("topics") or None,
    }
    enqueue("generate_ktp", payload, chat_id=callback.message.chat.id)
    await state.clear()
    await callback.message.answer(texts.GENERATE_KTP_QUEUED, reply_markup=keyboards.MAIN_MENU)
    await callback.answer()


# =====================================================================
# /status
# =====================================================================


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    rows = query(
        "SELECT type, status, retries FROM tasks "
        "WHERE telegram_chat_id = ? AND status IN ('pending', 'processing') "
        "ORDER BY created_at",
        (message.chat.id,),
    )
    if not rows:
        await message.answer(texts.STATUS_EMPTY)
        return

    lines = [texts.STATUS_HEADER]
    for row in rows:
        lines.append(
            texts.STATUS_ROW.format(
                status_emoji=texts.STATUS_EMOJI.get(row["status"], ""),
                type_label=texts.TASK_TYPE_LABELS.get(row["type"], row["type"]),
                status_label=texts.STATUS_LABELS.get(row["status"], row["status"]),
                retries=row["retries"],
                max_retries=MAX_RETRIES,
            )
        )
    await message.answer("\n".join(lines))


# =====================================================================
# /history
# =====================================================================


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return

    rows = query(
        "SELECT id, content_json, created_at FROM generated_ksp "
        "WHERE teacher_id = ? ORDER BY created_at DESC LIMIT 10",
        (teacher["id"],),
    )
    if not rows:
        await message.answer(texts.HISTORY_EMPTY)
        return

    keyboard_rows = []
    for row in rows:
        content = json.loads(row["content_json"]) if row["content_json"] else {}
        topic = content.get("tema_uroka", "?")
        date_str = str(row["created_at"])[:10]
        button_text = texts.HISTORY_ROW_BUTTON.format(date=date_str, topic=topic)[:64]
        keyboard_rows.append([InlineKeyboardButton(text=button_text, callback_data=f"hist:{row['id']}")])

    await message.answer(texts.HISTORY_HEADER, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows))


# =====================================================================
# /class (У2, PLAN.md) — педагог создаёт класс и выдаёт код приглашения.
# Ученика и вступление по коду добавляет только блок У3 — здесь класс
# существует, но вступить в него пока некому.
# =====================================================================

# Алфавит инвайт-кода — без 0/O/1/I/L: цифры и буквы, которые визуально
# путаются что на экране, что при попытке продиктовать код вслух
# (ловушка блока У1, дословно "без похожих символов (0/O, 1/l)"; I и L
# исключены той же логикой — код всегда отображается заглавными буквами,
# а заглавная L от цифры 1 отличается не больше, чем строчная l).
_INVITE_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_INVITE_CODE_LENGTH = 6
_INVITE_CODE_MAX_ATTEMPTS = 10


def _generate_invite_code(db_path=None) -> str:
    """Короткий код, читаемый вслух; уникальность проверяется у самой
    базы, а не предполагается по размеру алфавита (2.7: не подставлять
    правдоподобное вместо проверенного) — при 32 символах и длине 6
    (32**6 ≈ 1.07 млрд комбинаций) коллизия на масштабе пилота
    практически невозможна, но убедиться дешевле, чем гадать."""
    for _ in range(_INVITE_CODE_MAX_ATTEMPTS):
        code = "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(_INVITE_CODE_LENGTH))
        existing = query("SELECT 1 FROM classes WHERE invite_code = ?", (code,), db_path=db_path)
        if not existing:
            return code
    raise RuntimeError("не удалось подобрать уникальный код приглашения за отведённое число попыток")


def _count_class_members(class_id: int, db_path=None) -> int:
    rows = query("SELECT COUNT(*) AS n FROM class_members WHERE class_id = ?", (class_id,), db_path=db_path)
    return rows[0]["n"]


def _class_list_keyboard(classes: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=texts.CLASS_LIST_ROW_BUTTON.format(name=c["name"], count=_count_class_members(c["id"]))[:64],
                callback_data=f"class_view:{c['id']}",
            )
        ]
        for c in classes
    ]
    rows.append([InlineKeyboardButton(text=texts.CLASS_CREATE_BUTTON, callback_data="class_create")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _class_card_keyboard(class_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.CLASS_REGEN_BUTTON, callback_data=f"class_regen:{class_id}")],
            [InlineKeyboardButton(text=texts.CLASS_DELETE_BUTTON, callback_data=f"class_delete:{class_id}")],
            [InlineKeyboardButton(text=texts.CLASS_BACK_TO_LIST_BUTTON, callback_data="class_list")],
        ]
    )


def _class_delete_confirm_keyboard(class_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.CLASS_DELETE_CONFIRM_BUTTON, callback_data=f"class_delete_confirm:{class_id}"
                ),
                InlineKeyboardButton(
                    text=texts.CLASS_DELETE_CANCEL_BUTTON, callback_data=f"class_delete_cancel:{class_id}"
                ),
            ]
        ]
    )


async def _send_class_list(message: Message, teacher_id: int) -> None:
    classes = query(
        "SELECT id, name, subject, invite_code FROM classes WHERE teacher_id = ? ORDER BY created_at",
        (teacher_id,),
    )
    if not classes:
        await message.answer(
            texts.CLASS_LIST_EMPTY,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.CLASS_CREATE_BUTTON, callback_data="class_create")]]
            ),
        )
        return
    await message.answer(texts.CLASS_LIST_HEADER, reply_markup=_class_list_keyboard([dict(row) for row in classes]))


@router.message(Command("class"))
async def cmd_class(message: Message) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await _send_class_list(message, teacher["id"])


@router.callback_query(F.data == "class_list")
async def class_list_requested(callback: CallbackQuery) -> None:
    teacher = _get_teacher(callback.from_user.id)
    if teacher is not None:
        await _send_class_list(callback.message, teacher["id"])
    await callback.answer()


async def _ask_class_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.CLASS_ASK_NAME
    if data.get("name"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["name"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _ask_class_subject(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = texts.CLASS_ASK_SUBJECT
    if data.get("subject"):
        text += texts.CURRENT_VALUE_NOTE.format(value=data["subject"], back=texts.BUTTON_BACK)
    await message.answer(text, reply_markup=keyboards.back_cancel_keyboard())


async def _render_class_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    summary = texts.CLASS_CONFIRM_SUMMARY.format(
        name=data["name"], subject=data.get("subject") or texts.CLASS_CONFIRM_NO_SUBJECT
    )
    rows = [[InlineKeyboardButton(text=texts.CLASS_CONFIRM_DONE_BUTTON, callback_data="class_confirm_create")]]
    await message.answer(summary, reply_markup=keyboards.with_back_row(rows))


@router.callback_query(F.data == "class_create")
async def class_create_started(callback: CallbackQuery, state: FSMContext) -> None:
    teacher = _get_teacher(callback.from_user.id)
    if teacher is None:
        await callback.answer()
        return
    await go_to(state, ClassCreate.waiting_for_name)
    await _ask_class_name(callback.message, state)
    await callback.answer()


@router.message(ClassCreate.waiting_for_name)
async def class_name_received(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await _ask_class_name(message, state)
        return
    await state.update_data(name=name)
    await go_to(state, ClassCreate.waiting_for_subject)
    await _ask_class_subject(message, state)


@router.message(ClassCreate.waiting_for_subject)
async def class_subject_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    subject = None if text in ("-", "") else text
    await state.update_data(subject=subject)
    await go_to(state, ClassCreate.waiting_for_confirmation)
    await _render_class_confirmation(message, state)


@router.callback_query(ClassCreate.waiting_for_confirmation, F.data == "class_confirm_create")
async def class_create_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
    teacher = _get_teacher(callback.from_user.id)
    if teacher is None:
        await state.clear()
        await callback.answer()
        return
    data = await state.get_data()
    name = data["name"]
    subject = data.get("subject")

    invite_code = _generate_invite_code()
    execute(
        "INSERT INTO classes (teacher_id, name, subject, invite_code) VALUES (?, ?, ?, ?)",
        (teacher["id"], name, subject, invite_code),
    )
    await state.clear()

    await callback.message.answer(texts.CLASS_CREATED.format(name=name), reply_markup=keyboards.MAIN_MENU)
    # Код приглашения — отдельным сообщением, крупно (У2, дословно план):
    # его показывают с экрана или диктуют вслух, смешивать с другим
    # текстом неудобно копировать/фотографировать.
    await callback.message.answer(texts.CLASS_INVITE_CODE_MESSAGE.format(name=name, code=invite_code))
    await callback.answer()


def _get_teacher_class(class_id: int, teacher_id: int, db_path=None) -> dict | None:
    rows = query(
        "SELECT id, name, subject, invite_code FROM classes WHERE id = ? AND teacher_id = ?",
        (class_id, teacher_id),
        db_path=db_path,
    )
    return dict(rows[0]) if rows else None


async def _send_class_card(message: Message, class_row: dict) -> None:
    count = _count_class_members(class_row["id"])
    text = texts.CLASS_CARD.format(
        name=class_row["name"],
        subject=class_row["subject"] or texts.CLASS_CONFIRM_NO_SUBJECT,
        count=count,
        code=class_row["invite_code"],
    )
    await message.answer(text, reply_markup=_class_card_keyboard(class_row["id"]))


@router.callback_query(F.data.startswith("class_view:"))
async def class_view_requested(callback: CallbackQuery) -> None:
    class_id = int(callback.data.split(":", 1)[1])
    teacher = _get_teacher(callback.from_user.id)
    class_row = _get_teacher_class(class_id, teacher["id"]) if teacher else None
    if class_row is None:
        await callback.answer(texts.CLASS_NOT_FOUND, show_alert=True)
        return
    await _send_class_card(callback.message, class_row)
    await callback.answer()


@router.callback_query(F.data.startswith("class_regen:"))
async def class_regen_requested(callback: CallbackQuery) -> None:
    class_id = int(callback.data.split(":", 1)[1])
    teacher = _get_teacher(callback.from_user.id)
    class_row = _get_teacher_class(class_id, teacher["id"]) if teacher else None
    if class_row is None:
        await callback.answer(texts.CLASS_NOT_FOUND, show_alert=True)
        return
    new_code = _generate_invite_code()
    execute("UPDATE classes SET invite_code = ? WHERE id = ?", (new_code, class_id))
    await callback.message.answer(texts.CLASS_REGENERATED.format(name=class_row["name"], code=new_code))
    await callback.answer()


@router.callback_query(F.data.startswith("class_delete:"))
async def class_delete_requested(callback: CallbackQuery) -> None:
    class_id = int(callback.data.split(":", 1)[1])
    teacher = _get_teacher(callback.from_user.id)
    class_row = _get_teacher_class(class_id, teacher["id"]) if teacher else None
    if class_row is None:
        await callback.answer(texts.CLASS_NOT_FOUND, show_alert=True)
        return
    await callback.message.answer(
        texts.CLASS_DELETE_CONFIRM.format(name=class_row["name"]),
        reply_markup=_class_delete_confirm_keyboard(class_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("class_delete_confirm:"))
async def class_delete_confirmed(callback: CallbackQuery) -> None:
    class_id = int(callback.data.split(":", 1)[1])
    teacher = _get_teacher(callback.from_user.id)
    class_row = _get_teacher_class(class_id, teacher["id"]) if teacher else None
    if class_row is None:
        await callback.answer(texts.CLASS_NOT_FOUND, show_alert=True)
        return
    # Только связь с классом — не сами ученики (У1, ловушка): удаление
    # класса не удаляет учеников, они могут состоять и в других классах.
    execute("DELETE FROM class_members WHERE class_id = ?", (class_id,))
    execute("DELETE FROM classes WHERE id = ?", (class_id,))
    await callback.message.answer(texts.CLASS_DELETED.format(name=class_row["name"]))
    await callback.answer()


@router.callback_query(F.data.startswith("class_delete_cancel:"))
async def class_delete_cancelled(callback: CallbackQuery) -> None:
    await callback.message.answer(texts.CLASS_DELETE_CANCELLED)
    await callback.answer()


# =====================================================================
# /delete_my_data (Ю2, PLAN.md) — статьи 18, 24, 25 Закона РК «О
# персональных данных и их защите» № 94-V: субъект вправе отозвать
# согласие, данные подлежат уничтожению по достижении цели сбора.
#
# Аудиозапись удаляется сразу после расшифровки уже сейчас (не эта
# команда) — здесь про то, что оставалось навсегда: транскрипты,
# конспекты, профиль стиля, загруженные для /upload_ksp файлы и записи
# о сгенерированных документах. usage_daily и incidents — обезличенная
# статистика и журнал инцидентов, персональных данных в них нет, не
# удаляются (сказано прямо в тексте подтверждения, не молчанием).
#
# Блок Э3 добавил сюда же удаление строки consents: удаление данных —
# это и есть отзыв согласия по тем же статьям 18/24/25, которые прямо
# выше в этом комментарии, а без удаления самой строки нечего было бы
# держать в синхронизации с _consent_given_cache. Следствие для
# пользователя прямо в тексте подтверждения: после удаления согласие
# нужно будет принять заново.
#
# Файлы, загруженные для /upload_ksp, не удаляются автоматически после
# разбора (в отличие от аудио) — их пути нигде не хранятся, кроме
# payload задач parse_ksp в tasks. Отдельной таблицы под них в схеме
# нет, заводить её ради одной команды удаления — лишняя сущность;
# tasks и так единственный источник правды о том, что было загружено.
# =====================================================================


def _uploaded_ksp_file_paths(telegram_user_id: int) -> list[str]:
    rows = query(
        "SELECT payload FROM tasks WHERE type = 'parse_ksp' AND telegram_chat_id = ?",
        (telegram_user_id,),
    )
    paths: list[str] = []
    for row in rows:
        payload = json.loads(row["payload"]) if row["payload"] else {}
        paths.extend(payload.get("file_paths", []))
    return paths


def _count_personal_data(teacher_id: int, telegram_user_id: int) -> dict:
    transcripts = query("SELECT COUNT(*) AS c FROM transcripts WHERE teacher_id = ?", (teacher_id,))[0]["c"]
    konspekty_count = query("SELECT COUNT(*) AS c FROM konspekty WHERE teacher_id = ?", (teacher_id,))[0]["c"]
    generated_count = query("SELECT COUNT(*) AS c FROM generated_ksp WHERE teacher_id = ?", (teacher_id,))[0]["c"]
    return {
        "transcripts": transcripts,
        "konspekty": konspekty_count,
        "generated_ksp": generated_count,
        "style_profile": _has_style_profile(teacher_id),
        "uploaded_files": len(_uploaded_ksp_file_paths(telegram_user_id)),
    }


def _unlink_quietly(path_str: str | None) -> None:
    if not path_str:
        return
    try:
        Path(path_str).unlink(missing_ok=True)
    except OSError:
        logger.warning("/delete_my_data: не удалось удалить файл %s", path_str)


def _delete_personal_data(teacher_id: int, telegram_user_id: int) -> dict:
    """Порядок обязателен: сначала файлы с диска, потом строки в базе —
    иначе останутся файлы-сироты, на которые уже никто не ссылается
    (Ю2, ловушка). Внутри базы — konspekty раньше transcripts: у
    konspekty.transcript_id внешний ключ на transcripts, удаление
    родителя первым упадёт на FOREIGN KEY (что в SQLite, что в Postgres).

    Э3: удаление данных — это и есть отзыв согласия (статьи 18, 24, 25
    Закона о ПДн, те же, что в основании блока Ю2: "данные подлежат
    уничтожению... по отзыву согласия; субъект вправе отозвать
    согласие"), поэтому здесь же удаляется строка consents и запись
    выкидывается из _consent_given_cache — иначе кэш продолжал бы
    считать согласие данным до перезапуска процесса, а следующее
    сообщение пользователя прошло бы _consent_gate без проверки."""
    counts = _count_personal_data(teacher_id, telegram_user_id)

    for path_str in _uploaded_ksp_file_paths(telegram_user_id):
        _unlink_quietly(path_str)

    for row in query("SELECT docx_path FROM konspekty WHERE teacher_id = ?", (teacher_id,)):
        _unlink_quietly(row["docx_path"])
    execute("DELETE FROM konspekty WHERE teacher_id = ?", (teacher_id,))

    for row in query("SELECT docx_path FROM generated_ksp WHERE teacher_id = ?", (teacher_id,)):
        _unlink_quietly(row["docx_path"])
    execute("DELETE FROM generated_ksp WHERE teacher_id = ?", (teacher_id,))

    execute("DELETE FROM transcripts WHERE teacher_id = ?", (teacher_id,))
    execute("DELETE FROM style_profiles WHERE teacher_id = ?", (teacher_id,))
    execute("DELETE FROM consents WHERE telegram_user_id = ?", (telegram_user_id,))
    _consent_given_cache.discard(telegram_user_id)

    return counts


def _format_delete_my_data_counts(template: str, counts: dict) -> str:
    return template.format(
        transcripts=counts["transcripts"],
        konspekty=counts["konspekty"],
        generated_ksp=counts["generated_ksp"],
        style_profile=texts.DELETE_MY_DATA_YES if counts["style_profile"] else texts.DELETE_MY_DATA_NO,
        uploaded_files=counts["uploaded_files"],
    )


def _delete_my_data_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.DELETE_MY_DATA_CONFIRM_BUTTON, callback_data="delete_my_data_confirm"
                ),
                InlineKeyboardButton(
                    text=texts.DELETE_MY_DATA_CANCEL_BUTTON, callback_data="delete_my_data_cancel"
                ),
            ]
        ]
    )


@router.message(Command("delete_my_data"))
async def cmd_delete_my_data(message: Message) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    counts = _count_personal_data(teacher["id"], message.from_user.id)
    if not any(counts.values()):
        await message.answer(texts.DELETE_MY_DATA_NOTHING_TO_DELETE)
        return
    text = _format_delete_my_data_counts(texts.DELETE_MY_DATA_SUMMARY, counts)
    await message.answer(text, reply_markup=_delete_my_data_keyboard())


@router.callback_query(F.data == "delete_my_data_confirm")
async def delete_my_data_confirmed(callback: CallbackQuery) -> None:
    teacher = _get_teacher(callback.from_user.id)
    if teacher is None:
        await callback.answer()
        return
    counts = _delete_personal_data(teacher["id"], callback.from_user.id)
    await callback.message.answer(_format_delete_my_data_counts(texts.DELETE_MY_DATA_DONE, counts))
    await callback.answer()


@router.callback_query(F.data == "delete_my_data_cancel")
async def delete_my_data_cancelled(callback: CallbackQuery) -> None:
    await callback.message.answer(texts.DELETE_MY_DATA_CANCELLED)
    await callback.answer()


# =====================================================================
# /dashboard — текстовая сводка (М5.2). core.dashboard.collect() — ЕДИНСТВЕННЫЙ
# расчёт (М5.1); web/api.py (М5.3) форматирует тот же collect() под JSON,
# а не считает заново.
# =====================================================================


def format_dashboard_text(data: dict) -> str:
    """Форматирование текстовой сводки дашборда — используется и /dashboard,
    и тестами, сверяющими бот с Mini App на одних и тех же данных (М5.3 КГ).
    Не в core/dashboard.py: там только расчёт, текст для конкретного канала —
    дело вызывающего кода (см. шапку модуля core/dashboard.py)."""
    if not data["has_profile"]:
        # М7.4: живучесть не про конкретного учителя — показывается даже
        # без профиля, в отличие от остального дашборда.
        return texts.DASHBOARD_NO_PROFILE + _format_uptime_text(data["uptime"])

    text = texts.DASHBOARD_HEADER
    text += texts.DASHBOARD_QUEUE.format(**data["queue"])
    text += texts.DASHBOARD_GENERATED_KSP.format(**data["generated_ksp"])
    text += texts.DASHBOARD_KTP_COVERAGE.format(**data["ktp_coverage"])
    if data["unparsed_planned_dates"]:
        text += texts.DASHBOARD_UNPARSED_DATES_NOTE.format(n=data["unparsed_planned_dates"])

    upcoming = data["upcoming_lessons_without_ksp"]
    if upcoming:
        text += texts.DASHBOARD_UPCOMING_HEADER
        for lesson in upcoming:
            text += texts.DASHBOARD_UPCOMING_ROW.format(**lesson)
    else:
        text += texts.DASHBOARD_UPCOMING_EMPTY

    style = data["style_profile"]
    if style["exists"]:
        text += texts.DASHBOARD_STYLE_PROFILE_YES.format(samples_count=style["samples_count"])
    else:
        text += texts.DASHBOARD_STYLE_PROFILE_NO

    usage = data["usage_today"]
    text += texts.DASHBOARD_USAGE_TODAY.format(
        ksp_used=usage["generate_ksp"], ksp_limit=usage["generate_ksp_limit"],
        ktp_used=usage["generate_ktp"], ktp_limit=usage["generate_ktp_limit"],
    )

    text += _format_uptime_text(data["uptime"])

    return text


def _format_downtime_duration(seconds: int) -> str:
    """10 мин / 1 ч 30 мин — коротко и по-русски, не "0:10:00"."""
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _format_uptime_text(uptime: dict) -> str:
    """М7.4: отдельная функция — используется и в /dashboard с профилем,
    и без него (данные о живучести общие на всю систему, не про учителя)."""
    text = texts.DASHBOARD_UPTIME_HEADER.format(since=uptime["measured_since"])

    last = uptime["last_incident"]
    if last is not None:
        reason_label = texts.DASHBOARD_REASON_LABELS.get(last["reason"], last["reason"])
        if last["ended_at"] is None:
            text += texts.DASHBOARD_UPTIME_LAST_INCIDENT_ONGOING.format(
                reason_label=reason_label, started=str(last["started_at"])[:16].replace("T", " ")
            )
        else:
            text += texts.DASHBOARD_UPTIME_LAST_INCIDENT_RESOLVED.format(
                started=str(last["started_at"])[:16].replace("T", " "),
                ended=str(last["ended_at"])[:16].replace("T", " "),
                reason_label=reason_label,
            )

    if uptime["incidents_7d"]:
        text += texts.DASHBOARD_UPTIME_STATS.format(
            count=uptime["incidents_7d"],
            downtime=_format_downtime_duration(uptime["downtime_seconds_7d"]),
        )
    else:
        text += texts.DASHBOARD_UPTIME_NO_INCIDENTS

    return text


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message) -> None:
    teacher = _get_teacher(message.from_user.id)
    data = collect_dashboard(teacher["id"] if teacher else None)
    await message.answer(format_dashboard_text(data), reply_markup=keyboards.MAIN_MENU)


# =====================================================================
# М2.1 — таблица «текст кнопки меню -> обработчик», для menu_button_pressed
# выше. Три из восьми обработчиков (cmd_templates, cmd_status, cmd_history)
# принимают только message, без state — под них тонкие обёртки с общей
# сигнатурой (message, state), чтобы menu_button_pressed вызывал любой
# обработчик из словаря одинаково, не проверяя его сигнатуру.
# =====================================================================


async def _menu_call_templates(message: Message, state: FSMContext) -> None:
    await cmd_templates(message)


async def _menu_call_status(message: Message, state: FSMContext) -> None:
    await cmd_status(message)


async def _menu_call_history(message: Message, state: FSMContext) -> None:
    await cmd_history(message)


async def _menu_call_dashboard(message: Message, state: FSMContext) -> None:
    await cmd_dashboard(message)


async def _menu_call_class(message: Message, state: FSMContext) -> None:
    await cmd_class(message)


_MENU_BUTTON_HANDLERS = {
    texts.MENU_BUTTON_GENERATE_KSP: cmd_generate,
    texts.MENU_BUTTON_GENERATE_KTP: cmd_generate_ktp,
    texts.MENU_BUTTON_TEACHER: cmd_teacher,
    texts.TEMPLATES_BUTTON: _menu_call_templates,
    texts.MENU_BUTTON_STATUS: _menu_call_status,
    texts.MENU_BUTTON_HISTORY: _menu_call_history,
    texts.MENU_BUTTON_UPLOAD_KSP: cmd_upload_ksp,
    texts.MENU_BUTTON_UPLOAD_KTP: cmd_upload_ktp,
    texts.MENU_BUTTON_DASHBOARD: _menu_call_dashboard,
    texts.MENU_BUTTON_KONSPEKT: cmd_konspekt,
    texts.MENU_BUTTON_CLASS: _menu_call_class,
}


@router.callback_query(F.data.startswith("hist:"))
async def history_resend(callback: CallbackQuery, bot: Bot) -> None:
    generated_id = callback.data.split(":", 1)[1]

    # Владение проверяется здесь, а не только тем, что кнопку прислал сам
    # бот: в групповом чате нажать её может любой участник, а web/api.py
    # ту же проверку делает честно (чужой ksp_id -> 404). Расходиться в
    # правилах доступа между ботом и API нельзя — этап 6 сделает такую
    # щель настоящей дырой.
    teacher = _get_teacher(callback.from_user.id)
    if teacher is None:
        await callback.answer(texts.HISTORY_FILE_MISSING, show_alert=True)
        return

    rows = query(
        "SELECT docx_path FROM generated_ksp WHERE id = ? AND teacher_id = ?",
        (generated_id, teacher["id"]),
    )

    # Отсутствие файла на диске и чужой/несуществующий id намеренно дают
    # один и тот же ответ — не подтверждаем существование чужой записи.
    docx_path = rows[0]["docx_path"] if rows else None
    if not docx_path or not Path(docx_path).exists():
        await callback.answer(texts.HISTORY_FILE_MISSING, show_alert=True)
        return

    await callback.message.answer_document(FSInputFile(docx_path))
    await callback.answer()


# =====================================================================
# М3.2 — таблица «имя состояния из стека -> функция "спросить заново"»,
# для _handle_go_back выше. Строится здесь, когда все функции уже
# определены (тем же способом, что и _MENU_BUTTON_HANDLERS). Ключи — не
# сами объекты State, а их .state (строка вида "Generate:waiting_for_topic") —
# именно так go_back/go_to хранят их в стеке (aiogram FSMContext сериализует
# состояние в строку, не в объект).
#
# UploadKSP/UploadKTP/UploadTemplate — однoшаговые диалоги, они не вызывают
# go_to ни разу, поэтому их состояния никогда не попадают в стек навигации
# и не нуждаются в записи здесь (см. cmd_upload_ksp/cmd_upload_ktp/
# cmd_upload_template — там по-прежнему прямой state.set_state).
# =====================================================================

_BACK_ASK_HANDLERS = {
    TeacherProfile.waiting_for_name.state: _ask_teacher_name,
    TeacherProfile.waiting_for_subject.state: _ask_teacher_subject,
    TeacherProfile.waiting_for_school.state: _ask_teacher_school,
    Generate.waiting_for_topic.state: _ask_generate_topic,
    Generate.waiting_for_objective_code.state: _ask_generate_objective_code,
    Generate.waiting_for_razdel.state: _ask_generate_razdel,
    Generate.waiting_for_klass.state: _ask_generate_klass,
    Generate.waiting_for_duration.state: _ask_generate_duration,
    Generate.waiting_for_textbook_photos.state: _ask_generate_textbook_photos,
    Generate.waiting_for_extra_options.state: _ask_generate_extra_options,
    Generate.waiting_for_template.state: _ask_generate_template,
    Generate.waiting_for_confirmation.state: _render_generate_confirmation,
    GenerateKTP.waiting_for_predmet.state: _ask_generate_ktp_predmet,
    GenerateKTP.waiting_for_klass.state: _ask_generate_ktp_klass,
    GenerateKTP.waiting_for_hours_week.state: _ask_generate_ktp_hours_week,
    GenerateKTP.waiting_for_hours_year.state: _ask_generate_ktp_hours_year,
    GenerateKTP.waiting_for_topics.state: _ask_generate_ktp_topics,
    GenerateKTP.waiting_for_confirmation.state: _ask_generate_ktp_confirmation,
    ClassCreate.waiting_for_name.state: _ask_class_name,
    ClassCreate.waiting_for_subject.state: _ask_class_subject,
    ClassCreate.waiting_for_confirmation.state: _render_class_confirmation,
}


# =====================================================================
# Обработчики задач очереди (core.queue.QueueWorker) — фабрики, им
# нужен Bot для отправки результата; создаются в bot/main.py.
# =====================================================================


def make_parse_ksp_handler(bot: Bot):
    """Разбирает 2-5 загруженных КСП, строит и сохраняет профиль стиля.

    Ошибка именно LLM (провайдеры недоступны и т.п.) — не считается
    провалом всей задачи: генерация КСП по-прежнему будет работать,
    просто без стилизации, поэтому такую ошибку ловим здесь и мягко
    сообщаем, не отдавая её в core.queue.fail()/ретраи. Любая другая
    ошибка (битый файл и т.п.) уходит в очередь как есть — это
    настоящий повод для ретрая."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        teacher_id = payload["teacher_id"]
        file_paths = payload["file_paths"]
        chat_id = task["telegram_chat_id"]

        # parse_ksp — синхронная блокирующая работа: для .doc она зовёт
        # LibreOffice через subprocess.run с таймаутом 60 секунд
        # (core/ksp_parser.ensure_docx). Вызванная прямо здесь, она
        # останавливала весь event loop, а в нём же крутится long polling
        # бота: на пяти .doc-файлах бот замолкал целиком и не отвечал даже
        # на /cancel — до нескольких минут. В отдельный поток.
        parsed_list = [await asyncio.to_thread(parse_ksp, p) for p in file_paths]

        try:
            profile = await build_style_profile(parsed_list)
        except LLMError as exc:
            logger.warning("не удалось построить профиль стиля учителя %s: %s", teacher_id, exc)
            await bot.send_message(chat_id, texts.UPLOAD_KSP_DONE_FAILED.format(error=str(exc)))
            return {"style_profile_built": False}

        save_style_profile(teacher_id, profile)
        await bot.send_message(chat_id, texts.UPLOAD_KSP_DONE_SUCCESS.format(count=len(file_paths)))
        return {"style_profile_built": True, "raw_samples_count": profile["raw_samples_count"]}

    return handler


async def _recognize_textbook_photos(photo_paths: list[str]) -> tuple[str | None, bool]:
    """Р6.1/Р6.2: распознаёт и объединяет текст со всех присланных фото
    учебника. Одно неудачное фото (плохое качество, отказ провайдера) не
    должно ронять всю задачу генерации КСП целиком — тем же принципом,
    что make_parse_ksp_handler мягко переживает недоступность LLM при
    построении профиля стиля: КСП всё равно нужен, просто без этой опоры.

    Возвращает (объединённый текст или None, "фото были, но ни одно не
    распозналось") — второе нужно вызывающему коду, чтобы честно
    предупредить учителя в подписи к файлу, а не промолчать."""
    if not photo_paths:
        return None, False

    recognized_parts = []
    for path_str in photo_paths:
        path = Path(path_str)
        mime = _TEXTBOOK_PHOTO_MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg")
        try:
            image_bytes = path.read_bytes()
            text = await recognize_textbook_page(image_bytes, mime)
            recognized_parts.append(text)
        except (TextbookOCRError, LLMError, OSError) as exc:
            logger.warning("не удалось распознать фото учебника %s: %s", path, exc)
            continue

    if not recognized_parts:
        return None, True  # фото были, но ни одно не распозналось

    return "\n\n".join(recognized_parts), False


async def _try_send_pdf(bot: Bot, chat_id: int, docx_path: Path, caption: str) -> Path | None:
    """Конвертирует готовый конспект из .docx в .pdf и отправляет вторым
    файлом. Конвертация через LibreOffice блокирующая — обязательно
    asyncio.to_thread, иначе заморозим бот на время конвертации (та же
    ловушка, что уже была у core.ksp_parser.ensure_docx).

    Мягкий отказ: .docx конспекта уже отправлен — если LibreOffice
    недоступен или упал, просто не шлём PDF и не роняем всю задачу."""
    try:
        pdf_path = await asyncio.to_thread(convert_docx_to_pdf, docx_path)
    except PdfExportError as exc:
        logger.warning("не удалось собрать PDF для %s: %s", docx_path, exc)
        return None

    await bot.send_document(chat_id, FSInputFile(pdf_path), caption=caption)
    return pdf_path


def make_generate_ksp_handler(bot: Bot):
    """Полный конвейер генерации (core.ksp_generator.generate_and_save_ksp,
    блок Б6) и отправка готового файла. Любая ошибка (LLM недоступен,
    невалидный ответ) уходит наверх как есть — это настоящий провал,
    core.queue сам решит про ретрай/финальное уведомление.

    Распознавание фото учебника (Р6.1/Р6.2) — тоже вызов LLM, поэтому
    здесь, а не синхронно в диалоге бота: собирать фото быстро, а
    распознавать их (может занять минуту на фото) — задача очереди,
    с её же ретраями и гарантией уведомления, а не блокировка диалога."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

        options_dict = payload.get("options")
        options = LessonOptions(**options_dict) if options_dict else None

        textbook_text, ocr_failed = await _recognize_textbook_photos(
            payload.get("textbook_photo_paths") or []
        )

        # М6.2: свой клиент, а не тот, что generate_and_save_ksp создал бы
        # сам — чтобы после генерации прочитать total_tokens_used и
        # записать реальный расход (record_usage), а не выдуманную оценку.
        # Владеем клиентом сами (llm_client передан явно) — значит и
        # закрываем сами, generate_ksp этого не сделает за нас.
        llm_client = LLMClient()
        try:
            result = await generate_and_save_ksp(
                teacher_id=payload["teacher_id"],
                template_id=payload["template_id"],
                topic=payload["topic"],
                razdel=payload["razdel"],
                subject=payload["subject"],
                klass=payload["klass"],
                duration_minutes=payload["duration_minutes"],
                objective_code=payload.get("objective_code"),
                ktp_entry_id=payload.get("ktp_entry_id"),
                options=options,
                textbook_text=textbook_text,
                konspekt_text=payload.get("konspekt_text"),
                llm_client=llm_client,
            )
        finally:
            # count_delta=1 только здесь, ПОСЛЕ успеха — М6.2, ловушка 2:
            # провалившаяся генерация не должна списывать квоту по
            # количеству. Токены, наоборот, пишем в finally безусловно —
            # они потрачены независимо от исхода (тот же принцип, что и
            # в core/limits.py, record_usage). Ключ — telegram_chat_id
            # задачи: для приватного чата с ботом это то же число, что
            # telegram_user_id учителя (см. core/dashboard.py, тот же приём).
            record_usage(chat_id, "generate_ksp", tokens_delta=llm_client.total_tokens_used)
            await llm_client.aclose()
        record_usage(chat_id, "generate_ksp", count_delta=1)

        docx_path = Path(result["docx_path"])
        caption = texts.GENERATE_RESULT_CAPTION.format(topic=payload["topic"])
        if ocr_failed:
            caption += texts.GENERATE_TEXTBOOK_OCR_FAILED_NOTE

        keyboard = None
        if settings.webapp_url:
            # ?preview=, не /preview/{id} как путь: web/static/ раздаётся
            # StaticFiles(html=True) в web/api.py (блок Б9), она отдаёт
            # index.html только на корне — путь /preview/{id} дал бы 404.
            # Query-параметр читает сам index.html (блок Б10) и решает,
            # какой из двух экранов показать, без правок бэкенда.
            preview_url = f"{settings.webapp_url.rstrip('/')}/?preview={result['id']}"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.GENERATE_PREVIEW_BUTTON, web_app=WebAppInfo(url=preview_url))]]
            )

        await bot.send_document(chat_id, FSInputFile(docx_path), caption=caption, reply_markup=keyboard)

        return {
            "generated_ksp_id": result["id"],
            "docx_path": str(docx_path),
            "pdf_path": None,
        }

    return handler


def make_generate_ktp_handler(bot: Bot):
    """Полный конвейер генерации КТП (core.ktp_generator.generate_and_save_ktp,
    блок Р4) и отправка готового файла. Тем же путём, что generate_ksp:
    любая ошибка уходит наверх как есть, core.queue решает про ретрай и
    финальное уведомление."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

        # М6.2 — тот же приём, что и в make_generate_ksp_handler: свой
        # клиент, чтобы прочитать total_tokens_used и записать реальный
        # расход, count_delta=1 только после успеха.
        llm_client = LLMClient()
        try:
            result = await generate_and_save_ktp(
                teacher_id=payload["teacher_id"],
                predmet=payload["predmet"],
                klass=payload["klass"],
                chasov_v_nedelu=payload["chasov_v_nedelu"],
                chasov_v_god=payload["chasov_v_god"],
                topics=payload.get("topics"),
                llm_client=llm_client,
            )
        finally:
            record_usage(chat_id, "generate_ktp", tokens_delta=llm_client.total_tokens_used)
            await llm_client.aclose()
        record_usage(chat_id, "generate_ktp", count_delta=1)

        docx_path = Path(result["docx_path"])
        caption = texts.GENERATE_KTP_RESULT_CAPTION.format(
            predmet=payload["predmet"], klass=payload["klass"]
        )
        if result["ktp_entries_inserted"]:
            caption += texts.GENERATE_KTP_ENTRIES_NOTE.format(count=result["ktp_entries_inserted"])

        await bot.send_document(chat_id, FSInputFile(docx_path), caption=caption)

        return {
            "docx_path": str(docx_path),
            "ktp_entries_inserted": result["ktp_entries_inserted"],
            "pdf_path": None,
        }

    return handler


# =====================================================================
# 'transcribe' — задача очереди (блок К3.2, PLAN_STAGE2.md)
# =====================================================================

# К3.2, ловушка плана дословно: "ретраи очереди для этой задачи опасны —
# повторная расшифровка часового файла съест время и упрётся в тот же
# таймаут. Ограничить число повторов явно." core.queue.QueueWorker не
# умеет разный MAX_RETRIES по типу задачи (один счётчик на все типы) —
# не переделываем архитектуру очереди ради одного типа задачи, вместо
# этого сам обработчик отказывается ПОВТОРНО расшифровывать (task["retries"] > 0
# означает, что первая попытка уже провалилась): реальная транскрипция
# происходит максимум один раз, дальше — быстрый отказ с понятным
# текстом, без траты минут на заведомо тот же результат.
TRANSCRIBE_REAL_ATTEMPT_LIMIT = 0  # retries > этого числа -> не пробовать заново


def _estimate_transcription_minutes(total_duration_seconds: int) -> int:
    """Грубая оценка для сообщения пользователю ДО начала расшифровки —
    по живому замеру (KPI_STAGE1.md): 8-11х быстрее реального времени.
    Берём консервативные 6х (медленнее всех измеренных случаев), чтобы
    не обещать меньше, чем реально получится."""
    return max(1, round(total_duration_seconds / 60 / 6))


def make_transcribe_handler(bot: Bot):
    """Расшифровывает все части записи по порядку (К2.3, ловушка 2:
    склейка транскриптов, не аудиофайлов — каждая часть расшифровывается
    отдельно, тексты соединяются), удаляет аудио после обработки — и при
    успехе, и при провале (К2.4, требование MASTER.md: аудиофайлов на
    диске после обработки — 0), сохраняет готовый транскрипт."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]
        audio_paths: list[str] = payload["audio_paths"]
        # Старые задачи, уже лежащие в очереди на момент миграции, не
        # содержат mode и продолжают прежний ученический сценарий.
        mode = payload.get("mode", "student")

        if task.get("retries", 0) > TRANSCRIBE_REAL_ATTEMPT_LIMIT:
            # Файлы всё равно должны исчезнуть с диска — они уже
            # обречены на неудачу, и K2.4 требует нулевой мусор на диске
            # независимо от исхода.
            for path_str in audio_paths:
                Path(path_str).unlink(missing_ok=True)
            raise TranscriptionError(texts.KONSPEKT_TRANSCRIBE_RETRY_DISABLED)

        total_estimate_seconds = sum(await asyncio.gather(*(_safe_probe(p) for p in audio_paths)))
        await bot.send_message(
            chat_id,
            texts.KONSPEKT_TRANSCRIBING_STARTED.format(
                parts=len(audio_paths),
                total_duration=_format_duration(total_estimate_seconds),
                estimated_minutes=_estimate_transcription_minutes(total_estimate_seconds),
            ),
        )

        text_parts: list[str] = []
        total_duration = 0
        try:
            for path_str in audio_paths:
                result = await transcribe(path_str)
                text_parts.append(result["text"])
                total_duration += result["duration_seconds"]
        finally:
            # К2.4: удаление ИСХОДНИКОВ в finally — и при успехе, и при
            # провале расшифровки. Промежуточный WAV убирается сам внутри
            # core.transcriber (TemporaryDirectory), это только исходные
            # присланные файлы.
            for path_str in audio_paths:
                try:
                    Path(path_str).unlink(missing_ok=True)
                except OSError:
                    logger.warning("не удалось удалить аудиофайл %s после транскрипции", path_str)

        full_text = "\n\n".join(text_parts)
        transcript_id = str(uuid.uuid4())
        execute(
            "INSERT INTO transcripts (id, teacher_id, source, mode, text, duration_seconds, language) "
            "VALUES (?, ?, 'audio', ?, ?, ?, ?)",
            (transcript_id, payload["teacher_id"], mode, full_text, total_duration, "ru"),
        )

        if mode == "student":
            preview = full_text[:300] + ("…" if len(full_text) > 300 else "")
            await bot.send_message(
                chat_id,
                texts.KONSPEKT_TRANSCRIPT_READY.format(duration=_format_duration(total_duration), preview=preview),
            )
            enqueue(
                "generate_konspekt",
                {"teacher_id": payload["teacher_id"], "transcript_id": transcript_id, "mode": mode},
                chat_id=chat_id,
            )
        else:
            # Учительская ветка намеренно не ставит generate_konspekt:
            # здесь нет ни одного обращения к LLM. Строка konspekty нужна
            # существующей кнопке перехода к КСП в обоих режимах.
            konspekt_id = str(uuid.uuid4())
            content = {
                "tema": "Расшифровка урока",
                "transcript_text": full_text,
            }
            execute(
                "INSERT INTO konspekty (id, teacher_id, transcript_id, mode, tema, content_json) "
                "VALUES (?, ?, ?, 'teacher', ?, ?)",
                (
                    konspekt_id,
                    payload["teacher_id"],
                    transcript_id,
                    content["tema"],
                    json.dumps(content, ensure_ascii=False),
                ),
            )
            chunks = _split_for_telegram(
                texts.KONSPEKT_TEACHER_TRANSCRIPT_READY.format(
                    duration=_format_duration(total_duration), transcript=full_text
                )
            )
            ksp_button = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.KSP_FROM_KONSPEKT_BUTTON,
                            callback_data=f"ksp_from_konspekt:{konspekt_id}",
                        )
                    ]
                ]
            )
            for index, chunk in enumerate(chunks):
                await bot.send_message(
                    chat_id,
                    chunk,
                    reply_markup=ksp_button if index == len(chunks) - 1 else None,
                )

        return {"transcript_id": transcript_id, "duration_seconds": total_duration, "mode": mode}

    return handler


# =====================================================================
# 'generate_konspekt' — задача очереди (блок К4.3, PLAN_STAGE2.md)
# =====================================================================


def make_konspekt_handler(bot: Bot):
    """Собирает конспект по уже готовому транскрипту (core.konspekt_generator,
    блок К4). Топик/код цели как вспомогательный контекст в
    generate_konspekt пока не передаются: /konspekt (К2.3) их не
    спрашивает — только аудио. Расширить это позже можно без изменения
    самого генератора, у него оба параметра уже необязательные.

    К6/П1: сначала .docx (core.konspekt_builder), следом — .pdf через
    _try_send_pdf (мягкий отказ, если LibreOffice недоступен, второй
    конвертации нет). КСП и КТП в PDF больше не отправляются. Текстом в чат
    конспект тоже приходит (К4) — .docx нужен для печати/архива, текст —
    для быстрого чтения прямо в Telegram, одно другому не мешает.

    К5: последнее ТЕКСТОВОЕ сообщение несёт кнопку «Собрать КСП по этому
    конспекту» — ведёт в ksp_from_konspekt_pressed."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

        rows = query("SELECT text, mode FROM transcripts WHERE id = ?", (payload["transcript_id"],))
        if not rows:
            raise KonspektGenerationError(
                f"транскрипт {payload['transcript_id']} не найден — не может собрать по нему конспект"
            )
        transcript_text = rows[0]["text"]

        llm_client = LLMClient()
        try:
            content = await generate_konspekt(transcript_text, llm_client=llm_client)
        finally:
            record_usage(chat_id, "generate_konspekt", tokens_delta=llm_client.total_tokens_used)
            await llm_client.aclose()
        record_usage(chat_id, "generate_konspekt", count_delta=1)

        konspekt_id = str(uuid.uuid4())

        # К6: .docx собирается синхронно, python-docx здесь ничего не
        # блокирует надолго (в отличие от внешней конвертации LibreOffice) —
        # тот же приём, что уже используют core.ksp_generator.save_generated_ksp
        # и core.ktp_generator (build_docx без asyncio.to_thread).
        student_content = content.get("konspekt_uchenika", content)
        filename = build_konspekt_filename(student_content.get("tema", ""), datetime.now().date())
        docx_path = build_konspekt_docx(content, settings.generated_dir / filename)

        execute(
            "INSERT INTO konspekty (id, teacher_id, transcript_id, mode, tema, content_json, docx_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                konspekt_id,
                payload["teacher_id"],
                payload["transcript_id"],
                rows[0]["mode"],
                student_content["tema"],
                json.dumps(content, ensure_ascii=False),
                str(docx_path),
            ),
        )

        await bot.send_document(
            chat_id, FSInputFile(docx_path), caption=texts.KONSPEKT_DOCX_CAPTION.format(tema=student_content["tema"])
        )
        await _try_send_pdf(bot, chat_id, docx_path, texts.KONSPEKT_PDF_CAPTION)

        # К5: кнопка на ПОСЛЕДНЕМ текстовом сообщении (не на каждом — при
        # разбивке на несколько частей одна кнопка под всем конспектом
        # достаточна). У5: «Отправить ученику» — на той же карточке, второй
        # кнопкой отдельным рядом (не тем же рядом с КСП — разные по весу
        # действия, объединять в один ряд визуально их равняло бы).
        chunks = _split_for_telegram(format_konspekt_text(content))
        ksp_button = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=texts.KSP_FROM_KONSPEKT_BUTTON, callback_data=f"ksp_from_konspekt:{konspekt_id}")],
                [InlineKeyboardButton(text=texts.SEND_KONSPEKT_BUTTON, callback_data=f"sk:{konspekt_id}")],
            ]
        )
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            await bot.send_message(chat_id, chunk, reply_markup=ksp_button if is_last else None)

        return {"konspekt_id": konspekt_id, "docx_path": str(docx_path)}

    return handler


_TELEGRAM_MESSAGE_LIMIT = 4000  # с запасом от настоящего лимита Telegram в 4096


def _split_for_telegram(text: str) -> list[str]:
    """Конспект целиком может не влезть в одно сообщение Telegram (лимит
    4096 символов) — режем по границам строк, не по символам, чтобы не
    разрывать слово или дескриптор посередине."""
    if len(text) <= _TELEGRAM_MESSAGE_LIMIT:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # STT может вернуть весь часовой урок одним абзацем. Тогда
        # одной границы строк недостаточно — длинную строку режем жёстко,
        # иначе Telegram отклонит сообщение целиком.
        pieces = [
            line[index : index + _TELEGRAM_MESSAGE_LIMIT]
            for index in range(0, len(line), _TELEGRAM_MESSAGE_LIMIT)
        ] or [""]
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) > _TELEGRAM_MESSAGE_LIMIT and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def format_konspekt_text(content: dict) -> str:
    """Текстовое представление конспекта для бота — .docx появится в
    блоке К6, здесь пока только текст сообщения."""
    student = content.get("konspekt_uchenika", content)
    lines = ["📝 Конспект урока", "", texts.KONSPEKT_REPLICAS_NOTICE, "", "Опорные реплики учителя:"]
    replicas = content.get("opornye_repliki") or []
    if replicas:
        lines.extend(f"• {replica}" for replica in replicas)
    else:
        lines.append("• опорных реплик в записи не нашлось")
    lines.extend(["", "Конспект для ученика:", f"Тема: {student['tema']}", ""])

    lines.append("Цели:")
    if student.get("celi"):
        lines.extend(f"• {c}" for c in student["celi"])
    else:
        # Аудит этапа 2, находка 1: пустые цели — законный результат, а не
        # недоделка. Раздел показываем всегда, чтобы читатель не гадал,
        # целей не было или модель их потеряла.
        lines.append(f"• {CELI_NOT_STATED_NOTE}")
    lines.append("")

    if student.get("glavnoe"):
        lines.append("Главное:")
        lines.extend(f"• {g}" for g in student["glavnoe"])
        lines.append("")

    if student.get("formuly"):
        lines.append("Формулы:")
        for f in student["formuly"]:
            lines.append(f"• {f['formula']} — {f['znachenie']}")
        lines.append("")

    if student.get("terminy"):
        lines.append("Термины:")
        for t in student["terminy"]:
            lines.append(f"• {t['termin']}: {t['opredelenie']}")
        lines.append("")

    if student.get("primery"):
        lines.append("Примеры:")
        lines.extend(f"• {p}" for p in student["primery"])
        lines.append("")

    if student.get("voprosy_dlya_samoproverki"):
        lines.append("Вопросы для самопроверки:")
        lines.extend(f"• {q}" for q in student["voprosy_dlya_samoproverki"])
        lines.append("")

    if student.get("domashnee_zadanie"):
        lines.append(f"Домашнее задание: {student['domashnee_zadanie']}")

    return "\n".join(lines).strip()


async def _safe_probe(path_str: str) -> int:
    """Длительность части ДО расшифровки, для оценочного сообщения
    (К3.2). Ошибка здесь не должна сорвать саму транскрипцию — оценка
    времени необязательна, сама расшифровка обязательна; при сбое проба
    просто не учитывается в оценке (0), а не роняет всю задачу."""
    try:
        return await probe_duration_seconds(path_str)
    except Exception:
        return 0
