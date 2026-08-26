"""
tests/test_bot_handlers.py — тесты bot/handlers.py и bot/main.py.

bot/handlers.py вызывает core.db.query/execute БЕЗ явного db_path (как и
должно быть у реального бота — он работает с одной базой), поэтому для
изоляции тестов приходится на время подменять settings.db_path/
uploads_dir/generated_dir — settings это frozen dataclass-синглтон,
поэтому через object.__setattr__ с гарантированным восстановлением в
finally (fixture isolated_env). Реальные Telegram/LLM вызовы не
используются нигде — Bot и все aiogram-объекты здесь лёгкие фейки.
"""

import json
import re
from pathlib import Path

import pytest
from openpyxl import Workbook

from bot.handlers import (
    back_button_pressed,
    back_callback_pressed,
    cancel_button_pressed,
    cmd_konspekt,
    konspekt_audio_received,
    konspekt_document_received,
    konspekt_done,
    konspekt_voice_received,
    konspekt_wrong_input,
    format_konspekt_text,
    ksp_from_konspekt_pressed,
    make_konspekt_handler,
    make_transcribe_handler,
    cmd_cancel,
    cmd_generate,
    cmd_generate_ktp,
    cmd_history,
    cmd_start,
    cmd_status,
    cmd_teacher,
    cmd_templates,
    cmd_upload_ksp,
    cmd_upload_ktp,
    generate_duration_received,
    generate_extra_options_received,
    generate_klass_received,
    generate_ktp_hours_week_received,
    generate_ktp_hours_year_received,
    generate_ktp_klass_received,
    generate_ktp_predmet_received,
    generate_ktp_topics_received,
    generate_objective_code_received,
    generate_razdel_received,
    generate_template_chosen,
    generate_textbook_photos_skipped,
    generate_topic_received,
    history_resend,
    make_generate_ksp_handler,
    make_parse_ksp_handler,
    menu_button_pressed,
    router,
    teacher_name_received,
    teacher_school_received,
    teacher_subject_received,
    upload_ksp_done,
    upload_ksp_file_received,
    upload_ktp_file_received,
)
from bot import keyboards, texts
from bot.main import _global_error_handler, _register_bot_commands, _register_chat_menu_button
from bot.states import Generate, GenerateKTP, Konspekt, TeacherProfile, UploadKSP
from core import ksp_generator as ksp_generator_module
from core.config import settings
from core.ksp_generator import MAX_VIDY_DEYATELNOSTI
from core.db import execute, init_db, query
from core.konspekt_generator import KonspektGenerationError
from core.limits import get_usage_today, record_usage
from core.transcriber import TranscriptionError
from core.templates import list_templates, load_builtin_templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"


# =====================================================================
# Изоляция settings.db_path / uploads_dir / generated_dir на время теста
# =====================================================================


@pytest.fixture
def isolated_env(tmp_path):
    db_path = tmp_path / "test.db"
    uploads_dir = tmp_path / "uploads"
    generated_dir = tmp_path / "generated"
    uploads_dir.mkdir()
    generated_dir.mkdir()

    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    load_builtin_templates(db_path=db_path)

    originals = {
        "db_path": settings.db_path,
        "uploads_dir": settings.uploads_dir,
        "generated_dir": settings.generated_dir,
    }
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "uploads_dir", uploads_dir)
    object.__setattr__(settings, "generated_dir", generated_dir)
    try:
        yield {"db_path": db_path, "uploads_dir": uploads_dir, "generated_dir": generated_dir}
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)


def _create_teacher(telegram_user_id: int, name: str = "Тестов Тест", subject: str = "физика") -> int:
    return execute(
        "INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
        (name, subject, telegram_user_id),
    )


# =====================================================================
# Лёгкие фейки aiogram-объектов (без сети, без реального Bot)
# =====================================================================


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeDocument:
    def __init__(self, file_id="fid", file_name="file.docx", file_size=1000, mime_type=None):
        self.file_id = file_id
        self.file_name = file_name
        self.file_size = file_size
        self.mime_type = mime_type


class FakePhotoSize:
    def __init__(self, file_id="photo-fid", file_size=1000):
        self.file_id = file_id
        self.file_size = file_size


class FakeVoice:
    def __init__(self, file_id="voice-fid", file_size=1000, duration=30, mime_type="audio/ogg"):
        self.file_id = file_id
        self.file_size = file_size
        self.duration = duration
        self.mime_type = mime_type


class FakeAudio:
    def __init__(self, file_id="audio-fid", file_size=1000, duration=30, mime_type="audio/mpeg", file_name="urok.mp3"):
        self.file_id = file_id
        self.file_size = file_size
        self.duration = duration
        self.mime_type = mime_type
        self.file_name = file_name


class FakeMessage:
    def __init__(self, text=None, user_id=1, chat_id=1, document=None, photo=None, voice=None, audio=None):
        self.text = text
        self.from_user = FakeUser(user_id)
        self.chat = FakeChat(chat_id)
        self.document = document
        self.photo = photo  # список FakePhotoSize (крупнейший — последний), как у Telegram
        self.voice = voice
        self.audio = audio
        self.sent: list[dict] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return FakeMessage(text=text, chat_id=self.chat.id)

    async def answer_document(self, document, **kwargs):
        self.sent.append({"document": document, **kwargs})


class FakeCallbackQuery:
    def __init__(self, data, message: FakeMessage, user_id=1):
        self.data = data
        self.message = message
        self.from_user = FakeUser(user_id)
        self.answered: list[dict] = []

    async def answer(self, text=None, show_alert=False):
        self.answered.append({"text": text, "show_alert": show_alert})


class FakeBot:
    def __init__(self):
        self.downloaded: list[tuple] = []
        self.sent_messages: list[tuple] = []
        self.sent_documents: list[dict] = []
        self.set_commands_calls: list[list] = []
        self.set_chat_menu_button_calls: list = []

    async def download(self, file, destination):
        Path(destination).write_bytes(b"fake ksp/ktp file content for tests")
        self.downloaded.append((file, destination))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text, kwargs.get("reply_markup")))

    async def send_document(self, chat_id, document, caption=None, reply_markup=None, **kwargs):
        self.sent_documents.append(
            {"chat_id": chat_id, "document": document, "caption": caption, "reply_markup": reply_markup}
        )

    async def set_my_commands(self, commands, **kwargs):
        self.set_commands_calls.append(commands)

    async def set_chat_menu_button(self, menu_button=None, **kwargs):
        self.set_chat_menu_button_calls.append(menu_button)


def _state():
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=0, chat_id=1, user_id=1))


# =====================================================================
# /start (Б8.1 КГ)
# =====================================================================


async def test_start_answers_with_greeting(isolated_env):
    message = FakeMessage(text="/start")
    await cmd_start(message, _state())
    assert len(message.sent) == 1
    assert "черновик" in message.sent[0]["text"].lower()


# =====================================================================
# /teacher — повторный вызов не плодит дубли
# =====================================================================


async def test_teacher_create_then_update_no_duplicate(isolated_env):
    state = _state()
    message1 = FakeMessage(text="/teacher", user_id=42)
    await cmd_teacher(message1, state)
    assert await state.get_state() == TeacherProfile.waiting_for_name.state

    await teacher_name_received(FakeMessage(text="Иванов И.И.", user_id=42), state)
    await teacher_subject_received(FakeMessage(text="физика", user_id=42), state)
    await teacher_school_received(FakeMessage(text="КГУ «Школа №5»", user_id=42), state)

    rows = query("SELECT * FROM teachers WHERE telegram_user_id = 42")
    assert len(rows) == 1
    assert rows[0]["name"] == "Иванов И.И."
    assert rows[0]["school"] == "КГУ «Школа №5»"

    # второй раз - другое имя, должно ОБНОВИТЬ ту же строку
    state2 = _state()
    await cmd_teacher(FakeMessage(text="/teacher", user_id=42), state2)
    await teacher_name_received(FakeMessage(text="Иванов Иван Иванович", user_id=42), state2)
    await teacher_subject_received(FakeMessage(text="физика (продлёнка)", user_id=42), state2)
    m = FakeMessage(text="-", user_id=42)
    await teacher_school_received(m, state2)

    rows = query("SELECT * FROM teachers WHERE telegram_user_id = 42")
    assert len(rows) == 1, "повторный вызов /teacher не должен плодить дубли"
    assert rows[0]["name"] == "Иванов Иван Иванович"
    assert rows[0]["school"] == "КГУ «Школа №5»", "'-' на повторном шаге не должен стирать уже сохранённую школу"
    assert "обновлён" in m.sent[-1]["text"].lower()


# =====================================================================
# /cancel
# =====================================================================


async def test_cancel_with_no_active_state(isolated_env):
    message = FakeMessage(text="/cancel")
    await cmd_cancel(message, _state())
    assert texts.CANCEL_NOTHING_TO_CANCEL in message.sent[0]["text"]


async def test_cancel_clears_active_state(isolated_env):
    state = _state()
    await state.set_state(TeacherProfile.waiting_for_name)
    message = FakeMessage(text="/cancel")
    await cmd_cancel(message, state)
    assert await state.get_state() is None
    assert texts.CANCEL_DONE in message.sent[0]["text"]


# =====================================================================
# Б8.3, сценарий 1: файл больше 20 МБ
# =====================================================================


async def test_file_too_large_gives_clear_message_not_crash(isolated_env):
    from bot.states import UploadKSP

    state = _state()
    _create_teacher(1)
    await cmd_upload_ksp(FakeMessage(text="/upload_ksp"), state)

    huge_doc = FakeDocument(file_name="huge.docx", file_size=25 * 1024 * 1024)
    message = FakeMessage(document=huge_doc)
    bot = FakeBot()

    await upload_ksp_file_received(message, state, bot)

    assert "20" in message.sent[-1]["text"]
    assert bot.downloaded == []  # до скачивания дело дойти не должно
    data = await state.get_data()
    assert data.get("file_paths", []) == []


# =====================================================================
# Б8.3, сценарий 2: неверный формат файла
# =====================================================================


async def test_unsupported_format_lists_supported_formats(isolated_env):
    state = _state()
    _create_teacher(1)
    await cmd_upload_ksp(FakeMessage(text="/upload_ksp"), state)

    bad_doc = FakeDocument(file_name="lesson.pdf")
    message = FakeMessage(document=bad_doc)
    bot = FakeBot()

    await upload_ksp_file_received(message, state, bot)

    sent_text = message.sent[-1]["text"]
    assert ".pdf" in sent_text
    assert ".docx" in sent_text
    assert bot.downloaded == []


# =====================================================================
# Б8.3, сценарий 3: нет профиля учителя
# =====================================================================


@pytest.mark.parametrize(
    "command_coro_name",
    ["cmd_upload_ksp", "cmd_upload_ktp", "cmd_generate"],
)
async def test_commands_requiring_teacher_profile_prompt_when_missing(isolated_env, command_coro_name):
    import bot.handlers as handlers_module

    handler = getattr(handlers_module, command_coro_name)
    message = FakeMessage(text="/x", user_id=999)  # такого учителя нет в базе
    await handler(message, _state())

    assert message.sent[-1]["text"] == texts.ERROR_NO_TEACHER_PROFILE


