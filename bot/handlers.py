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

from bot import texts
from bot.states import Generate, GenerateKTP, TeacherProfile, UploadKSP, UploadKTP, UploadTemplate
from core.config import settings
from core.db import execute, query
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
from core.llm_client import LLMError
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


# =====================================================================
# /start
# =====================================================================


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.START)


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
# /teacher
# =====================================================================


@router.message(Command("teacher"))
async def cmd_teacher(message: Message, state: FSMContext) -> None:
    await state.set_state(TeacherProfile.waiting_for_name)
    await message.answer(texts.TEACHER_ASK_NAME)


@router.message(TeacherProfile.waiting_for_name)
async def teacher_name_received(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer(texts.TEACHER_ASK_NAME)
        return
    await state.update_data(name=name)
    await state.set_state(TeacherProfile.waiting_for_subject)
    await message.answer(texts.TEACHER_ASK_SUBJECT)


@router.message(TeacherProfile.waiting_for_subject)
async def teacher_subject_received(message: Message, state: FSMContext) -> None:
    subject = (message.text or "").strip()
    if not subject:
        await message.answer(texts.TEACHER_ASK_SUBJECT)
        return
    await state.update_data(subject=subject)
    await state.set_state(TeacherProfile.waiting_for_school)
    await message.answer(texts.TEACHER_ASK_SCHOOL)


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
    await message.answer(response.format(name=name, subject=subject))


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
    await message.answer(texts.UPLOAD_KSP_PROMPT)


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
        texts.UPLOAD_KSP_FILE_ACCEPTED.format(n=len(file_paths), max=MAX_KSP_FILES, filename=filename)
    )


@router.message(UploadKSP.collecting_files, Command("done"))
async def upload_ksp_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    file_paths = data.get("file_paths", [])

    if len(file_paths) < MIN_KSP_FILES:
        await message.answer(texts.UPLOAD_KSP_TOO_FEW.format(count=len(file_paths)))
        return

    enqueue(
        "parse_ksp",
        {"teacher_id": data["teacher_id"], "file_paths": file_paths},
        chat_id=message.chat.id,
    )
    await state.clear()
    await message.answer(texts.UPLOAD_KSP_QUEUED.format(count=len(file_paths)))


@router.message(UploadKSP.collecting_files)
async def upload_ksp_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_KSP_PROMPT)


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
    await message.answer(texts.UPLOAD_KTP_PROMPT)


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
        await message.answer(texts.UPLOAD_KTP_PARSE_ERROR.format(error=str(exc)))
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
        )
    )
    await state.clear()


