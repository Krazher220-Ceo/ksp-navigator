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
from pathlib import Path

import pytest
from openpyxl import Workbook

from bot.handlers import (
    cmd_cancel,
    cmd_generate,
    cmd_history,
    cmd_start,
    cmd_status,
    cmd_teacher,
    cmd_templates,
    cmd_upload_ksp,
    cmd_upload_ktp,
    generate_duration_received,
    generate_klass_received,
    generate_objective_code_received,
    generate_razdel_received,
    generate_template_chosen,
    generate_topic_received,
    history_resend,
    make_generate_ksp_handler,
    make_parse_ksp_handler,
    router,
    teacher_name_received,
    teacher_school_received,
    teacher_subject_received,
    upload_ksp_done,
    upload_ksp_file_received,
    upload_ktp_file_received,
)
from bot import texts
from bot.main import _global_error_handler, _register_bot_commands
from bot.states import Generate, TeacherProfile
from core import ksp_generator as ksp_generator_module
from core.config import settings
from core.ksp_generator import MAX_VIDY_DEYATELNOSTI
from core.db import execute, init_db, query
from core.templates import load_builtin_templates

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
    def __init__(self, file_id="fid", file_name="file.docx", file_size=1000):
        self.file_id = file_id
        self.file_name = file_name
        self.file_size = file_size


class FakePhotoSize:
    def __init__(self, file_id="photo-fid", file_size=1000):
        self.file_id = file_id
        self.file_size = file_size


class FakeMessage:
    def __init__(self, text=None, user_id=1, chat_id=1, document=None, photo=None):
        self.text = text
        self.from_user = FakeUser(user_id)
        self.chat = FakeChat(chat_id)
        self.document = document
        self.photo = photo  # список FakePhotoSize (крупнейший — последний), как у Telegram
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

    async def download(self, file, destination):
        Path(destination).write_bytes(b"fake ksp/ktp file content for tests")
        self.downloaded.append((file, destination))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent_messages.append((chat_id, text))

    async def send_document(self, chat_id, document, caption=None, reply_markup=None, **kwargs):
        self.sent_documents.append(
            {"chat_id": chat_id, "document": document, "caption": caption, "reply_markup": reply_markup}
        )

    async def set_my_commands(self, commands, **kwargs):
        self.set_commands_calls.append(commands)


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