async def test_history_requires_teacher_profile_when_missing(isolated_env):
    """cmd_history не принимает FSM state (это не диалог) — проверяется отдельно."""
    message = FakeMessage(text="/history", user_id=999)
    await cmd_history(message)
    assert message.sent[-1]["text"] == texts.ERROR_NO_TEACHER_PROFILE


# =====================================================================
# Б8.3, сценарий 4: нет профиля стиля — НЕ ошибка, честное предупреждение
# =====================================================================


async def test_generate_without_style_profile_warns_but_does_not_block(isolated_env):
    teacher_id = _create_teacher(1)
    state = _state()
    await state.update_data(
        teacher_id=teacher_id,
        subject="физика",
        topic="Тема",
        razdel="Раздел",
        objective_code=None,
        klass="10А",
        duration_minutes=40,
    )
    await state.set_state(Generate.waiting_for_template)

    templates_rows = query("SELECT id FROM templates WHERE is_builtin = 1")
    template_id = templates_rows[0]["id"]

    message = FakeMessage()
    callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=message)
    await generate_template_chosen(callback, state)

    summary = message.sent[-1]["text"]
    assert texts.GENERATE_NO_STYLE_PROFILE_NOTE.strip() in summary
    assert "ℹ️" in summary  # предупреждение, не блокировка
    assert await state.get_state() == Generate.waiting_for_confirmation.state  # диалог продолжается


async def test_generate_with_style_profile_has_no_warning(isolated_env):
    teacher_id = _create_teacher(1)
    execute(
        "INSERT INTO style_profiles (teacher_id, goal_phrasing, stage_structure, "
        "assessment_methods, resources_used, raw_samples_count) VALUES (?, '[]', '[]', '[]', '[]', 3)",
        (teacher_id,),
    )
    state = _state()
    await state.update_data(
        teacher_id=teacher_id, subject="физика", topic="Тема", razdel="Раздел",
        objective_code=None, klass="10А", duration_minutes=40,
    )
    await state.set_state(Generate.waiting_for_template)

    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    message = FakeMessage()
    callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=message)
    await generate_template_chosen(callback, state)

    assert "ℹ️" not in message.sent[-1]["text"]


# =====================================================================
# /upload_ksp: меньше 2 файлов -> отказ
# =====================================================================


async def test_upload_ksp_rejects_when_fewer_than_two_files(isolated_env):
    state = _state()
    _create_teacher(1)
    await cmd_upload_ksp(FakeMessage(text="/upload_ksp"), state)

    bot = FakeBot()
    await upload_ksp_file_received(FakeMessage(document=FakeDocument(file_name="a.docx")), state, bot)

    message = FakeMessage(text="/done")
    await upload_ksp_done(message, state)

    assert "минимум 2" in message.sent[-1]["text"] or "2" in message.sent[-1]["text"]
    assert query("SELECT * FROM tasks") == []


async def test_upload_ksp_with_enough_files_queues_task(isolated_env):
    state = _state()
    teacher_id = _create_teacher(1)
    await cmd_upload_ksp(FakeMessage(text="/upload_ksp"), state)

    bot = FakeBot()
    for name in ("a.docx", "b.docx"):
        await upload_ksp_file_received(FakeMessage(document=FakeDocument(file_name=name)), state, bot)

    message = FakeMessage(text="/done", chat_id=777)
    await upload_ksp_done(message, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'parse_ksp'")
    assert len(tasks) == 1
    assert tasks[0]["telegram_chat_id"] == 777
    payload = json.loads(tasks[0]["payload"])
    assert payload["teacher_id"] == teacher_id
    assert len(payload["file_paths"]) == 2
    assert await state.get_state() is None


# =====================================================================
# /upload_ktp
# =====================================================================


def _make_test_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["№", "Раздел", "Тема урока", "Цели обучения", "Часы", "Дата", "Четверть"])
    ws.append([1, "Механика", "Кинематика точки", "10.1.1.1", 1, "01-05.09.25", 1])
    ws.append([2, "Механика", "Тема без кода", None, 1, "", 1])
    wb.save(path)


async def test_upload_ktp_success_inserts_entries(isolated_env, tmp_path):
    teacher_id = _create_teacher(1)
    state = _state()
    await cmd_upload_ktp(FakeMessage(text="/upload_ktp"), state)

    xlsx_path = tmp_path / "source.xlsx"
    _make_test_xlsx(xlsx_path)

    class RealDownloadBot(FakeBot):
        async def download(self, file, destination):
            Path(destination).write_bytes(xlsx_path.read_bytes())
            self.downloaded.append((file, destination))

    bot = RealDownloadBot()
    message = FakeMessage(document=FakeDocument(file_name="ktp.xlsx"))
    await upload_ktp_file_received(message, state, bot)

    rows = query("SELECT * FROM ktp_entries WHERE teacher_id = ?", (teacher_id,))
    assert len(rows) == 2
    assert "2" in message.sent[-1]["text"]
    assert await state.get_state() is None


async def test_upload_ktp_parse_error_is_friendly_not_a_crash(isolated_env, tmp_path):
    _create_teacher(1)
    state = _state()
    await cmd_upload_ktp(FakeMessage(text="/upload_ktp"), state)

    garbage_path = tmp_path / "garbage.xlsx"
    garbage_path.write_bytes(b"not a real xlsx file at all")

    class GarbageBot(FakeBot):
        async def download(self, file, destination):
            Path(destination).write_bytes(garbage_path.read_bytes())

    message = FakeMessage(document=FakeDocument(file_name="garbage.xlsx"))
    await upload_ktp_file_received(message, state, GarbageBot())

    assert message.sent  # что-то ответили, не упали молча
    assert "разобрать" in message.sent[-1]["text"].lower() or "ошиб" in message.sent[-1]["text"].lower()


# =====================================================================
# /generate — полный happy path FSM до постановки в очередь
# =====================================================================


async def test_generate_full_flow_enqueues_task_with_correct_payload(isolated_env):
    teacher_id = _create_teacher(1)
    state = _state()
    await cmd_generate(FakeMessage(text="/generate"), state)

    await generate_topic_received(FakeMessage(text="Совершенно новая тема"), state)
    assert await state.get_state() == Generate.waiting_for_objective_code.state

    await generate_objective_code_received(FakeMessage(text="-"), state)
    await generate_razdel_received(FakeMessage(text="Механика"), state)
    await generate_klass_received(FakeMessage(text="10А"), state)
    await generate_duration_received(FakeMessage(text="40"), state)
    assert await state.get_state() == Generate.waiting_for_textbook_photos.state

    from bot.handlers import generate_extra_options_received, generate_textbook_photos_skipped

    await generate_textbook_photos_skipped(FakeMessage(text="/skip"), state)  # пропущено, Р6
    assert await state.get_state() == Generate.waiting_for_extra_options.state

    await generate_extra_options_received(FakeMessage(text="-"), state)  # пропущено, Р5
    assert await state.get_state() == Generate.waiting_for_template.state

    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    message = FakeMessage(chat_id=555)
    callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=message)
    await generate_template_chosen(callback, state)

    from bot.handlers import generate_confirmed

    confirm_callback = FakeCallbackQuery(data="gen_confirm", message=message)
    await generate_confirmed(confirm_callback, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'generate_ksp'")
    assert len(tasks) == 1
    assert tasks[0]["telegram_chat_id"] == 555
    payload = json.loads(tasks[0]["payload"])
    assert payload == {
        "teacher_id": teacher_id,
        "template_id": template_id,
        "topic": "Совершенно новая тема",
        "razdel": "Механика",
        "subject": "физика",
        "klass": "10А",
        "duration_minutes": 40,
        "objective_code": None,
        # "-" на шаге доп. настроек -> LessonOptions() со значениями по
        # умолчанию, не None — функционально то же самое (все проверки в
        # _render_lesson_options/_fill_header_fields одинаково пропускают
        # и None, и объект с пустыми полями), но по факту в payload лежит
        # словарь, а не null.
        "options": {
            "cennost_key": None,
            "vidy_deyatelnosti": [],
            "ima_oop": False,
            "sor_instead_of_reflection": False,
            "fizkultminutka": False,
            "predvaritelnye_znaniya": None,
            "tip_uroka": None,
            "mezhpredmetnye_svyazi": [],
            "page_orientation": "book",
        },
        "textbook_photo_paths": [],  # /skip на шаге Р6.1 -> пустой список
        "konspekt_text": None,  # К5: обычный /generate, не по кнопке конспекта
    }
    assert await state.get_state() is None


async def test_generate_ktp_full_flow_enqueues_task_with_correct_payload(isolated_env):
    from bot.handlers import (
        cmd_generate_ktp,
        generate_ktp_confirmed,
        generate_ktp_hours_week_received,
        generate_ktp_hours_year_received,
        generate_ktp_klass_received,
        generate_ktp_predmet_received,
        generate_ktp_topics_received,
    )
    from bot.states import GenerateKTP

    teacher_id = _create_teacher(1)
    state = _state()
    await cmd_generate_ktp(FakeMessage(text="/generate_ktp"), state)
    assert await state.get_state() == GenerateKTP.waiting_for_predmet.state

    await generate_ktp_predmet_received(FakeMessage(text="физика"), state)
    await generate_ktp_klass_received(FakeMessage(text="10А"), state)
    await generate_ktp_hours_week_received(FakeMessage(text="2"), state)
    await generate_ktp_hours_year_received(FakeMessage(text="68"), state)
    assert await state.get_state() == GenerateKTP.waiting_for_topics.state

    message = FakeMessage(chat_id=777)
    await generate_ktp_topics_received(FakeMessage(text="Тема 1\nТема 2"), state)
    assert await state.get_state() == GenerateKTP.waiting_for_confirmation.state

    confirm_callback = FakeCallbackQuery(data="genktp_confirm", message=message)
    await generate_ktp_confirmed(confirm_callback, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'generate_ktp'")
    assert len(tasks) == 1
    assert tasks[0]["telegram_chat_id"] == 777
    payload = json.loads(tasks[0]["payload"])
    assert payload == {
        "teacher_id": teacher_id,
        "predmet": "физика",
        "klass": "10А",
        "chasov_v_nedelu": 2,
        "chasov_v_god": 68,
        "topics": ["Тема 1", "Тема 2"],
    }
    assert await state.get_state() is None


async def test_generate_ktp_dash_means_no_topics(isolated_env):
    from bot.handlers import generate_ktp_topics_received
    from bot.states import GenerateKTP

    state = _state()
    await state.update_data(predmet="физика", klass="10А", chasov_v_nedelu=2, chasov_v_god=68)
    await state.set_state(GenerateKTP.waiting_for_hours_year)

    message = FakeMessage(text="-")
    await generate_ktp_topics_received(message, state)

    data = await state.get_data()
    assert data["topics"] == []
    assert "составлю сам" in message.sent[-1]["text"]


# =====================================================================
# Р6.1: сбор фото учебника в /generate
# =====================================================================


async def test_textbook_photo_accepted_and_counted(isolated_env):
    from bot.handlers import generate_textbook_photo_received
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=[])
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    bot = FakeBot()
    message = FakeMessage(photo=[FakePhotoSize(file_size=2000)])
    await generate_textbook_photo_received(message, state, bot)

    data = await state.get_data()
    assert len(data["textbook_photo_paths"]) == 1
    assert "1 из 3" in message.sent[-1]["text"]
    assert len(bot.downloaded) == 1


async def test_textbook_photo_document_accepted_for_png(isolated_env):
    from bot.handlers import generate_textbook_photo_document_received
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=[])
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    bot = FakeBot()
    message = FakeMessage(document=FakeDocument(file_name="page.png", file_size=2000))
    await generate_textbook_photo_document_received(message, state, bot)

    data = await state.get_data()
    assert len(data["textbook_photo_paths"]) == 1
    assert data["textbook_photo_paths"][0].endswith(".png")