@router.message(UploadKTP.waiting_for_file)
async def upload_ktp_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_KTP_PROMPT)


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
        await message.answer(texts.TEMPLATES_CHOSEN_UNKNOWN, reply_markup=ReplyKeyboardRemove())
        return

    # Шаблон должен быть доступен именно этому учителю: id приходит с
    # клиента, а значит доверять ему как своему нельзя.
    available = {t["id"]: t for t in list_templates(teacher["id"])}
    template = available.get(template_id)
    if template is None:
        await message.answer(texts.TEMPLATES_CHOSEN_UNKNOWN, reply_markup=ReplyKeyboardRemove())
        return

    await state.clear()
    await state.set_state(Generate.waiting_for_topic)
    await state.update_data(
        teacher_id=teacher["id"], subject=teacher["subject"], template_id=template_id
    )
    await message.answer(
        texts.TEMPLATES_CHOSEN.format(template_name=template["name"]),
        reply_markup=ReplyKeyboardRemove(),
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
    await message.answer(texts.UPLOAD_TEMPLATE_PROMPT)


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
        await message.answer(texts.UPLOAD_TEMPLATE_PARSE_ERROR.format(error=str(exc)))
        await state.clear()
        return

    await message.answer(texts.UPLOAD_TEMPLATE_SUCCESS.format(template_name=template["name"]))
    await state.clear()


@router.message(UploadTemplate.waiting_for_file)
async def upload_template_wrong_input(message: Message) -> None:
    await message.answer(texts.UPLOAD_TEMPLATE_PROMPT)


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


@router.message(Command("generate"))
async def cmd_generate(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.clear()
    await state.set_state(Generate.waiting_for_topic)
    await state.update_data(teacher_id=teacher["id"], subject=teacher["subject"])
    await message.answer(texts.GENERATE_ASK_TOPIC)


@router.message(Generate.waiting_for_topic)
async def generate_topic_received(message: Message, state: FSMContext) -> None:
    topic = (message.text or "").strip()
    if not topic:
        await message.answer(texts.GENERATE_ASK_TOPIC)
        return

    data = await state.get_data()
    code = guess_objective_code(data["teacher_id"], topic)
    await state.update_data(topic=topic, objective_code=code)

    if code:
        await message.answer(texts.GENERATE_OBJECTIVE_AUTO_FOUND.format(code=code))
        await state.set_state(Generate.waiting_for_razdel)
        await message.answer(texts.GENERATE_ASK_RAZDEL)
    else:
        await state.set_state(Generate.waiting_for_objective_code)
        await message.answer(texts.GENERATE_ASK_OBJECTIVE_CODE)


@router.message(Generate.waiting_for_objective_code)
async def generate_objective_code_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    code = None if text in ("-", "") else text
    await state.update_data(objective_code=code)
    await state.set_state(Generate.waiting_for_razdel)
    await message.answer(texts.GENERATE_ASK_RAZDEL)


@router.message(Generate.waiting_for_razdel)
async def generate_razdel_received(message: Message, state: FSMContext) -> None:
    razdel = (message.text or "").strip()
    if not razdel:
        await message.answer(texts.GENERATE_ASK_RAZDEL)
        return
    await state.update_data(razdel=razdel)
    await state.set_state(Generate.waiting_for_klass)
    await message.answer(texts.GENERATE_ASK_KLASS)


@router.message(Generate.waiting_for_klass)
async def generate_klass_received(message: Message, state: FSMContext) -> None:
    klass = (message.text or "").strip()
    if not klass:
        await message.answer(texts.GENERATE_ASK_KLASS)
        return
    await state.update_data(klass=klass)
    await state.set_state(Generate.waiting_for_duration)
    await message.answer(texts.GENERATE_ASK_DURATION)


@router.message(Generate.waiting_for_duration)
async def generate_duration_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_DURATION_NOT_A_NUMBER)
        return

    await state.update_data(duration_minutes=int(text))
    await state.set_state(Generate.waiting_for_textbook_photos)
    await state.update_data(textbook_photo_paths=[])
    await message.answer(texts.GENERATE_ASK_TEXTBOOK_PHOTOS)


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
    await state.set_state(Generate.waiting_for_extra_options)
    await message.answer(texts.GENERATE_ASK_EXTRA_OPTIONS)


@router.message(Generate.waiting_for_textbook_photos, Command("done"))
async def generate_textbook_photos_done(message: Message, state: FSMContext) -> None:
    await state.set_state(Generate.waiting_for_extra_options)
    await message.answer(texts.GENERATE_ASK_EXTRA_OPTIONS)


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
    await message.answer(texts.GENERATE_TEXTBOOK_PHOTO_ACCEPTED.format(n=len(paths)))


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
    await message.answer(texts.GENERATE_TEXTBOOK_PHOTO_ACCEPTED.format(n=len(paths)))


@router.message(Generate.waiting_for_textbook_photos)
async def generate_textbook_photos_wrong_input(message: Message) -> None:
    await message.answer(texts.GENERATE_TEXTBOOK_PHOTO_UNSUPPORTED_FORMAT)


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
    # тогда спрашивать его второй раз незачем, сразу к подтверждению.
    if data.get("template_id"):
        await _ask_generate_confirmation(message, state, data["template_id"])
        return

    templates_list = list_templates(data["teacher_id"])
    if not templates_list:
        await message.answer(texts.GENERATE_NO_TEMPLATES)
        await state.clear()
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t["name"], callback_data=f"gen_tpl:{t['id']}")]
            for t in templates_list
        ]
    )
    await state.set_state(Generate.waiting_for_template)
    await message.answer(texts.GENERATE_ASK_TEMPLATE, reply_markup=keyboard)


async def _ask_generate_confirmation(message: Message, state: FSMContext, template_id: int) -> bool:
    """Показывает сводку перед генерацией и кнопки да/нет. Общий шаг для
    двух путей выбора шаблона: инлайн-кнопкой в боте и заранее — в Mini
    App. Возвращает False, если шаблон за это время исчез."""
    template = get_template(template_id)
    if template is None:
        return False

    await state.update_data(template_id=template_id)
    data = await state.get_data()

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

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.GENERATE_CONFIRM_BUTTON, callback_data="gen_confirm"),
                InlineKeyboardButton(text=texts.GENERATE_CANCEL_BUTTON, callback_data="gen_cancel"),
            ]
        ]
    )
    await state.set_state(Generate.waiting_for_confirmation)
    await message.answer(summary, reply_markup=keyboard)
    return True


@router.callback_query(Generate.waiting_for_template, F.data.startswith("gen_tpl:"))
async def generate_template_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    template_id = int(callback.data.split(":", 1)[1])

    shown = await _ask_generate_confirmation(callback.message, state, template_id)
    if not shown:
        await callback.answer("Такого шаблона больше нет, попробуйте /generate заново", show_alert=True)
        return

    await callback.answer()


@router.callback_query(Generate.waiting_for_confirmation, F.data == "gen_cancel")
async def generate_cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.GENERATE_CANCELLED)
    await callback.answer()


@router.callback_query(Generate.waiting_for_confirmation, F.data == "gen_confirm")
async def generate_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
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
    }
    enqueue("generate_ksp", payload, chat_id=callback.message.chat.id)
    await state.clear()
    await callback.message.answer(texts.GENERATE_QUEUED)
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


