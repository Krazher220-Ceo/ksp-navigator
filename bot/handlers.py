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
import uuid
from dataclasses import asdict
from datetime import datetime
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
    WebAppInfo,
)

from bot import keyboards, texts
from bot.navigation import go_back, go_to
from bot.states import Generate, GenerateKTP, Konspekt, TeacherProfile, UploadKSP, UploadKTP, UploadTemplate
from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import execute, query
from core.limits import DAILY_COUNT_LIMITS, LimitExceeded, check_count_limit, check_token_limit, get_usage_today, record_usage
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
from core.values import find_value_key_by_name, get_value

logger = logging.getLogger(__name__)

router = Router()

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MIN_KSP_FILES = 2
MAX_KSP_FILES = 5
SUPPORTED_KSP_EXTENSIONS = {".doc", ".docx"}
SUPPORTED_KTP_EXTENSIONS = {".docx", ".xlsx"}


# =====================================================================
# Общие хелперы
# =====================================================================


def _get_teacher(telegram_user_id: int, db_path=None) -> dict | None:
    rows = query("SELECT * FROM teachers WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path)
    return dict(rows[0]) if rows else None


def _has_style_profile(teacher_id: int, db_path=None) -> bool:
    rows = query("SELECT id FROM style_profiles WHERE teacher_id = ?", (teacher_id,), db_path=db_path)
    return bool(rows)


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
        if operation in DAILY_COUNT_LIMITS and exc.limit == DAILY_COUNT_LIMITS[operation]:
            text = texts.LIMIT_COUNT_EXCEEDED.format(
                operation_label=_LIMIT_OPERATION_LABELS.get(operation, operation),
                used=exc.used, limit=exc.limit, reset_time=reset_time,
            )
        else:
            text = texts.LIMIT_TOKENS_EXCEEDED.format(used=exc.used, limit=exc.limit, reset_time=reset_time)
        await message.answer(text, reply_markup=keyboards.MAIN_MENU)
        return False
    return True


# =====================================================================
# /start
# =====================================================================


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.START, reply_markup=keyboards.MAIN_MENU)


# =====================================================================
# /cancel — общий сброс любого диалога
# =====================================================================


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer(texts.CANCEL_NOTHING_TO_CANCEL)
        return
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
# UploadKSP.collecting_files — единственное исключение из общего правила
# "Назад = предыдущий шаг": там всего один шаг и файлы копятся, поэтому
# "Назад" означает "убрать последний загруженный файл" (М3.3, ловушка).
# Это одна ветка в одном обработчике, а не второй обработчик и не 16 копий.
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
    current = await state.get_state()
    if current == UploadKSP.collecting_files.state:
        await _upload_ksp_remove_last_file(message, state)
        return
    await _handle_go_back(message, state)


@router.callback_query(F.data == "nav_back")
async def back_callback_pressed(callback: CallbackQuery, state: FSMContext) -> None:
    await _handle_go_back(callback.message, state)
    await callback.answer()