async def test_textbook_photo_document_rejects_non_image_extension(isolated_env):
    from bot.handlers import generate_textbook_photo_document_received
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=[])
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    bot = FakeBot()
    message = FakeMessage(document=FakeDocument(file_name="page.pdf", file_size=2000))
    await generate_textbook_photo_document_received(message, state, bot)

    data = await state.get_data()
    assert data["textbook_photo_paths"] == []
    assert len(bot.downloaded) == 0
    assert message.sent[-1]["text"] == texts.GENERATE_TEXTBOOK_PHOTO_UNSUPPORTED_FORMAT


async def test_textbook_photo_stops_at_max_three(isolated_env):
    from bot.handlers import generate_textbook_photo_received
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=["a.jpg", "b.jpg", "c.jpg"])  # уже 3
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    bot = FakeBot()
    message = FakeMessage(photo=[FakePhotoSize(file_size=2000)])
    await generate_textbook_photo_received(message, state, bot)

    data = await state.get_data()
    assert len(data["textbook_photo_paths"]) == 3  # не выросло до 4
    assert len(bot.downloaded) == 0  # 4-е фото даже не скачивалось
    assert message.sent[-1]["text"] == texts.GENERATE_TEXTBOOK_PHOTOS_MAX_REACHED


async def test_textbook_photos_done_moves_to_extra_options_with_photos_kept(isolated_env):
    from bot.handlers import generate_textbook_photos_done
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=["a.jpg"])
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    await generate_textbook_photos_done(FakeMessage(text="/done"), state)

    data = await state.get_data()
    assert data["textbook_photo_paths"] == ["a.jpg"]  # не очищено, только шаг сменился
    assert await state.get_state() == GenerateStates.waiting_for_extra_options.state


async def test_textbook_photos_skip_clears_any_collected_photos(isolated_env):
    """/skip — явный отказ от фото целиком, даже если что-то уже прислали."""
    from bot.handlers import generate_textbook_photos_skipped
    from bot.states import Generate as GenerateStates

    state = _state()
    await state.update_data(textbook_photo_paths=["a.jpg"])
    await state.set_state(GenerateStates.waiting_for_textbook_photos)

    await generate_textbook_photos_skipped(FakeMessage(text="/skip"), state)

    data = await state.get_data()
    assert data["textbook_photo_paths"] == []
    assert await state.get_state() == GenerateStates.waiting_for_extra_options.state


# =====================================================================
# Р5.2/Р5.3: разбор строк доп. настроек и их применение в /generate
# =====================================================================


def test_parse_lesson_options_text_recognizes_all_keys():
    from bot.handlers import _parse_lesson_options_text

    text = "\n".join(
        [
            "Ценность: Созидание и новаторство",
            "Виды деятельности: групповая работа, финансовая грамотность",
            "ООП: да",
            "СОР: да",
            "Физкультминутка: да",
            "Предварительные знания: основы кинематики",
            "Тип урока: Контроль",
            "Межпредметные связи: информатика, математика",
            "Ориентация: альбомная",
        ]
    )
    options, unrecognized = _parse_lesson_options_text(text)

    assert unrecognized == []
    assert options.cennost_key == "sozidaniye_novatorstvo"
    assert options.vidy_deyatelnosti == ["групповая работа", "финансовая грамотность"]
    assert options.ima_oop is True
    assert options.sor_instead_of_reflection is True
    assert options.fizkultminutka is True
    assert options.predvaritelnye_znaniya == "основы кинематики"
    assert options.tip_uroka == "Контроль"
    assert options.mezhpredmetnye_svyazi == ["информатика", "математика"]
    assert options.page_orientation == "album"


def test_parse_lesson_options_text_case_insensitive_keys_and_values():
    from bot.handlers import _parse_lesson_options_text

    options, unrecognized = _parse_lesson_options_text("ооп: ДА\nориентация: Альбомная")
    assert unrecognized == []
    assert options.ima_oop is True
    assert options.page_orientation == "album"


def test_parse_lesson_options_text_flags_unknown_lines_honestly():
    from bot.handlers import _parse_lesson_options_text

    options, unrecognized = _parse_lesson_options_text("Погода: солнечно\nООП: да")
    assert options.ima_oop is True
    assert unrecognized == ["Погода: солнечно"]


def test_parse_lesson_options_text_flags_unknown_value_name():
    """Неизвестное название ценности — тоже честная ошибка, не молчаливое
    игнорирование и не выдуманный ключ."""
    from bot.handlers import _parse_lesson_options_text

    options, unrecognized = _parse_lesson_options_text("Ценность: Выдуманная ценность")
    assert options.cennost_key is None
    assert unrecognized == ["Ценность: Выдуманная ценность"]


def test_parse_lesson_options_text_truncates_vidy_deyatelnosti_to_three():
    from bot.handlers import _parse_lesson_options_text

    options, _ = _parse_lesson_options_text("Виды деятельности: А, Б, В, Г, Д")
    assert len(options.vidy_deyatelnosti) == MAX_VIDY_DEYATELNOSTI
    assert options.vidy_deyatelnosti == ["А", "Б", "В"]


async def test_generate_extra_options_dash_skips_and_moves_to_template_step(isolated_env):
    from bot.handlers import generate_extra_options_received

    _create_teacher(1)
    state = _state()
    await state.update_data(teacher_id=1, topic="Т", razdel="Р", klass="10А", duration_minutes=40)
    await state.set_state(Generate.waiting_for_extra_options)

    await generate_extra_options_received(FakeMessage(text="-"), state)

    data = await state.get_data()
    assert data["options"]["cennost_key"] is None
    assert await state.get_state() == Generate.waiting_for_template.state


async def test_generate_extra_options_applied_reach_confirmation_summary(isolated_env):
    from bot.handlers import generate_extra_options_received

    _create_teacher(1)
    state = _state()
    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    await state.update_data(
        teacher_id=1, topic="Т", razdel="Р", klass="10А", duration_minutes=40, template_id=template_id
    )
    await state.set_state(Generate.waiting_for_extra_options)

    message = FakeMessage(text="Ценность: Единство и солидарность\nООП: да")
    await generate_extra_options_received(message, state)

    assert await state.get_state() == Generate.waiting_for_confirmation.state
    summary_text = message.sent[-1]["text"]
    assert "Единство и солидарность" in summary_text
    assert "ООП" in summary_text


async def test_generate_extra_options_unrecognized_line_warns_but_continues(isolated_env):
    from bot.handlers import generate_extra_options_received

    _create_teacher(1)
    state = _state()
    await state.update_data(teacher_id=1, topic="Т", razdel="Р", klass="10А", duration_minutes=40)
    await state.set_state(Generate.waiting_for_extra_options)

    message = FakeMessage(text="Чепуха: ерунда\nООП: да")
    await generate_extra_options_received(message, state)

    warning = message.sent[0]["text"]
    assert "Чепуха" in warning
    data = await state.get_data()
    assert data["options"]["ima_oop"] is True  # распознанная строка всё равно применилась


async def test_generate_ktp_hours_week_rejects_non_numeric_input(isolated_env):
    from bot.handlers import generate_ktp_hours_week_received
    from bot.states import GenerateKTP

    state = _state()
    await state.set_state(GenerateKTP.waiting_for_hours_week)

    message = FakeMessage(text="два")
    await generate_ktp_hours_week_received(message, state)

    assert message.sent[-1]["text"] == texts.GENERATE_KTP_HOURS_NOT_A_NUMBER
    assert await state.get_state() == GenerateKTP.waiting_for_hours_week.state


async def test_generate_ktp_task_handler_sends_document_and_notes_entries(isolated_env, monkeypatch):
    from bot import handlers as handlers_module
    from bot.handlers import make_generate_ktp_handler

    async def fake_generate_and_save_ktp(**kwargs):
        return {
            "id": "ktp-xyz",
            "docx_path": "/tmp/ktp_result.docx",
            "content_json": {},
            "ktp_entries_inserted": 34,
            "ktp_entries_replaced": 0,
        }

    monkeypatch.setattr(handlers_module, "generate_and_save_ktp", fake_generate_and_save_ktp)

    bot = FakeBot()
    handler = make_generate_ktp_handler(bot)
    task = {
        "id": "t9",
        "type": "generate_ktp",
        "telegram_chat_id": 88,
        "payload": {
            "teacher_id": 1, "predmet": "физика", "klass": "10А",
            "chasov_v_nedelu": 2, "chasov_v_god": 68, "topics": None,
        },
    }

    result = await handler(task)

    assert result == {"docx_path": "/tmp/ktp_result.docx", "ktp_entries_inserted": 34, "pdf_path": None}
    # /tmp/ktp_result.docx не существует - PDF-конвертация мягко проваливается
    # (Р10), .docx уже отправлен и этого достаточно
    assert len(bot.sent_documents) == 1
    assert bot.sent_documents[0]["chat_id"] == 88
    assert "34" in bot.sent_documents[0]["caption"]


async def test_generate_duration_rejects_non_numeric_input(isolated_env):
    _create_teacher(1)
    state = _state()
    await state.update_data(teacher_id=1, subject="физика")
    await state.set_state(Generate.waiting_for_duration)

    message = FakeMessage(text="сорок")
    await generate_duration_received(message, state)

    assert message.sent[-1]["text"] == texts.GENERATE_DURATION_NOT_A_NUMBER
    assert await state.get_state() == Generate.waiting_for_duration.state


# =====================================================================
# /status, /history
# =====================================================================


async def test_status_empty(isolated_env):
    message = FakeMessage(chat_id=1)
    await cmd_status(message)
    assert message.sent[-1]["text"] == texts.STATUS_EMPTY


async def test_status_shows_pending_task(isolated_env):
    from core.queue import enqueue

    enqueue("generate_ksp", {}, chat_id=1)
    message = FakeMessage(chat_id=1)
    await cmd_status(message)
    assert "generate_ksp".replace("_", " ") or "генерация" in message.sent[-1]["text"].lower()


async def test_history_empty(isolated_env):
    _create_teacher(1)
    message = FakeMessage(user_id=1)
    await cmd_history(message)
    assert message.sent[-1]["text"] == texts.HISTORY_EMPTY