@router.message(Command("generate_ktp"))
async def cmd_generate_ktp(message: Message, state: FSMContext) -> None:
    teacher = await _require_teacher(message)
    if teacher is None:
        return
    await state.clear()
    await state.set_state(GenerateKTP.waiting_for_predmet)
    await state.update_data(teacher_id=teacher["id"])
    await message.answer(texts.GENERATE_KTP_ASK_PREDMET)


@router.message(GenerateKTP.waiting_for_predmet)
async def generate_ktp_predmet_received(message: Message, state: FSMContext) -> None:
    predmet = (message.text or "").strip()
    if not predmet:
        await message.answer(texts.GENERATE_KTP_ASK_PREDMET)
        return
    await state.update_data(predmet=predmet)
    await state.set_state(GenerateKTP.waiting_for_klass)
    await message.answer(texts.GENERATE_KTP_ASK_KLASS)


@router.message(GenerateKTP.waiting_for_klass)
async def generate_ktp_klass_received(message: Message, state: FSMContext) -> None:
    klass = (message.text or "").strip()
    if not klass:
        await message.answer(texts.GENERATE_KTP_ASK_KLASS)
        return
    await state.update_data(klass=klass)
    await state.set_state(GenerateKTP.waiting_for_hours_week)
    await message.answer(texts.GENERATE_KTP_ASK_HOURS_WEEK)


@router.message(GenerateKTP.waiting_for_hours_week)
async def generate_ktp_hours_week_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_KTP_HOURS_NOT_A_NUMBER)
        return
    await state.update_data(chasov_v_nedelu=int(text))
    await state.set_state(GenerateKTP.waiting_for_hours_year)
    await message.answer(texts.GENERATE_KTP_ASK_HOURS_YEAR)


@router.message(GenerateKTP.waiting_for_hours_year)
async def generate_ktp_hours_year_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer(texts.GENERATE_KTP_HOURS_NOT_A_NUMBER)
        return
    await state.update_data(chasov_v_god=int(text))
    await state.set_state(GenerateKTP.waiting_for_topics)
    await message.answer(texts.GENERATE_KTP_ASK_TOPICS)


@router.message(GenerateKTP.waiting_for_topics)
async def generate_ktp_topics_received(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    topics = [] if text in ("-", "") else [line.strip() for line in text.splitlines() if line.strip()]
    await state.update_data(topics=topics)

    data = await state.get_data()
    summary = texts.GENERATE_KTP_CONFIRM_SUMMARY.format(
        predmet=data["predmet"],
        klass=data["klass"],
        hours_week=data["chasov_v_nedelu"],
        hours_year=data["chasov_v_god"],
        topics_count=len(topics) if topics else "не даны, составлю сам",
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.GENERATE_KTP_CONFIRM_BUTTON, callback_data="genktp_confirm"),
                InlineKeyboardButton(text=texts.GENERATE_KTP_CANCEL_BUTTON, callback_data="genktp_cancel"),
            ]
        ]
    )
    await state.set_state(GenerateKTP.waiting_for_confirmation)
    await message.answer(summary, reply_markup=keyboard)


@router.callback_query(GenerateKTP.waiting_for_confirmation, F.data == "genktp_cancel")
async def generate_ktp_cancelled(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.GENERATE_KTP_CANCELLED)
    await callback.answer()


@router.callback_query(GenerateKTP.waiting_for_confirmation, F.data == "genktp_confirm")
async def generate_ktp_confirmed(callback: CallbackQuery, state: FSMContext) -> None:
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
    await callback.message.answer(texts.GENERATE_KTP_QUEUED)
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
        )

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
        return {"generated_ksp_id": result["id"], "docx_path": str(docx_path)}

    return handler


def make_generate_ktp_handler(bot: Bot):
    """Полный конвейер генерации КТП (core.ktp_generator.generate_and_save_ktp,
    блок Р4) и отправка готового файла. Тем же путём, что generate_ksp:
    любая ошибка уходит наверх как есть, core.queue решает про ретрай и
    финальное уведомление."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

        result = await generate_and_save_ktp(
            teacher_id=payload["teacher_id"],
            predmet=payload["predmet"],
            klass=payload["klass"],
            chasov_v_nedelu=payload["chasov_v_nedelu"],
            chasov_v_god=payload["chasov_v_god"],
            topics=payload.get("topics"),
        )

        docx_path = Path(result["docx_path"])
        caption = texts.GENERATE_KTP_RESULT_CAPTION.format(
            predmet=payload["predmet"], klass=payload["klass"]
        )
        if result["ktp_entries_inserted"]:
            caption += texts.GENERATE_KTP_ENTRIES_NOTE.format(count=result["ktp_entries_inserted"])

        await bot.send_document(chat_id, FSInputFile(docx_path), caption=caption)
        return {"docx_path": str(docx_path), "ktp_entries_inserted": result["ktp_entries_inserted"]}

    return handler