@router.message(F.text == texts.BUTTON_CANCEL)
async def cancel_button_pressed(message: Message, state: FSMContext) -> None:
    await cmd_cancel(message, state)


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
    await message.answer(texts.UPLOAD_KSP_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


async def _upload_ksp_remove_last_file(message: Message, state: FSMContext) -> None:
    """М3.3, ловушка: в этом диалоге один шаг, файлы копятся — «Назад»
    здесь означает «убрать последний загруженный файл», а не переключение
    состояния (переключать некуда, шаг один)."""
    data = await state.get_data()
    file_paths = list(data.get("file_paths", []))
    if not file_paths:
        await message.answer(texts.UPLOAD_KSP_NOTHING_TO_REMOVE, reply_markup=keyboards.back_cancel_keyboard())
        return

    removed_path = file_paths.pop()
    await state.update_data(file_paths=file_paths)
    try:
        Path(removed_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("не удалось удалить файл %s при отмене загрузки КСП", removed_path)

    await message.answer(
        texts.UPLOAD_KSP_LAST_FILE_REMOVED.format(filename=Path(removed_path).name, count=len(file_paths)),
        reply_markup=keyboards.back_cancel_keyboard(),
    )


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
        reply_markup=keyboards.back_cancel_keyboard(),
    )


@router.message(UploadKSP.collecting_files, Command("done"))
async def upload_ksp_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    file_paths = data.get("file_paths", [])

    if len(file_paths) < MIN_KSP_FILES:
        await message.answer(
            texts.UPLOAD_KSP_TOO_FEW.format(count=len(file_paths)), reply_markup=keyboards.back_cancel_keyboard()
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
    await message.answer(texts.UPLOAD_KSP_PROMPT, reply_markup=keyboards.back_cancel_keyboard())


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
        template_id = int(payload["template_id"])
    except (ValueError, TypeError, KeyError):
        await message.answer(texts.TEMPLATES_CHOSEN_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

    # Шаблон должен быть доступен именно этому учителю: id приходит с
    # клиента, а значит доверять ему как своему нельзя.
    available = {t["id"]: t for t in list_templates(teacher["id"])}
    template = available.get(template_id)
    if template is None:
        await message.answer(texts.TEMPLATES_CHOSEN_UNKNOWN, reply_markup=keyboards.MAIN_MENU)
        return

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

MAX_KONSPEKT_PARTS = 10

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
    await go_to(state, Konspekt.collecting_audio)
    await state.update_data(teacher_id=teacher["id"], audio_paths=[], audio_durations=[])
    await message.answer(
        texts.KONSPEKT_PROMPT.format(max_parts=MAX_KONSPEKT_PARTS),
        reply_markup=keyboards.back_cancel_keyboard(),
    )


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
        await message.answer(size_error)
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
        reply_markup=keyboards.back_cancel_keyboard(),
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
        await message.answer(texts.KONSPEKT_UNSUPPORTED_INPUT, reply_markup=keyboards.back_cancel_keyboard())
        return
    await _konspekt_store_part(message, state, bot, document, document.file_name, mime_type, None)


@router.message(Konspekt.collecting_audio, Command("done"))
async def konspekt_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    paths = data.get("audio_paths", [])
    if not paths:
        await message.answer(texts.KONSPEKT_NO_PARTS_YET, reply_markup=keyboards.back_cancel_keyboard())
        return

    enqueue(
        "transcribe",
        {"teacher_id": data["teacher_id"], "audio_paths": paths},
        chat_id=message.chat.id,
    )
    await state.clear()
    await message.answer(texts.KONSPEKT_QUEUED.format(count=len(paths)), reply_markup=keyboards.MAIN_MENU)


@router.message(Konspekt.collecting_audio)
async def konspekt_wrong_input(message: Message) -> None:
    await message.answer(texts.KONSPEKT_UNSUPPORTED_INPUT, reply_markup=keyboards.back_cancel_keyboard())


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
        field = _EXTRA_OPTION_KEY_ALIASES.get(key_part.strip().lower())
        value = value_part.strip()
        if field is None or not value:
            unrecognized.append(line)
            continue

        if field == "cennost":
            key = find_value_key_by_name(value)
            if key is None:
                unrecognized.append(line)
                continue
            options.cennost_key = key
        elif field == "vidy_deyatelnosti":
            options.vidy_deyatelnosti = [v.strip() for v in value.split(",") if v.strip()][:MAX_VIDY_DEYATELNOSTI]
        elif field == "ima_oop":
            options.ima_oop = value.lower() in _AFFIRMATIVE_VALUES
        elif field == "sor":
            options.sor_instead_of_reflection = value.lower() in _AFFIRMATIVE_VALUES
        elif field == "fizkultminutka":
            options.fizkultminutka = value.lower() in _AFFIRMATIVE_VALUES
        elif field == "predvaritelnye_znaniya":
            options.predvaritelnye_znaniya = value
        elif field == "tip_uroka":
            options.tip_uroka = value
        elif field == "mezhpredmetnye_svyazi":
            options.mezhpredmetnye_svyazi = [v.strip() for v in value.split(",") if v.strip()]
        elif field == "page_orientation":
            options.page_orientation = "album" if "альбом" in value.lower() else "book"

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
    await message.answer(texts.GENERATE_ASK_EXTRA_OPTIONS, reply_markup=keyboards.back_cancel_keyboard())


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
    await state.clear()
    await go_to(state, Generate.waiting_for_topic)
    await state.update_data(teacher_id=teacher["id"], subject=teacher["subject"])
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
    rows = query("SELECT teacher_id, content_json FROM konspekty WHERE id = ?", (konspekt_id,))
    if not rows or rows[0]["teacher_id"] != teacher["id"]:
        await callback.answer(texts.KSP_FROM_KONSPEKT_NOT_FOUND, show_alert=True)
        return

    content = json.loads(rows[0]["content_json"])

    await state.clear()
    await go_to(state, Generate.waiting_for_topic)
    await state.update_data(
        teacher_id=teacher["id"],
        subject=teacher["subject"],
        konspekt_text=format_konspekt_text(content),
    )

    await _proceed_with_topic(callback.message, state, content["tema"])
    await callback.answer()


async def _proceed_with_topic(message: Message, state: FSMContext, topic: str) -> None:
    """Общая часть после того, как тема стала известна — что при обычном
    ручном вводе в /generate (generate_topic_received), что при
    предзаполнении темой из уже готового конспекта (К5,
    ksp_from_konspekt_pressed): код цели угадывается тем же способом в
    обоих случаях, не двумя разными."""
    data = await state.get_data()
    code = guess_objective_code(data["teacher_id"], topic)
    await state.update_data(topic=topic, objective_code=code)

    if code:
        await message.answer(texts.GENERATE_OBJECTIVE_AUTO_FOUND.format(code=code))
        await go_to(state, Generate.waiting_for_razdel)
        await _ask_generate_razdel(message, state)
    else:
        await go_to(state, Generate.waiting_for_objective_code)
        await _ask_generate_objective_code(message, state)


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
    klass = (message.text or "").strip()
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
    summary = texts.GENERATE_CONFIRM_SUMMARY.format(
        topic=data["topic"],
        razdel=data["razdel"],
        objective_code=data.get("objective_code") or "не указан",
        klass=data["klass"],
        duration=data["duration_minutes"],
        template_name=template["name"],
        textbook_photos_line=texts.GENERATE_TEXTBOOK_PHOTOS_LINE.format(count=len(photo_paths)) if photo_paths else "",
        extra_options_line=_format_extra_options_summary(data.get("options")),
    )
    if not _has_style_profile(data["teacher_id"]):
        summary += texts.GENERATE_NO_STYLE_PROFILE_NOTE

    rows = [
        [
            InlineKeyboardButton(text=texts.GENERATE_CONFIRM_BUTTON, callback_data="gen_confirm"),
            InlineKeyboardButton(text=texts.GENERATE_CANCEL_BUTTON, callback_data="gen_cancel"),
        ]
    ]
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
    """Конвертирует готовый .docx в .pdf и отправляет вторым файлом
    (блок Р10 — приказ №130 принимает оба формата). Конвертация через
    LibreOffice блокирующая — обязательно asyncio.to_thread, иначе
    заморозим бот на время конвертации (та же ловушка, что уже была у
    core.ksp_parser.ensure_docx).

    Мягкий отказ: .docx уже отправлен и сам по себе достаточен для
    сдачи — если LibreOffice недоступен или упал, просто не шлём PDF,
    не роняя всю генерацию."""
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

        pdf_path = await _try_send_pdf(bot, chat_id, docx_path, texts.GENERATE_PDF_CAPTION)

        return {
            "generated_ksp_id": result["id"],
            "docx_path": str(docx_path),
            "pdf_path": str(pdf_path) if pdf_path else None,
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

        pdf_path = await _try_send_pdf(bot, chat_id, docx_path, texts.GENERATE_KTP_PDF_CAPTION)

        return {
            "docx_path": str(docx_path),
            "ktp_entries_inserted": result["ktp_entries_inserted"],
            "pdf_path": str(pdf_path) if pdf_path else None,
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
            "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
            "VALUES (?, ?, 'audio', ?, ?, ?)",
            (transcript_id, payload["teacher_id"], full_text, total_duration, "ru"),
        )

        preview = full_text[:300] + ("…" if len(full_text) > 300 else "")
        await bot.send_message(
            chat_id,
            texts.KONSPEKT_TRANSCRIPT_READY.format(duration=_format_duration(total_duration), preview=preview),
        )

        # К4: цепочка одна (MASTER.md 0.6, п.2) — расшифровка сама
        # запускает сборку конспекта следующей задачей очереди, учителю
        # не нужно ничего вызывать отдельно.
        enqueue(
            "generate_konspekt",
            {"teacher_id": payload["teacher_id"], "transcript_id": transcript_id},
            chat_id=chat_id,
        )

        return {"transcript_id": transcript_id, "duration_seconds": total_duration}

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

    К6: сначала .docx (core.konspekt_builder), следом — .pdf тем же
    _try_send_pdf, что и у КСП/КТП (мягкий отказ, если LibreOffice
    недоступен, второй конвертации нет). Текстом в чат конспект тоже
    приходит (К4) — .docx нужен для печати/архива, текст — для быстрого
    чтения прямо в Telegram, одно другому не мешает.

    К5: последнее ТЕКСТОВОЕ сообщение несёт кнопку «Собрать КСП по этому
    конспекту» — ведёт в ksp_from_konspekt_pressed."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

        rows = query("SELECT text FROM transcripts WHERE id = ?", (payload["transcript_id"],))
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
        # блокирует надолго (в отличие от whisper/ffmpeg/LibreOffice) —
        # тот же приём, что уже используют core.ksp_generator.save_generated_ksp
        # и core.ktp_generator (build_docx без asyncio.to_thread).
        filename = build_konspekt_filename(content.get("tema", ""), datetime.now().date())
        docx_path = build_konspekt_docx(content, settings.generated_dir / filename)

        execute(
            "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json, docx_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                konspekt_id,
                payload["teacher_id"],
                payload["transcript_id"],
                content["tema"],
                json.dumps(content, ensure_ascii=False),
                str(docx_path),
            ),
        )

        await bot.send_document(
            chat_id, FSInputFile(docx_path), caption=texts.KONSPEKT_DOCX_CAPTION.format(tema=content["tema"])
        )
        await _try_send_pdf(bot, chat_id, docx_path, texts.KONSPEKT_PDF_CAPTION)

        # К5: кнопка на ПОСЛЕДНЕМ текстовом сообщении (не на каждом — при
        # разбивке на несколько частей одна кнопка под всем конспектом
        # достаточна).
        chunks = _split_for_telegram(format_konspekt_text(content))
        ksp_button = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=texts.KSP_FROM_KONSPEKT_BUTTON, callback_data=f"ksp_from_konspekt:{konspekt_id}")]
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
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _TELEGRAM_MESSAGE_LIMIT and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def format_konspekt_text(content: dict) -> str:
    """Текстовое представление конспекта для бота — .docx появится в
    блоке К6, здесь пока только текст сообщения."""
    lines = [f"📝 Конспект: {content['tema']}", ""]

    lines.append("Цели:")
    if content.get("celi"):
        lines.extend(f"• {c}" for c in content["celi"])
    else:
        # Аудит этапа 2, находка 1: пустые цели — законный результат, а не
        # недоделка. Раздел показываем всегда, чтобы читатель не гадал,
        # целей не было или модель их потеряла.
        lines.append(f"• {CELI_NOT_STATED_NOTE}")
    lines.append("")

    if content.get("glavnoe"):
        lines.append("Главное:")
        lines.extend(f"• {g}" for g in content["glavnoe"])
        lines.append("")

    if content.get("formuly"):
        lines.append("Формулы:")
        for f in content["formuly"]:
            lines.append(f"• {f['formula']} — {f['znachenie']}")
        lines.append("")

    if content.get("terminy"):
        lines.append("Термины:")
        for t in content["terminy"]:
            lines.append(f"• {t['termin']}: {t['opredelenie']}")
        lines.append("")

    if content.get("primery"):
        lines.append("Примеры:")
        lines.extend(f"• {p}" for p in content["primery"])
        lines.append("")

    if content.get("voprosy_dlya_samoproverki"):
        lines.append("Вопросы для самопроверки:")
        lines.extend(f"• {q}" for q in content["voprosy_dlya_samoproverki"])
        lines.append("")

    if content.get("domashnee_zadanie"):
        lines.append(f"Домашнее задание: {content['domashnee_zadanie']}")

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