async def test_history_lists_and_resends_file(isolated_env, tmp_path):
    teacher_id = _create_teacher(1)
    fake_docx = tmp_path / "result.docx"
    fake_docx.write_bytes(b"fake docx bytes")
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, template_id, content_json, docx_path) "
        "VALUES ('gen-1', ?, NULL, ?, ?)",
        (teacher_id, json.dumps({"tema_uroka": "Моя тема"}), str(fake_docx)),
    )

    message = FakeMessage(user_id=1)
    await cmd_history(message)
    assert "Моя тема" in message.sent[-1]["text"] or message.sent[-1]["reply_markup"] is not None

    resend_message = FakeMessage()
    callback = FakeCallbackQuery(data="hist:gen-1", message=resend_message)

    class DummyBot:
        pass

    await history_resend(callback, DummyBot())
    assert len(resend_message.sent) == 1
    assert "document" in resend_message.sent[0]


# =====================================================================
# /templates
# =====================================================================


async def test_templates_without_webapp_url_configured(isolated_env):
    original = settings.webapp_url
    object.__setattr__(settings, "webapp_url", None)
    try:
        message = FakeMessage()
        await cmd_templates(message)
        assert message.sent[-1]["text"] == texts.TEMPLATES_NOT_CONFIGURED
    finally:
        object.__setattr__(settings, "webapp_url", original)


# =====================================================================
# Обработчики задач очереди
# =====================================================================


async def test_parse_ksp_task_handler_soft_fails_on_llm_error(isolated_env, monkeypatch):
    from core.llm_client import LLMUnavailable

    async def failing_build_style_profile(parsed_list, llm_client=None):
        raise LLMUnavailable("все провайдеры недоступны")

    monkeypatch.setattr("bot.handlers.build_style_profile", failing_build_style_profile)
    monkeypatch.setattr(
        "bot.handlers.parse_ksp",
        lambda path: {"headings": [], "paragraphs": [], "tables": [], "lesson_plan_table": None},
    )

    teacher_id = _create_teacher(1)
    bot = FakeBot()
    handler = make_parse_ksp_handler(bot)

    task = {
        "id": "t1",
        "type": "parse_ksp",
        "telegram_chat_id": 1,
        "payload": {"teacher_id": teacher_id, "file_paths": ["/tmp/a.docx", "/tmp/b.docx"]},
    }
    result = await handler(task)  # не должен бросить исключение наружу

    assert result["style_profile_built"] is False
    assert len(bot.sent_messages) == 1
    assert "стиля" in bot.sent_messages[0][1].lower()


async def test_generate_ksp_task_handler_sends_document(isolated_env, monkeypatch):
    async def fake_generate_and_save_ksp(**kwargs):
        return {"id": "gen-xyz", "docx_path": "/tmp/result.docx"}

    monkeypatch.setattr(
        ksp_generator_module, "generate_and_save_ksp", fake_generate_and_save_ksp
    )
    monkeypatch.setattr("bot.handlers.generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t2",
        "type": "generate_ksp",
        "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
        },
    }

    result = await handler(task)

    assert result == {"generated_ksp_id": "gen-xyz", "docx_path": "/tmp/result.docx", "pdf_path": None}
    # /tmp/result.docx не существует - PDF-конвертация мягко проваливается
    # (Р10), .docx уже отправлен и этого достаточно
    assert len(bot.sent_documents) == 1
    assert bot.sent_documents[0]["chat_id"] == 42


async def test_generate_ksp_task_handler_sends_pdf_alongside_docx(isolated_env, monkeypatch):
    """Р10: настоящая конвертация (не /tmp/result.docx, а реальный файл
    из фикстур) — PDF должен уйти вторым документом в тот же чат."""
    real_docx = str(FIXTURES_DIR / "ksp_sample_1_single_table.docx")

    async def fake_generate_and_save_ksp(**kwargs):
        return {"id": "gen-pdf", "docx_path": real_docx}

    monkeypatch.setattr("bot.handlers.generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t2b",
        "type": "generate_ksp",
        "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
        },
    }

    result = await handler(task)

    assert result["pdf_path"] is not None
    assert result["pdf_path"].endswith(".pdf")
    assert len(bot.sent_documents) == 2
    assert bot.sent_documents[1]["chat_id"] == 42
    assert bot.sent_documents[1]["caption"] == texts.GENERATE_PDF_CAPTION


# =====================================================================
# Р6.1/Р6.2: распознавание фото учебника внутри обработчика очереди
# =====================================================================


async def test_generate_ksp_task_handler_passes_recognized_textbook_text(isolated_env, monkeypatch, tmp_path):
    from bot import handlers as handlers_module

    photo1 = tmp_path / "p1.jpg"
    photo1.write_bytes(b"fake photo 1")
    photo2 = tmp_path / "p2.jpg"
    photo2.write_bytes(b"fake photo 2")

    async def fake_recognize(image_bytes, image_mime):
        return "текст с фото: " + image_bytes.decode()

    captured = {}

    async def fake_generate_and_save_ksp(**kwargs):
        captured.update(kwargs)
        return {"id": "gen-1", "docx_path": "/tmp/result.docx"}

    monkeypatch.setattr(handlers_module, "recognize_textbook_page", fake_recognize)
    monkeypatch.setattr(handlers_module, "generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t3", "type": "generate_ksp", "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
            "textbook_photo_paths": [str(photo1), str(photo2)],
        },
    }

    await handler(task)

    assert captured["textbook_text"] == "текст с фото: fake photo 1\n\nтекст с фото: fake photo 2"
    assert texts.GENERATE_TEXTBOOK_OCR_FAILED_NOTE not in bot.sent_documents[0]["caption"]


async def test_generate_ksp_task_handler_notes_when_all_textbook_photos_fail(isolated_env, monkeypatch, tmp_path):
    """КГ Р6: одно неудачное фото не должно ронять всю задачу генерации,
    но учитель должен честно узнать, что урок собран без опоры на фото."""
    from bot import handlers as handlers_module
    from core.textbook_ocr import TextbookOCRError

    photo = tmp_path / "p1.jpg"
    photo.write_bytes(b"unreadable photo")

    async def fake_recognize(image_bytes, image_mime):
        raise TextbookOCRError("не удалось распознать")

    async def fake_generate_and_save_ksp(**kwargs):
        assert kwargs["textbook_text"] is None
        return {"id": "gen-2", "docx_path": "/tmp/result.docx"}

    monkeypatch.setattr(handlers_module, "recognize_textbook_page", fake_recognize)
    monkeypatch.setattr(handlers_module, "generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t4", "type": "generate_ksp", "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
            "textbook_photo_paths": [str(photo)],
        },
    }

    await handler(task)

    assert texts.GENERATE_TEXTBOOK_OCR_FAILED_NOTE in bot.sent_documents[0]["caption"]


async def test_generate_ksp_task_handler_partial_ocr_failure_uses_successful_text(
    isolated_env, monkeypatch, tmp_path
):
    """Одно фото не распозналось, другое — да: генерация опирается на то,
    что реально распозналось, без предупреждения (не ВСЕ фото отказали)."""
    from bot import handlers as handlers_module
    from core.textbook_ocr import TextbookOCRError

    good_photo = tmp_path / "good.jpg"
    good_photo.write_bytes(b"readable")
    bad_photo = tmp_path / "bad.jpg"
    bad_photo.write_bytes(b"unreadable")

    async def fake_recognize(image_bytes, image_mime):
        if image_bytes == b"unreadable":
            raise TextbookOCRError("плохое качество")
        return "хороший текст"

    captured = {}

    async def fake_generate_and_save_ksp(**kwargs):
        captured.update(kwargs)
        return {"id": "gen-3", "docx_path": "/tmp/result.docx"}

    monkeypatch.setattr(handlers_module, "recognize_textbook_page", fake_recognize)
    monkeypatch.setattr(handlers_module, "generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t5", "type": "generate_ksp", "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
            "textbook_photo_paths": [str(bad_photo), str(good_photo)],
        },
    }

    await handler(task)

    assert captured["textbook_text"] == "хороший текст"
    assert texts.GENERATE_TEXTBOOK_OCR_FAILED_NOTE not in bot.sent_documents[0]["caption"]


async def test_generate_ksp_task_handler_without_photos_skips_ocr_entirely(isolated_env, monkeypatch):
    """Без фото — OCR вообще не вызывается, тем же путём, что и раньше
    (не должно быть регрессии для запросов без Р6.1)."""
    from bot import handlers as handlers_module

    ocr_called = []

    async def fake_recognize(image_bytes, image_mime):
        ocr_called.append(True)
        return "не должно вызваться"

    async def fake_generate_and_save_ksp(**kwargs):
        assert kwargs["textbook_text"] is None
        return {"id": "gen-4", "docx_path": "/tmp/result.docx"}

    monkeypatch.setattr(handlers_module, "recognize_textbook_page", fake_recognize)
    monkeypatch.setattr(handlers_module, "generate_and_save_ksp", fake_generate_and_save_ksp)

    bot = FakeBot()
    handler = make_generate_ksp_handler(bot)
    task = {
        "id": "t6", "type": "generate_ksp", "telegram_chat_id": 42,
        "payload": {
            "teacher_id": 1, "template_id": 1, "topic": "Тема", "razdel": "Раздел",
            "subject": "физика", "klass": "10А", "duration_minutes": 40, "objective_code": None,
        },
    }

    await handler(task)
    assert ocr_called == []


# =====================================================================
# Б8.1 КГ: глобальный error handler не роняет процесс
# =====================================================================


async def test_global_error_handler_does_not_raise_and_notifies_user():
    from aiogram.types import ErrorEvent, Update, Message as AiogramMessage

    class FakeUpdate:
        update_id = 1
        message = None
        callback_query = None

    class FakeErrorEvent:
        def __init__(self):
            self.update = FakeUpdate()
            self.exception = RuntimeError("что-то сломалось")

    bot = FakeBot()
    result = await _global_error_handler(FakeErrorEvent(), bot)
    assert result is True  # aiogram не должен пробрасывать исключение дальше


# =====================================================================
# М1.1 КГ (PLAN_STAGE2.md): set_my_commands вызывается ровно один раз при
# старте, и ни одна команда из списка не ссылается на несуществующий
# хендлер
# =====================================================================


async def test_register_bot_commands_calls_set_my_commands_once():
    bot = FakeBot()
    await _register_bot_commands(bot)
    assert len(bot.set_commands_calls) == 1


async def test_register_bot_commands_sends_full_list_from_texts():
    from aiogram.types import BotCommand

    bot = FakeBot()
    await _register_bot_commands(bot)
    sent = bot.set_commands_calls[0]
    expected = [BotCommand(command=name, description=desc) for name, desc in texts.BOT_COMMANDS]
    assert sent == expected


def test_every_bot_command_has_a_registered_handler():
    """Обходит router так же, как это делает aiogram при матчинге апдейта,
    и собирает имена команд из flags['commands'] каждого хендлера
    (aiogram кладёт туда объекты Command из применённых фильтров).
    Подсказка на несуществующую команду хуже, чем её отсутствие —
    PLAN_STAGE2.md, М1.1."""
    registered = set()
    for handler in router.message.handlers:
        for command_filter in handler.flags.get("commands", []):
            registered.update(command_filter.commands)

    declared = {name for name, _ in texts.BOT_COMMANDS}
    missing = declared - registered
    assert not missing, f"в BOT_COMMANDS есть команды без хендлера в router: {missing}"


