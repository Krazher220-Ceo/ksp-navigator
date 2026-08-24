"""
bot/handlers.py — все команды Telegram-бота (блок Б8).

Зачем модуль: единственное место, где Telegram-сообщения превращаются в
вызовы core/*. Никакой бизнес-логики здесь нет — парсинг, генерация,
работа с БД целиком в core/, здесь только маршрутизация и диалоги (FSM).

Что осознанно не делает: не содержит ни одной строки текста напрямую —
все формулировки в bot/texts.py (PLAN_STAGE1.md, Б8.1), редактировать
их можно не трогая логику. /upload_ktp обрабатывается СИНХРОННО (не
через очередь): в tasks.type жёстко два значения (parse_ksp,
generate_ksp, CHECK в schema.sql), а разбор КТП — быстрый и без LLM,
третий тип задачи под него заводить незачем.

На что опирается: aiogram 3 (Router, FSM), core.db, core.ksp_parser,
core.ktp_parser, core.templates, core.ksp_generator, core.queue.
Хендлеры задач очереди (parse_ksp/generate_ksp) — фабрики, которым
нужен экземпляр Bot для отправки файлов/сообщений; создаются в
bot/main.py, где Bot уже существует.
"""

import json
import logging
import uuid
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from bot import texts
from bot.states import Generate, TeacherProfile, UploadKSP, UploadKTP
from core.config import settings
from core.db import execute, query
from core.ksp_generator import generate_and_save_ksp, guess_objective_code
from core.ksp_parser import build_style_profile, parse_ksp, save_style_profile
from core.ktp_parser import KTPParseError, parse_ktp_file, save_ktp_entries
from core.llm_client import LLMError
from core.queue import MAX_RETRIES, enqueue
from core.templates import get_template, list_templates

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

    data = await state.get_data()
    name = data["name"]
    telegram_user_id = message.from_user.id

    existing = _get_teacher(telegram_user_id)
    if existing:
        execute(
            "UPDATE teachers SET name = ?, subject = ? WHERE telegram_user_id = ?",
            (name, subject, telegram_user_id),
        )
        response = texts.TEACHER_UPDATED
    else:
        execute(
            "INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            (name, subject, telegram_user_id),
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
    await message.answer(texts.UPLOAD_KTP_SUCCESS.format(inserted=result["inserted"], codes_warning=warning))
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
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.TEMPLATES_BUTTON, web_app=WebAppInfo(url=settings.webapp_url))]]
    )
    await message.answer(texts.TEMPLATES_MESSAGE, reply_markup=keyboard)


# =====================================================================
# /generate — FSM: тема -> код -> раздел -> класс -> продолжительность
#             -> шаблон -> подтверждение -> очередь
#
# Спецификация (PLAN_STAGE1.md, Б8.2) в сокращённом виде перечисляет
# "тема -> код -> шаблон -> подтверждение", но generate_and_save_ksp
# (блок Б6) требует ещё razdel/klass/duration_minutes — их неоткуда
# взять, кроме как спросить. Не хотелось молча подставлять выдуманные
# значения (класс/раздел/минуты урока — это не то, что можно угадать).
# =====================================================================


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

    duration = int(text)
    await state.update_data(duration_minutes=duration)

    data = await state.get_data()
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


@router.callback_query(Generate.waiting_for_template, F.data.startswith("gen_tpl:"))
async def generate_template_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    template_id = int(callback.data.split(":", 1)[1])
    template = get_template(template_id)
    if template is None:
        await callback.answer("Такого шаблона больше нет, попробуйте /generate заново", show_alert=True)
        return

    await state.update_data(template_id=template_id)
    data = await state.get_data()

    summary = texts.GENERATE_CONFIRM_SUMMARY.format(
        topic=data["topic"],
        razdel=data["razdel"],
        objective_code=data.get("objective_code") or "не указан",
        klass=data["klass"],
        duration=data["duration_minutes"],
        template_name=template["name"],
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
    await callback.message.answer(summary, reply_markup=keyboard)
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
    }
    enqueue("generate_ksp", payload, chat_id=callback.message.chat.id)
    await state.clear()
    await callback.message.answer(texts.GENERATE_QUEUED)
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
    rows = query("SELECT docx_path FROM generated_ksp WHERE id = ?", (generated_id,))

    if not rows or not Path(rows[0]["docx_path"]).exists():
        await callback.answer(texts.HISTORY_FILE_MISSING, show_alert=True)
        return

    await callback.message.answer_document(FSInputFile(rows[0]["docx_path"]))
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

        parsed_list = [parse_ksp(p) for p in file_paths]

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


def make_generate_ksp_handler(bot: Bot):
    """Полный конвейер генерации (core.ksp_generator.generate_and_save_ksp,
    блок Б6) и отправка готового файла. Любая ошибка (LLM недоступен,
    невалидный ответ) уходит наверх как есть — это настоящий провал,
    core.queue сам решит про ретрай/финальное уведомление."""

    async def handler(task: dict) -> dict:
        payload = task["payload"]
        chat_id = task["telegram_chat_id"]

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
        )

        docx_path = Path(result["docx_path"])
        caption = texts.GENERATE_RESULT_CAPTION.format(topic=payload["topic"])

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