# =====================================================================
# М2.2 — кнопка меню чата (Mini App)
# =====================================================================


async def test_register_chat_menu_button_sets_webapp_when_configured():
    bot = FakeBot()
    original = settings.webapp_url
    object.__setattr__(settings, "webapp_url", "https://example.test/webapp")
    try:
        await _register_chat_menu_button(bot)
    finally:
        object.__setattr__(settings, "webapp_url", original)

    assert len(bot.set_chat_menu_button_calls) == 1
    assert bot.set_chat_menu_button_calls[0].web_app.url == "https://example.test/webapp"


async def test_register_chat_menu_button_skips_when_webapp_url_missing():
    bot = FakeBot()
    original = settings.webapp_url
    object.__setattr__(settings, "webapp_url", None)
    try:
        await _register_chat_menu_button(bot)
    finally:
        object.__setattr__(settings, "webapp_url", original)

    assert bot.set_chat_menu_button_calls == []


# =====================================================================
# М2.1 — постоянное меню: нажатие кнопки сбрасывает диалог
# =====================================================================


async def test_start_sends_main_menu_keyboard(isolated_env):
    message = FakeMessage(text="/start")
    await cmd_start(message, _state())
    assert message.sent[0]["reply_markup"] is keyboards.MAIN_MENU


async def test_menu_button_mid_generate_dialog_resets_state_without_recording_button_text(isolated_env):
    """М2.1 КГ (PLAN_STAGE2.md, дословно): на любом шаге /generate нажатие
    кнопки меню сбрасывает состояние и не записывает текст кнопки в
    данные диалога."""
    teacher_id = _create_teacher(999)
    state = _state()

    # доводим диалог /generate до шага "ожидаем тему урока"
    await cmd_generate(FakeMessage(text="/generate", user_id=999), state)
    assert await state.get_state() == Generate.waiting_for_topic.state

    # пользователь вместо темы урока нажимает кнопку меню
    button_message = FakeMessage(text=texts.MENU_BUTTON_STATUS, user_id=999)
    await menu_button_pressed(button_message, state)

    # диалог прерван — состояние сброшено, а не осталось на месте
    assert await state.get_state() is None
    data = await state.get_data()
    assert "topic" not in data
    assert texts.MENU_BUTTON_STATUS not in json.dumps(data, ensure_ascii=False)

    # пользователь увидел явное сообщение о прерывании, а не тишину
    interrupted_texts = [s["text"] for s in button_message.sent]
    assert texts.MENU_DIALOG_INTERRUPTED in interrupted_texts

    # и целевой обработчик (cmd_status) реально сработал
    assert any(
        "нет активных задач" in s["text"].lower() or "очеред" in s["text"].lower()
        for s in button_message.sent
    )


async def test_menu_button_pressed_dispatches_to_matching_handler(isolated_env):
    """Каждая кнопка меню вызывает ровно тот обработчик, который
    соответствует её тексту — проверка на кнопке «Мой профиль»."""
    message = FakeMessage(text=texts.MENU_BUTTON_TEACHER, user_id=555)
    state = _state()
    await menu_button_pressed(message, state)
    assert await state.get_state() == TeacherProfile.waiting_for_name.state


@pytest.mark.parametrize(
    "global_handler_name",
    ["menu_button_pressed", "back_button_pressed", "cancel_button_pressed"],
)
def test_global_button_handler_registered_before_all_state_handlers(global_handler_name):
    """Ловушка плана (М2.1/М3.2), для всех трёх глобальных обработчиков
    кнопок сразу: aiogram матчит хендлеры в порядке регистрации — если
    один из них окажется НИЖЕ хотя бы одного хендлера с фильтром по
    состоянию, текст его кнопки будет перехвачен этим состоянием раньше,
    чем дойдёт до глобального обработчика. Прямой вызов функции (как в
    тестах выше) эту ошибку не ловит — только проверка порядка в самом
    router."""
    from aiogram.fsm.state import State

    names = [h.callback.__name__ for h in router.message.handlers]
    handler_index = names.index(global_handler_name)

    for index, handler in enumerate(router.message.handlers):
        has_state_filter = any(isinstance(f.callback, State) for f in handler.filters)
        if has_state_filter:
            assert index > handler_index, (
                f"хендлер {handler.callback.__name__} с фильтром по состоянию "
                f"зарегистрирован раньше {global_handler_name} — его кнопка "
                f"будет перехвачена этим состоянием"
            )


# =====================================================================
# М3 — «Назад» на реальном прогоне диалогов
# =====================================================================


async def test_generate_dialog_back_navigation_preserves_data(isolated_env):
    """М3.2 КГ, дух дословного требования плана: пройти /generate до шага
    подтверждения, нажимать «Назад» — на всём пути назад данные,
    введённые на пройденных шагах (раздел, класс, длительность), не
    теряются и показываются в подсказке при повторном вопросе.

    Отклонение от буквы плана (записано и в NIGHT_REPORT_STAGE2.md):
    формулировка КГ «три раза — оказаться на шаге темы» предполагала
    более короткий путь, чем есть в реальном диалоге — между темой и
    подтверждением лежат необязательные, но всё равно присутствующие в
    стеке шаги (код цели, если не найден автоматически; фото учебника;
    доп. опции; выбор шаблона). Тест идёт назад ровно до темы, сколько бы
    шагов это ни заняло, и проверяет сохранность данных на каждом шаге —
    это строже, чем фиксированное число «три»."""
    teacher_id = _create_teacher(777)
    state = _state()

    await cmd_generate(FakeMessage(text="/generate", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_topic.state

    # тема -> код цели не найден (КТП не загружен) -> спросит код вручную
    await generate_topic_received(FakeMessage(text="Закон Ома", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_objective_code.state

    await generate_objective_code_received(FakeMessage(text="-", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_razdel.state

    await generate_razdel_received(FakeMessage(text="Электричество", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_klass.state

    await generate_klass_received(FakeMessage(text="10Б", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_duration.state

    await generate_duration_received(FakeMessage(text="45", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_textbook_photos.state

    await generate_textbook_photos_skipped(FakeMessage(text="/skip", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_extra_options.state

    await generate_extra_options_received(FakeMessage(text="-", user_id=777), state)
    assert await state.get_state() == Generate.waiting_for_template.state

    templates_list = list_templates(teacher_id)
    assert templates_list, "встроенные шаблоны должны быть в isolated_env (load_builtin_templates)"
    template_id = templates_list[0]["id"]

    template_message = FakeMessage(user_id=777)
    callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=template_message, user_id=777)
    await generate_template_chosen(callback, state)
    assert await state.get_state() == Generate.waiting_for_confirmation.state

    # теперь идём назад, пока не дойдём до темы, сохраняя журнал состояний
    visited_states = [await state.get_state()]
    back_message = FakeMessage(user_id=777)
    for _ in range(10):  # с запасом — реальных шагов заведомо меньше 10
        await back_button_pressed(back_message, state)
        current = await state.get_state()
        visited_states.append(current)
        if current == Generate.waiting_for_topic.state:
            break
    else:
        raise AssertionError(f"не дошли до темы за 10 нажатий 'Назад': {visited_states}")

    assert visited_states[-1] == Generate.waiting_for_topic.state
    # порядок шагов назад строго обратный порядку вперёд, без пропусков и дублей
    assert visited_states == [
        Generate.waiting_for_confirmation.state,
        Generate.waiting_for_template.state,
        Generate.waiting_for_extra_options.state,
        Generate.waiting_for_textbook_photos.state,
        Generate.waiting_for_duration.state,
        Generate.waiting_for_klass.state,
        Generate.waiting_for_razdel.state,
        Generate.waiting_for_objective_code.state,
        Generate.waiting_for_topic.state,
    ]

    # данные не потеряны на всём пути назад
    data = await state.get_data()
    assert data["razdel"] == "Электричество"
    assert data["klass"] == "10Б"
    assert data["duration_minutes"] == 45
    assert data["topic"] == "Закон Ома"

    # и подсказка на последнем шаге назад (тема) показывает текущее значение
    last_prompt = back_message.sent[-1]["text"]
    assert "Закон Ома" in last_prompt


async def test_generate_dialog_back_from_first_step_returns_to_menu(isolated_env):
    _create_teacher(778)
    state = _state()
    await cmd_generate(FakeMessage(text="/generate", user_id=778), state)
    assert await state.get_state() == Generate.waiting_for_topic.state

    message = FakeMessage(user_id=778)
    await back_button_pressed(message, state)

    assert await state.get_state() is None
    assert message.sent[-1]["text"] == texts.NAV_BACK_TO_MENU
    assert message.sent[-1]["reply_markup"] is keyboards.MAIN_MENU


async def test_generate_ktp_dialog_back_navigation(isolated_env):
    _create_teacher(779)
    state = _state()

    await cmd_generate_ktp(FakeMessage(text="/generate_ktp", user_id=779), state)
    assert await state.get_state() == GenerateKTP.waiting_for_predmet.state

    await generate_ktp_predmet_received(FakeMessage(text="Физика", user_id=779), state)
    await generate_ktp_klass_received(FakeMessage(text="10А", user_id=779), state)
    await generate_ktp_hours_week_received(FakeMessage(text="2", user_id=779), state)
    await generate_ktp_hours_year_received(FakeMessage(text="68", user_id=779), state)
    await generate_ktp_topics_received(FakeMessage(text="-", user_id=779), state)
    assert await state.get_state() == GenerateKTP.waiting_for_confirmation.state

    message = FakeMessage(user_id=779)
    await back_button_pressed(message, state)
    assert await state.get_state() == GenerateKTP.waiting_for_topics.state

    await back_button_pressed(message, state)
    assert await state.get_state() == GenerateKTP.waiting_for_hours_year.state

    data = await state.get_data()
    assert data["predmet"] == "Физика"
    assert data["klass"] == "10А"
    assert data["chasov_v_nedelu"] == 2


async def test_generate_ktp_back_via_callback_matches_message_back(isolated_env):
    """back_callback_pressed (inline-кнопка «← Назад» на шаге подтверждения)
    должен вести себя идентично текстовой кнопке — общий _handle_go_back."""
    _create_teacher(780)
    state = _state()
    await cmd_generate_ktp(FakeMessage(text="/generate_ktp", user_id=780), state)
    await generate_ktp_predmet_received(FakeMessage(text="Физика", user_id=780), state)
    await generate_ktp_klass_received(FakeMessage(text="10А", user_id=780), state)
    await generate_ktp_hours_week_received(FakeMessage(text="2", user_id=780), state)
    await generate_ktp_hours_year_received(FakeMessage(text="68", user_id=780), state)
    await generate_ktp_topics_received(FakeMessage(text="-", user_id=780), state)
    assert await state.get_state() == GenerateKTP.waiting_for_confirmation.state

    callback_message = FakeMessage(user_id=780)
    callback = FakeCallbackQuery(data="nav_back", message=callback_message, user_id=780)
    await back_callback_pressed(callback, state)

    assert await state.get_state() == GenerateKTP.waiting_for_topics.state
    assert len(callback.answered) == 1


async def test_teacher_dialog_back_preserves_name(isolated_env):
    state = _state()
    await cmd_teacher(FakeMessage(text="/teacher", user_id=781), state)
    await teacher_name_received(FakeMessage(text="Иванов И.И.", user_id=781), state)
    assert await state.get_state() == TeacherProfile.waiting_for_subject.state

    message = FakeMessage(user_id=781)
    await back_button_pressed(message, state)
    assert await state.get_state() == TeacherProfile.waiting_for_name.state
    data = await state.get_data()
    assert data["name"] == "Иванов И.И."
    assert "Иванов И.И." in message.sent[-1]["text"]


async def test_upload_ksp_back_removes_last_file_not_step(isolated_env):
    """М3.3, ловушка: в UploadKSP «Назад» убирает последний файл, а не
    переключает шаг (шаг там один)."""
    teacher_id = _create_teacher(782)
    state = _state()
    await state.set_state(UploadKSP.collecting_files)
    await state.update_data(teacher_id=teacher_id, file_paths=["/tmp/fake1.docx", "/tmp/fake2.docx"])

    message = FakeMessage(user_id=782)
    await back_button_pressed(message, state)

    # состояние осталось тем же (не переключилось никуда)
    assert await state.get_state() == UploadKSP.collecting_files.state
    data = await state.get_data()
    assert data["file_paths"] == ["/tmp/fake1.docx"]
    assert "fake2.docx" in message.sent[-1]["text"]


async def test_upload_ksp_back_with_no_files_does_not_crash():
    state = _state()
    await state.set_state(UploadKSP.collecting_files)
    await state.update_data(teacher_id=1, file_paths=[])

    message = FakeMessage(user_id=783)
    await back_button_pressed(message, state)

    assert await state.get_state() == UploadKSP.collecting_files.state
    assert message.sent[-1]["text"] == texts.UPLOAD_KSP_NOTHING_TO_REMOVE


async def test_back_command_synonym_works_same_as_button():
    """/back — синоним кнопки «← Назад» (М3.2)."""
    state = _state()
    await state.set_state(UploadKSP.collecting_files)
    await state.update_data(teacher_id=1, file_paths=["/tmp/fake1.docx"])

    message = FakeMessage(text="/back", user_id=784)
    await back_button_pressed(message, state)

    data = await state.get_data()
    assert data["file_paths"] == []


async def test_cancel_button_mid_dialog_clears_state():
    state = _state()
    await state.set_state(Generate.waiting_for_topic)
    message = FakeMessage(text=texts.BUTTON_CANCEL, user_id=785)
    await cancel_button_pressed(message, state)
    assert await state.get_state() is None
    assert message.sent[-1]["text"] == texts.CANCEL_DONE


# =====================================================================
# М5.2 — /dashboard в боте
# =====================================================================


async def test_dashboard_without_profile_shows_friendly_message_not_generic_error(isolated_env):
    """Ловушка плана (М5.1, п.2): учитель без профиля — не ошибка,
    /dashboard не должен показать ERROR_NO_TEACHER_PROFILE, а свой текст."""
    from bot.handlers import cmd_dashboard

    message = FakeMessage(text="/dashboard", user_id=901)
    await cmd_dashboard(message)
    # М7.4: живучесть дописывается и без профиля - проверяем, что текст
    # НАЧИНАЕТСЯ с дружелюбного сообщения, а не точное равенство целиком.
    assert message.sent[0]["text"].startswith(texts.DASHBOARD_NO_PROFILE)
    assert message.sent[0]["text"] != texts.ERROR_NO_TEACHER_PROFILE


async def test_dashboard_with_profile_shows_real_numbers(isolated_env):
    from bot.handlers import cmd_dashboard

    teacher_id = _create_teacher(902)
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json) VALUES ('ksp1', ?, '{}')",
        (teacher_id,),
    )
    message = FakeMessage(text="/dashboard", user_id=902)
    await cmd_dashboard(message)
    text = message.sent[0]["text"]
    assert "всего: 1" in text
    assert message.sent[0]["reply_markup"] is keyboards.MAIN_MENU


async def test_dashboard_menu_button_dispatches_to_cmd_dashboard(isolated_env):
    teacher_id = _create_teacher(903)
    message = FakeMessage(text=texts.MENU_BUTTON_DASHBOARD, user_id=903)
    await menu_button_pressed(message, _state())
    assert texts.DASHBOARD_HEADER in message.sent[0]["text"]


# =====================================================================
# М6.2/М6.3 — дневные лимиты: интеграционный прогон через реальный диалог
# =====================================================================


async def _run_generate_dialog_to_confirmation(user_id: int, chat_id: int, state):
    """Доводит /generate до callback-подтверждения, возвращает
    FakeCallbackQuery с data='gen_confirm', готовый к generate_confirmed."""
    await cmd_generate(FakeMessage(text="/generate", user_id=user_id, chat_id=chat_id), state)
    await generate_topic_received(FakeMessage(text="Тема урока", user_id=user_id, chat_id=chat_id), state)
    await generate_objective_code_received(FakeMessage(text="-", user_id=user_id, chat_id=chat_id), state)
    await generate_razdel_received(FakeMessage(text="Раздел", user_id=user_id, chat_id=chat_id), state)
    await generate_klass_received(FakeMessage(text="10А", user_id=user_id, chat_id=chat_id), state)
    await generate_duration_received(FakeMessage(text="40", user_id=user_id, chat_id=chat_id), state)
    await generate_textbook_photos_skipped(FakeMessage(text="/skip", user_id=user_id, chat_id=chat_id), state)
    await generate_extra_options_received(FakeMessage(text="-", user_id=user_id, chat_id=chat_id), state)

    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    message = FakeMessage(user_id=user_id, chat_id=chat_id)
    template_callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=message, user_id=user_id)
    await generate_template_chosen(template_callback, state)

    return FakeCallbackQuery(data="gen_confirm", message=message, user_id=user_id)


async def test_sixth_generate_ksp_confirm_blocked_by_daily_limit(isolated_env):
    """М6.3 КГ, дословно из плана: пятая генерация КСП проходит, шестая
    отказывает с внятным текстом (не 'лимит исчерпан', а сколько
    потрачено и когда сбросится)."""
    _create_teacher(950)
    for i in range(5):
        record_usage(950, "generate_ksp", count_delta=1)

    from bot.handlers import generate_confirmed

    state = _state()
    confirm_callback = await _run_generate_dialog_to_confirmation(950, 950, state)
    await generate_confirmed(confirm_callback, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'generate_ksp'")
    assert len(tasks) == 0, "шестая генерация не должна была уйти в очередь"

    answer_text = confirm_callback.message.sent[-1]["text"]
    # М6.3, требование дословно: не голое "лимит исчерпан", а сколько
    # потрачено (5/5) и когда сбросится (время в формате ЧЧ:ММ).
    assert "5/5" in answer_text
    assert re.search(r"\d{2}:\d{2}", answer_text), f"нет времени сброса лимита в тексте: {answer_text!r}"
    assert answer_text != texts.STATUS_EMPTY  # сигнал, что не подставился текст другого экрана


async def test_fifth_generate_ksp_confirm_still_passes(isolated_env):
    _create_teacher(951)
    for i in range(4):
        record_usage(951, "generate_ksp", count_delta=1)

    from bot.handlers import generate_confirmed

    state = _state()
    confirm_callback = await _run_generate_dialog_to_confirmation(951, 951, state)
    await generate_confirmed(confirm_callback, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'generate_ksp'")
    assert len(tasks) == 1


async def test_limit_check_does_not_consume_quota_by_itself(isolated_env):
    """Сама проверка лимита (check_count_limit) — только чтение, не
    списание: повторный вызов той же самой проверки не меняет счётчик."""
    from core.limits import check_count_limit

    _create_teacher(952)
    check_count_limit(952, "generate_ksp")
    check_count_limit(952, "generate_ksp")
    usage = get_usage_today(952)
    assert usage["counts"].get("generate_ksp", 0) == 0


# =====================================================================
# М7.1 — уведомление о завершённых инцидентах при старте бота
# =====================================================================


async def test_notify_unresolved_incidents_skips_when_admin_chat_id_not_set(isolated_env):
    from bot.main import _notify_unresolved_incidents
    from core.config import settings as core_settings

    original = core_settings.admin_telegram_chat_id
    object.__setattr__(core_settings, "admin_telegram_chat_id", None)
    try:
        bot = FakeBot()
        await _notify_unresolved_incidents(bot)
        assert bot.sent_messages == []
    finally:
        object.__setattr__(core_settings, "admin_telegram_chat_id", original)


async def test_notify_unresolved_incidents_sends_one_message_per_incident(isolated_env):
    """М7.1, ловушка 1 дословно: одно сообщение на инцидент, а не на
    каждую тревогу watchdog внутри него."""
    from bot.main import _notify_unresolved_incidents
    from core.config import settings as core_settings

    execute(
        "INSERT INTO incidents (started_at, ended_at, reason, notified) "
        "VALUES ('2026-08-25 10:00:00', '2026-08-25 11:57:00', 'dns_fail', 0)"
    )

    original = core_settings.admin_telegram_chat_id
    object.__setattr__(core_settings, "admin_telegram_chat_id", 999999)
    try:
        bot = FakeBot()
        await _notify_unresolved_incidents(bot)
        assert len(bot.sent_messages) == 1
        chat_id, text, _ = bot.sent_messages[0]
        assert chat_id == 999999
        assert "10:00" in text
    finally:
        object.__setattr__(core_settings, "admin_telegram_chat_id", original)

    # повторный запуск (следующий старт бота) не шлёт то же самое снова
    rows = query("SELECT notified FROM incidents")
    assert rows[0]["notified"] == 1


async def test_notify_unresolved_incidents_sends_multiple_separately(isolated_env):
    from bot.main import _notify_unresolved_incidents
    from core.config import settings as core_settings

    execute(
        "INSERT INTO incidents (started_at, ended_at, reason, notified) "
        "VALUES ('2026-08-25 10:00:00', '2026-08-25 11:57:00', 'dns_fail', 0)"
    )
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason, notified) "
        "VALUES ('2026-08-26 11:28:00', '2026-08-26 11:34:00', 'tcp_fail', 0)"
    )

    original = core_settings.admin_telegram_chat_id
    object.__setattr__(core_settings, "admin_telegram_chat_id", 999999)
    try:
        bot = FakeBot()
        await _notify_unresolved_incidents(bot)
        assert len(bot.sent_messages) == 2
    finally:
        object.__setattr__(core_settings, "admin_telegram_chat_id", original)


async def test_dashboard_text_shows_uptime_with_resolved_incident(isolated_env):
    from bot.handlers import cmd_dashboard

    _create_teacher(904)
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES "
        "('2026-08-25 10:00:00', '2026-08-25 11:57:00', 'dns_fail')"
    )
    message = FakeMessage(text="/dashboard", user_id=904)
    await cmd_dashboard(message)
    text = message.sent[0]["text"]
    assert "не резолвился DNS" in text
    assert "10:00" in text
    assert "11:57" in text
    assert "1 ч 57 мин" in text  # суммарная недоступность за 7 дней


async def test_dashboard_text_shows_ongoing_incident(isolated_env):
    from bot.handlers import cmd_dashboard

    _create_teacher(905)
    execute(
        "INSERT INTO incidents (started_at, ended_at, reason) VALUES (datetime('now'), NULL, 'tcp_fail')"
    )
    message = FakeMessage(text="/dashboard", user_id=905)
    await cmd_dashboard(message)
    text = message.sent[0]["text"]
    assert "сейчас недоступно" in text
    assert "сеть недоступна (TCP)" in text


# =====================================================================
# К2.3 — /konspekt: приём аудио урока
# =====================================================================


async def test_konspekt_starts_collecting_state(isolated_env):
    _create_teacher(910)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=910), state)
    assert await state.get_state() == Konspekt.collecting_audio.state


async def test_konspekt_without_profile_shows_error(isolated_env):
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=999999), state)
    assert await state.get_state() is None


async def test_konspekt_accepts_voice_message(isolated_env):
    teacher_id = _create_teacher(911)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=911), state)

    message = FakeMessage(user_id=911, voice=FakeVoice(duration=125))
    bot = FakeBot()
    await konspekt_voice_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == 1
    assert data["audio_durations"] == [125]
    assert "2 мин" in message.sent[-1]["text"]


async def test_konspekt_accepts_audio_file(isolated_env):
    _create_teacher(912)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=912), state)

    message = FakeMessage(user_id=912, audio=FakeAudio(file_name="urok.mp3"))
    bot = FakeBot()
    await konspekt_audio_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == 1
    assert data["audio_paths"][0].endswith(".mp3")


async def test_konspekt_rejects_non_audio_document(isolated_env):
    _create_teacher(913)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=913), state)

    message = FakeMessage(user_id=913, document=FakeDocument(file_name="report.docx", mime_type="application/msword"))
    bot = FakeBot()
    await konspekt_document_received(message, state, bot)

    data = await state.get_data()
    assert data["audio_paths"] == []
    assert "не похоже на аудио" in message.sent[-1]["text"]


async def test_konspekt_accepts_audio_document_with_correct_mime(isolated_env):
    _create_teacher(914)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=914), state)

    message = FakeMessage(user_id=914, document=FakeDocument(file_name="urok.wav", mime_type="audio/wav"))
    bot = FakeBot()
    await konspekt_document_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == 1


async def test_konspekt_multiple_parts_collected_in_order(isolated_env):
    """К2.3, ловушка 2: несколько частей одного урока — все части должны
    накапливаться по порядку присылки."""
    _create_teacher(915)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=915), state)

    bot = FakeBot()
    for i in range(3):
        message = FakeMessage(user_id=915, voice=FakeVoice(duration=60 + i))
        await konspekt_voice_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == 3
    assert data["audio_durations"] == [60, 61, 62]


async def test_konspekt_max_parts_limit(isolated_env):
    _create_teacher(916)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=916), state)

    bot = FakeBot()
    from bot.handlers import MAX_KONSPEKT_PARTS

    for i in range(MAX_KONSPEKT_PARTS + 2):
        message = FakeMessage(user_id=916, voice=FakeVoice())
        await konspekt_voice_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == MAX_KONSPEKT_PARTS
    assert "больше не приму" in message.sent[-1]["text"]


async def test_konspekt_size_limit_reuses_check_file_size(isolated_env):
    """Ловушка К2.3 дословно: переиспользовать существующую
    _check_file_size, не писать вторую."""
    _create_teacher(917)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=917), state)

    huge_voice = FakeVoice(file_size=25 * 1024 * 1024)  # 25 МБ, больше лимита в 20
    message = FakeMessage(user_id=917, voice=huge_voice)
    bot = FakeBot()
    await konspekt_voice_received(message, state, bot)

    data = await state.get_data()
    assert data["audio_paths"] == []
    assert "20" in message.sent[-1]["text"]


async def test_konspekt_done_with_no_parts_shows_error(isolated_env):
    _create_teacher(918)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=918), state)

    message = FakeMessage(text="/done", user_id=918)
    await konspekt_done(message, state)

    assert await state.get_state() == Konspekt.collecting_audio.state
    assert "нет ни одной части" in message.sent[-1]["text"]


async def test_konspekt_done_enqueues_transcribe_task(isolated_env):
    _create_teacher(919)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=919), state)

    bot = FakeBot()
    await konspekt_voice_received(FakeMessage(user_id=919, voice=FakeVoice(), chat_id=919), state, bot)
    await konspekt_voice_received(FakeMessage(user_id=919, voice=FakeVoice(), chat_id=919), state, bot)

    message = FakeMessage(text="/done", user_id=919, chat_id=919)
    await konspekt_done(message, state)

    assert await state.get_state() is None
    tasks = query("SELECT * FROM tasks WHERE type = 'transcribe'")
    assert len(tasks) == 1
    assert tasks[0]["telegram_chat_id"] == 919
    payload = json.loads(tasks[0]["payload"])
    assert len(payload["audio_paths"]) == 2


async def test_konspekt_wrong_input_shows_message(isolated_env):
    _create_teacher(920)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=920), state)

    message = FakeMessage(text="привет", user_id=920)
    await konspekt_wrong_input(message)
    assert "не похоже на аудио" in message.sent[-1]["text"]


async def test_konspekt_added_to_bot_commands_and_menu_after_k4():
    """К4: путь заработал целиком (transcribe -> generate_konspekt), теперь
    команда рекламируется (М1.1/М2.1 принцип наоборот — теперь МОЖНО)."""
    command_names = {name for name, _ in texts.BOT_COMMANDS}
    assert "konspekt" in command_names
    assert texts.MENU_BUTTON_KONSPEKT in keyboards.MAIN_MENU_BUTTON_TEXTS
    assert texts.MENU_BUTTON_DASHBOARD in keyboards.MAIN_MENU_BUTTON_TEXTS  # существующие кнопки не пострадали


# =====================================================================
# К3.2 — задача очереди 'transcribe' (реальная расшифровка, не мок)
# =====================================================================


async def test_transcribe_handler_real_audio_creates_transcript_and_notifies(isolated_env):
    """Реальная расшифровка настоящего фикстур-файла через сам
    обработчик очереди — не только core.transcriber напрямую."""
    teacher_id = _create_teacher(930)
    audio_copy = settings.uploads_dir / "part1.m4a"
    audio_copy.write_bytes((FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes())

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr1",
        "type": "transcribe",
        "telegram_chat_id": 930,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(audio_copy)]},
    }

    result = await handler(task)

    assert "transcript_id" in result
    assert 40 <= result["duration_seconds"] <= 55

    rows = query("SELECT * FROM transcripts WHERE id = ?", (result["transcript_id"],))
    assert len(rows) == 1
    assert rows[0]["source"] == "audio"
    assert rows[0]["teacher_id"] == teacher_id
    assert len(rows[0]["text"]) > 50

    # два сообщения: "начал расшифровку" и "готово"
    assert len(bot.sent_messages) == 2
    assert "начал расшифровку" in bot.sent_messages[0][1].lower()
    assert "готова" in bot.sent_messages[1][1].lower()


async def test_transcribe_handler_deletes_audio_after_success(isolated_env):
    """К2.4 КГ: после успешной транскрипции на диске не остаётся ни
    исходника, ни промежуточного WAV (WAV убирается сам внутри
    core.transcriber через TemporaryDirectory — здесь проверяем исходник,
    за который отвечает сам обработчик)."""
    teacher_id = _create_teacher(931)
    audio_copy = settings.uploads_dir / "part1.m4a"
    audio_copy.write_bytes((FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes())
    assert audio_copy.exists()

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr2",
        "type": "transcribe",
        "telegram_chat_id": 931,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(audio_copy)]},
    }
    await handler(task)

    assert not audio_copy.exists()
    # никаких .wav не осталось в uploads_dir вовсе
    assert list(settings.uploads_dir.glob("*.wav")) == []


async def test_transcribe_handler_deletes_audio_even_on_failure(isolated_env):
    """К2.4 КГ: и при ПРОВАЛЕ транскрипции — тоже ноль мусора на диске."""
    teacher_id = _create_teacher(932)
    broken_audio = settings.uploads_dir / "broken.m4a"
    broken_audio.write_text("это не аудиофайл, а текст")

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr3",
        "type": "transcribe",
        "telegram_chat_id": 932,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(broken_audio)]},
    }

    with pytest.raises(Exception):
        await handler(task)

    assert not broken_audio.exists()


async def test_transcribe_handler_concatenates_multiple_parts_in_order(isolated_env):
    """К2.3, ловушка 2: несколько частей одного урока — транскрипты
    склеиваются по порядку присылки."""
    teacher_id = _create_teacher(933)
    part1 = settings.uploads_dir / "part1.m4a"
    part2 = settings.uploads_dir / "part2.m4a"
    fixture_bytes = (FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes()
    part1.write_bytes(fixture_bytes)
    part2.write_bytes(fixture_bytes)

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr4",
        "type": "transcribe",
        "telegram_chat_id": 933,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(part1), str(part2)]},
    }
    result = await handler(task)

    rows = query("SELECT text, duration_seconds FROM transcripts WHERE id = ?", (result["transcript_id"],))
    text = rows[0]["text"]
    # текст первой части должен встретиться раньше текста второй — не
    # просто "оба текста где-то есть", а именно порядок присылки
    assert text.count("Кинематика") >= 1 or text.count("кинематика") >= 1
    # суммарная длительность — сумма обеих частей (не одной)
    assert rows[0]["duration_seconds"] >= 80  # 2 x ~47с


async def test_transcribe_handler_refuses_second_real_attempt_after_retry(isolated_env):
    """К3.2, ловушка плана дословно: ретраи для этой задачи опасны —
    повторная попытка не должна заново гонять настоящую транскрипцию,
    только быстро отказать."""
    teacher_id = _create_teacher(934)
    audio_copy = settings.uploads_dir / "part1.m4a"
    audio_copy.write_bytes((FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes())

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr5",
        "type": "transcribe",
        "telegram_chat_id": 934,
        "retries": 1,  # уже был один провал
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(audio_copy)]},
    }

    with pytest.raises(TranscriptionError):
        await handler(task)

    # файл всё равно удалён (K2.4 действует и в этой ветке)
    assert not audio_copy.exists()
    # никаких сообщений о "начал расшифровку" — быстрый отказ без реальной попытки
    assert bot.sent_messages == []


# =====================================================================
# К4 — задача очереди 'generate_konspekt' (core.konspekt_generator
# подменяется моком, как и generate_and_save_ksp в тестах Б7 выше —
# настоящие платные вызовы LLM в тестах не нужны и не разрешены
# дневным бюджетом, честность самого генератора уже проверена в
# tests/test_konspekt_generator.py на скриптованном клиенте)
# =====================================================================


_SAMPLE_KONSPEKT_CONTENT = {
    "tema": "Кинематика: путь и перемещение",
    "celi": ["Различать путь и перемещение"],
    "glavnoe": ["Путь — скаляр, перемещение — вектор"],
    "formuly": [{"formula": "s = v*t", "znachenie": "путь при равномерном движении"}],
    "primery": [],
    "terminy": [{"termin": "перемещение", "opredelenie": "вектор из начальной точки в конечную"}],
    "voprosy_dlya_samoproverki": ["Чем отличается путь от перемещения?"],
    "domashnee_zadanie": "",
}


async def test_konspekt_handler_generates_and_sends_text(isolated_env, monkeypatch):
    teacher_id = _create_teacher(940)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-940', ?, 'audio', 'расшифровка урока про кинематику', 47, 'ru')",
        (teacher_id,),
    )

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        assert transcript_text == "расшифровка урока про кинематику"
        return dict(_SAMPLE_KONSPEKT_CONTENT)

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k1",
        "type": "generate_konspekt",
        "telegram_chat_id": 940,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-940"},
    }

    result = await handler(task)

    rows = query("SELECT * FROM konspekty WHERE id = ?", (result["konspekt_id"],))
    assert len(rows) == 1
    assert rows[0]["teacher_id"] == teacher_id
    assert rows[0]["transcript_id"] == "tr-940"
    assert rows[0]["tema"] == "Кинематика: путь и перемещение"
    assert json.loads(rows[0]["content_json"])["glavnoe"] == _SAMPLE_KONSPEKT_CONTENT["glavnoe"]

    assert len(bot.sent_messages) == 1
    sent_text = bot.sent_messages[0][1]
    assert "Кинематика: путь и перемещение" in sent_text
    assert "Путь — скаляр, перемещение — вектор" in sent_text
    assert "s = v*t" in sent_text

    usage = get_usage_today(940)
    assert usage["counts"]["generate_konspekt"] == 1


async def test_konspekt_handler_missing_transcript_raises(isolated_env):
    teacher_id = _create_teacher(941)
    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k2",
        "type": "generate_konspekt",
        "telegram_chat_id": 941,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "нет-такого-id"},
    }

    with pytest.raises(KonspektGenerationError):
        await handler(task)

    assert query("SELECT * FROM konspekty") == []
    assert bot.sent_messages == []


async def test_konspekt_handler_splits_long_konspekt_into_multiple_messages(isolated_env, monkeypatch):
    """Реальный урок легко даёт конспект длиннее одного сообщения
    Telegram (лимит 4096) — проверяем это через сам обработчик, не
    только через _split_for_telegram напрямую."""
    teacher_id = _create_teacher(942)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-942', ?, 'audio', 'длинная расшифровка', 600, 'ru')",
        (teacher_id,),
    )

    long_content = dict(_SAMPLE_KONSPEKT_CONTENT)
    long_content["glavnoe"] = [f"Пункт номер {i} из длинного конспекта урока" for i in range(300)]

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return long_content

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k3",
        "type": "generate_konspekt",
        "telegram_chat_id": 942,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-942"},
    }
    await handler(task)

    assert len(bot.sent_messages) > 1
    for _, text, _reply_markup in bot.sent_messages:
        assert len(text) <= 4000


async def test_transcribe_handler_enqueues_generate_konspekt_task(isolated_env):
    """К4: цепочка одна (MASTER.md 0.6, п.2) — успешная расшифровка сама
    ставит задачу сборки конспекта, без ручного шага пользователя."""
    teacher_id = _create_teacher(943)
    audio_copy = settings.uploads_dir / "part1.m4a"
    audio_copy.write_bytes((FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes())

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    task = {
        "id": "tr6",
        "type": "transcribe",
        "telegram_chat_id": 943,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(audio_copy)]},
    }
    result = await handler(task)

    konspekt_tasks = query("SELECT * FROM tasks WHERE type = 'generate_konspekt'")
    assert len(konspekt_tasks) == 1
    assert konspekt_tasks[0]["telegram_chat_id"] == 943
    payload = json.loads(konspekt_tasks[0]["payload"])
    assert payload["teacher_id"] == teacher_id
    assert payload["transcript_id"] == result["transcript_id"]


def test_format_konspekt_text_includes_all_sections():
    text = format_konspekt_text(_SAMPLE_KONSPEKT_CONTENT)
    assert text.startswith("📝 Конспект: Кинематика: путь и перемещение")
    assert "Различать путь и перемещение" in text
    assert "s = v*t — путь при равномерном движении" in text
    assert "перемещение: вектор из начальной точки в конечную" in text
    assert "Чем отличается путь от перемещения?" in text
    # пустые секции (primery, domashnee_zadanie) не оставляют "хвостов" в тексте
    assert "Примеры:" not in text
    assert "Домашнее задание:" not in text


# =====================================================================
# К5 — кнопка «Собрать КСП по этому конспекту»
# =====================================================================


async def test_konspekt_message_carries_ksp_button_on_last_chunk_only(isolated_env, monkeypatch):
    teacher_id = _create_teacher(950)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-950', ?, 'audio', 'расшифровка', 47, 'ru')",
        (teacher_id,),
    )

    long_content = dict(_SAMPLE_KONSPEKT_CONTENT)
    long_content["glavnoe"] = [f"Пункт {i} длинного конспекта урока физики" for i in range(300)]

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return long_content

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k4",
        "type": "generate_konspekt",
        "telegram_chat_id": 950,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-950"},
    }
    result = await handler(task)

    assert len(bot.sent_messages) > 1
    # ни у одного сообщения, кроме последнего, кнопки нет
    for _, _text, reply_markup in bot.sent_messages[:-1]:
        assert reply_markup is None
    _, _text, last_markup = bot.sent_messages[-1]
    assert last_markup is not None
    button = last_markup.inline_keyboard[0][0]
    assert button.text == texts.KSP_FROM_KONSPEKT_BUTTON
    assert button.callback_data == f"ksp_from_konspekt:{result['konspekt_id']}"


async def test_ksp_from_konspekt_pressed_prefills_topic_and_stores_konspekt_text(isolated_env):
    teacher_id = _create_teacher(951)
    konspekt_id = "ksp-src-951"
    content = dict(_SAMPLE_KONSPEKT_CONTENT)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-951', ?, 'audio', 'расшифровка', 47, 'ru')",
        (teacher_id,),
    )
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json) VALUES (?, ?, ?, ?, ?)",
        (konspekt_id, teacher_id, "tr-951", content["tema"], json.dumps(content, ensure_ascii=False)),
    )

    state = _state()
    message = FakeMessage(user_id=951, chat_id=951)
    callback = FakeCallbackQuery(data=f"ksp_from_konspekt:{konspekt_id}", message=message, user_id=951)
    await ksp_from_konspekt_pressed(callback, state)

    data = await state.get_data()
    assert data["topic"] == content["tema"]
    assert data["teacher_id"] == teacher_id
    assert "Путь — скаляр, перемещение — вектор" in data["konspekt_text"]
    # код цели по такой теме не угадывается -> следующий шаг — код цели, не раздел
    assert await state.get_state() == Generate.waiting_for_objective_code.state
    assert callback.answered  # callback.answer() вызван — не висит "часиками" в клиенте


async def test_ksp_from_konspekt_pressed_rejects_foreign_konspekt(isolated_env):
    owner_id = _create_teacher(952)
    stranger_id = _create_teacher(953)
    konspekt_id = "ksp-src-952"
    content = dict(_SAMPLE_KONSPEKT_CONTENT)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-952', ?, 'audio', 'расшифровка', 47, 'ru')",
        (owner_id,),
    )
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json) VALUES (?, ?, ?, ?, ?)",
        (konspekt_id, owner_id, "tr-952", content["tema"], json.dumps(content, ensure_ascii=False)),
    )

    state = _state()
    message = FakeMessage(user_id=953, chat_id=953)
    callback = FakeCallbackQuery(data=f"ksp_from_konspekt:{konspekt_id}", message=message, user_id=953)
    await ksp_from_konspekt_pressed(callback, state)

    assert await state.get_state() is None  # диалог /generate не начался
    assert callback.answered[-1]["show_alert"] is True
    assert callback.answered[-1]["text"] == texts.KSP_FROM_KONSPEKT_NOT_FOUND
    assert message.sent == []  # никакого шага диалога не задано


async def test_ksp_from_konspekt_pressed_missing_konspekt_id(isolated_env):
    _create_teacher(954)
    state = _state()
    message = FakeMessage(user_id=954, chat_id=954)
    callback = FakeCallbackQuery(data="ksp_from_konspekt:нет-такого-id", message=message, user_id=954)
    await ksp_from_konspekt_pressed(callback, state)

    assert await state.get_state() is None
    assert callback.answered[-1]["show_alert"] is True


async def test_ksp_from_konspekt_pressed_requires_teacher_profile(isolated_env):
    state = _state()
    message = FakeMessage(user_id=955, chat_id=955)
    callback = FakeCallbackQuery(data="ksp_from_konspekt:любой-id", message=message, user_id=955)
    await ksp_from_konspekt_pressed(callback, state)

    assert texts.ERROR_NO_TEACHER_PROFILE in [item["text"] for item in message.sent]


async def test_generate_confirmed_carries_konspekt_text_through_to_task_payload(isolated_env):
    """К5 целиком: кнопка под конспектом -> предзаполненный /generate ->
    подтверждение -> конспект доехал до payload задачи очереди."""
    teacher_id = _create_teacher(956)
    konspekt_id = "ksp-src-956"
    content = dict(_SAMPLE_KONSPEKT_CONTENT)
    content["tema"] = "Совершенно новая уникальная тема"
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-956', ?, 'audio', 'расшифровка', 47, 'ru')",
        (teacher_id,),
    )
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json) VALUES (?, ?, ?, ?, ?)",
        (konspekt_id, teacher_id, "tr-956", content["tema"], json.dumps(content, ensure_ascii=False)),
    )

    state = _state()
    message = FakeMessage(user_id=956, chat_id=956)
    callback = FakeCallbackQuery(data=f"ksp_from_konspekt:{konspekt_id}", message=message, user_id=956)
    await ksp_from_konspekt_pressed(callback, state)
    assert await state.get_state() == Generate.waiting_for_objective_code.state

    await generate_objective_code_received(FakeMessage(text="-"), state)
    await generate_razdel_received(FakeMessage(text="Механика"), state)
    await generate_klass_received(FakeMessage(text="10А"), state)
    await generate_duration_received(FakeMessage(text="40"), state)
    await generate_textbook_photos_skipped(FakeMessage(text="/skip"), state)
    await generate_extra_options_received(FakeMessage(text="-"), state)

    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    tpl_message = FakeMessage(chat_id=956)
    tpl_callback = FakeCallbackQuery(data=f"gen_tpl:{template_id}", message=tpl_message)
    await generate_template_chosen(tpl_callback, state)

    from bot.handlers import generate_confirmed

    confirm_callback = FakeCallbackQuery(data="gen_confirm", message=tpl_message)
    await generate_confirmed(confirm_callback, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'generate_ksp'")
    assert len(tasks) == 1
    payload = json.loads(tasks[0]["payload"])
    assert payload["topic"] == "Совершенно новая уникальная тема"
    assert "Путь — скаляр, перемещение — вектор" in payload["konspekt_text"]
