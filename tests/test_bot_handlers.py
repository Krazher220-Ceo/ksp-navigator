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
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook

from bot.handlers import (
    MAX_FILE_SIZE_BYTES,
    MAX_KONSPEKT_PARTS,
    back_button_pressed,
    back_callback_pressed,
    _consent_gate,
    _student_gate,
    cancel_button_pressed,
    class_create_confirmed,
    class_create_started,
    class_delete_cancelled,
    class_delete_confirmed,
    class_delete_requested,
    class_list_requested,
    class_name_received,
    class_regen_requested,
    class_subject_received,
    class_view_requested,
    cmd_class,
    cmd_join,
    cmd_konspekt,
    cmd_menu,
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
    cmd_delete_my_data,
    cmd_generate,
    cmd_generate_ktp,
    cmd_history,
    cmd_start,
    cmd_status,
    cmd_teacher,
    cmd_templates,
    cmd_upload_ksp,
    cmd_upload_ktp,
    consent_accepted,
    consent_declined,
    delete_my_data_cancelled,
    delete_my_data_confirmed,
    has_given_consent,
    record_consent,
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
    konspekt_remove_last_part_pressed,
    make_generate_ksp_handler,
    make_parse_ksp_handler,
    cmd_sverka,
    make_sverka_handler,
    menu_button_pressed,
    role_student_chosen,
    role_teacher_chosen,
    router,
    send_konspekt_class_chosen,
    send_konspekt_requested,
    send_konspekt_student_chosen,
    sverka_class_chosen,
    sverka_lesson_chosen,
    sverka_photo_received,
    sverka_wrong_input,
    student_consent_accepted,
    student_consent_declined,
    student_join_cancelled,
    student_join_code_received,
    student_join_confirmed,
    teacher_name_received,
    teacher_school_received,
    teacher_subject_received,
    templates_web_app_choice,
    upload_ksp_done,
    upload_ksp_file_received,
    upload_ksp_remove_last_file_pressed,
    upload_ktp_file_received,
)
from bot import keyboards, texts
from core.konspekt_compare import KonspektCompareError
from bot.main import _global_error_handler, _register_bot_commands, _register_chat_menu_button
from bot.states import Generate, GenerateKTP, Konspekt, SverkaCheck, TeacherProfile, UploadKSP, UploadTemplate
from core import ksp_generator as ksp_generator_module
from core.config import settings
from core.ksp_generator import MAX_VIDY_DEYATELNOSTI
from core.db import SupabaseDatabaseError, execute, init_db, query
from core.konspekt_generator import CELI_NOT_STATED_NOTE, KonspektGenerationError
from core.limits import get_usage_today, record_usage
from core.pdf_export import PdfExportError
from core.queue import claim_next, enqueue, recover_stuck_tasks
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
        "db_backend": settings.db_backend,
        "uploads_dir": settings.uploads_dir,
        "generated_dir": settings.generated_dir,
    }
    # bot/handlers.py вызывает core.db.query/execute без явного db_path,
    # поэтому connect() смотрит на settings.db_backend — подмены одного
    # db_path недостаточно, при db_backend="supabase" запросы всё равно
    # уходят в боевую сеть (блок Н0 PLAN.md).
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "uploads_dir", uploads_dir)
    object.__setattr__(settings, "generated_dir", generated_dir)
    # Э3: has_given_consent кэширует положительный ответ в module-level
    # set внутри bot/handlers.py — без сброса между тестами telegram_user_id,
    # once закэшированный в одном тесте (своя, временная SQLite), молча
    # считался бы согласившимся и в следующем тесте с чистой базой того
    # же tmp_path, но другим содержимым. Чистим и до, и после — на случай
    # теста, который проверяет саму утечку кэша (падение при откате
    # правки — часть КГ этого блока).
    from bot.handlers import _consent_given_cache

    _consent_given_cache.clear()
    try:
        yield {"db_path": db_path, "uploads_dir": uploads_dir, "generated_dir": generated_dir}
    finally:
        for key, value in originals.items():
            object.__setattr__(settings, key, value)
        _consent_given_cache.clear()


def test_isolated_env_uses_sqlite_not_supabase(isolated_env):
    """КГ блока Н0: без этой подмены запросы уходят в боевой Supabase."""
    from core.db import SupabaseConnection, connect

    conn = connect()
    try:
        assert not isinstance(conn, SupabaseConnection)
    finally:
        conn.close()


@pytest.fixture
def fake_xai_transcriber(monkeypatch):
    """Изолирует тесты очереди от платного внешнего STT API."""
    async def fake_transcribe(_path):
        return {
            "text": "Кинематика: перемещение, скорость, ускорение и законы движения тела.",
            "duration_seconds": 47,
            "language": "ru",
        }

    monkeypatch.setattr("bot.handlers.transcribe", fake_transcribe)


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
        self.message_id = 1

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
        self.deleted_messages: list[tuple[int, int]] = []

    async def delete_message(self, chat_id, message_id):
        self.deleted_messages.append((chat_id, message_id))

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


async def _start_konspekt(state, user_id: int, mode_button: str = texts.KONSPEKT_MODE_STUDENT_BUTTON):
    """Проходит обязательный выбор режима и оставляет FSM на сборе аудио."""
    from bot.handlers import konspekt_mode_chosen

    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=user_id), state)
    mode_message = FakeMessage(text=mode_button, user_id=user_id)
    await konspekt_mode_chosen(mode_message, state)
    return mode_message


# =====================================================================
# /start (Б8.1 КГ)
# =====================================================================


async def test_start_shows_role_choice_for_completely_unknown_person(isolated_env):
    """У3: первый /start незнакомого человека — выбор роли, не экран
    согласия и не приветствие напрямую. До блока У3 бот молча считал
    всех педагогами; этот тест заменяет старый
    test_start_shows_consent_screen_when_not_yet_given, который проверял
    именно то поведение, которое У3 намеренно меняет."""
    message = FakeMessage(text="/start", user_id=700)
    await cmd_start(message, _state())
    assert len(message.sent) == 1
    assert message.sent[0]["text"] == texts.ROLE_CHOICE_TEXT
    assert message.sent[0]["reply_markup"] is not None


async def test_start_shows_consent_screen_after_choosing_teacher_role(isolated_env):
    """Выбор "Я — педагог" на полностью новом аккаунте ведёт туда же, куда
    раньше вёл голый /start — на экран согласия (Ю3), не на приветствие."""
    callback = FakeCallbackQuery(data="role_teacher", message=FakeMessage(user_id=700), user_id=700)
    await role_teacher_chosen(callback)
    assert callback.message.sent[-1]["text"] == texts.CONSENT_TEXT
    assert not has_given_consent(700)


async def test_start_answers_with_greeting_after_consent_given(isolated_env):
    record_consent(700)
    message = FakeMessage(text="/start", user_id=700)
    await cmd_start(message, _state())
    assert len(message.sent) == 1
    assert "черновик" in message.sent[0]["text"].lower()
    assert message.sent[0]["reply_markup"] is keyboards.MAIN_MENU


async def test_consent_accepted_records_and_sends_greeting(isolated_env):
    assert not has_given_consent(702)
    callback = FakeCallbackQuery(data="consent_accept", message=FakeMessage(), user_id=702)
    await consent_accepted(callback)

    assert has_given_consent(702)
    assert callback.message.sent[-1]["text"] == texts.START
    assert callback.message.sent[-1]["reply_markup"] is keyboards.MAIN_MENU
    assert len(callback.answered) == 1


async def test_consent_declined_does_not_record(isolated_env):
    callback = FakeCallbackQuery(data="consent_decline", message=FakeMessage(), user_id=703)
    await consent_declined(callback)

    assert not has_given_consent(703)
    assert callback.message.sent[-1]["text"] == texts.CONSENT_DECLINED


# --- Ю3: middleware _consent_gate — сам гейт, применяемый к router'у ---
# Прямые вызовы хендлеров (весь остальной этот файл) минуют router и,
# значит, минуют middleware — эти тесты вызывают _consent_gate саму по
# себе, единственный способ проверить именно её логику.


async def test_consent_gate_blocks_message_without_consent(isolated_env):
    calls = []

    async def dummy(event, data):
        calls.append(event)

    message = FakeMessage(text="что угодно", user_id=704)
    result = await _consent_gate(dummy, message, {})

    assert calls == []
    assert result is None
    assert message.sent[-1]["text"] == texts.CONSENT_REQUIRED_REDIRECT


async def test_consent_gate_allows_start_without_consent(isolated_env):
    calls = []

    async def dummy(event, data):
        calls.append(event)

    message = FakeMessage(text="/start", user_id=705)
    await _consent_gate(dummy, message, {})

    assert calls == [message]


async def test_consent_gate_allows_consent_callbacks_without_consent(isolated_env):
    calls = []

    async def dummy(event, data):
        calls.append(event)

    callback = FakeCallbackQuery(data="consent_accept", message=FakeMessage(), user_id=706)
    await _consent_gate(dummy, callback, {})

    assert calls == [callback]


async def test_consent_gate_blocks_other_callbacks_without_consent(isolated_env):
    calls = []

    async def dummy(event, data):
        calls.append(event)

    callback = FakeCallbackQuery(data="gen_confirm", message=FakeMessage(), user_id=707)
    result = await _consent_gate(dummy, callback, {})

    assert calls == []
    assert result is None
    assert callback.message.sent[-1]["text"] == texts.CONSENT_REQUIRED_REDIRECT
    assert len(callback.answered) == 1


async def test_consent_gate_allows_everything_once_consented(isolated_env):
    record_consent(708)
    calls = []

    async def dummy(event, data):
        calls.append(event)

    message = FakeMessage(text="/generate", user_id=708)
    await _consent_gate(dummy, message, {})

    assert calls == [message]


# --- Э3: положительный ответ has_given_consent кэшируется в памяти ---


async def test_has_given_consent_caches_positive_answer(isolated_env):
    from bot.handlers import _consent_given_cache

    record_consent(709)
    assert 709 in _consent_given_cache

    # строку в базе убрали в обход record_consent (например, ручным SQL) —
    # кэш продолжает отвечать "да", это и есть смысл кэширования только
    # положительного ответа
    execute("DELETE FROM consents WHERE telegram_user_id = ?", (709,))
    assert has_given_consent(709) is True


async def test_has_given_consent_does_not_cache_negative_answer(isolated_env):
    from bot.handlers import _consent_given_cache

    assert has_given_consent(710) is False
    assert 710 not in _consent_given_cache

    # согласие появилось в базе позже (другим процессом/путём) — следующая
    # проверка обязана его увидеть, а не молчать про старый отказ
    execute("INSERT INTO consents (telegram_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)", (710,))
    assert has_given_consent(710) is True


# --- Э3: недоступность базы не пропускает вперёд, а честно сообщает ---


async def test_consent_gate_reports_db_unavailable_for_message(isolated_env, monkeypatch):
    import bot.handlers as handlers_module

    def _boom(telegram_user_id, db_path=None):
        raise SupabaseDatabaseError("Supabase недоступна")

    monkeypatch.setattr(handlers_module, "has_given_consent", _boom)

    calls = []

    async def dummy(event, data):
        calls.append(event)

    message = FakeMessage(text="что угодно", user_id=711)
    result = await _consent_gate(dummy, message, {})

    assert calls == []  # НЕ пропущен вперёд "на всякий случай"
    assert result is None
    assert message.sent[-1]["text"] == texts.CONSENT_CHECK_UNAVAILABLE


async def test_consent_gate_reports_db_unavailable_for_callback(isolated_env, monkeypatch):
    import bot.handlers as handlers_module

    def _boom(telegram_user_id, db_path=None):
        raise SupabaseDatabaseError("Supabase недоступна")

    monkeypatch.setattr(handlers_module, "has_given_consent", _boom)

    calls = []

    async def dummy(event, data):
        calls.append(event)

    callback = FakeCallbackQuery(data="gen_confirm", message=FakeMessage(), user_id=712)
    result = await _consent_gate(dummy, callback, {})

    assert calls == []
    assert result is None
    assert callback.message.sent[-1]["text"] == texts.CONSENT_CHECK_UNAVAILABLE
    assert len(callback.answered) == 1  # спиннер закрыт, не висит


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
        "klass": "10",
        "duration_minutes": 40,
        "objective_code": None,
        # "-" на шаге доп. настроек -> LessonOptions() со значениями по
        # умолчанию, не None — функционально то же самое (все проверки в
        # _render_lesson_options/_fill_header_fields одинаково пропускают
        # и None, и объект с пустыми полями), но по факту в payload лежит
        # словарь, а не null.
        "options": {
            "cennost_key": None,
            "adal_azamat_project_key": None,
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


async def test_generate_fast_path_uses_ktp_and_previous_ksp_without_questions(isolated_env):
    teacher_id = _create_teacher(97)
    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    execute(
        "INSERT INTO curriculum_objectives (code, grade, description) VALUES (?, ?, ?)",
        ("10.1.1.1", 10, "Тестовая цель"),
    )
    execute(
        "INSERT INTO ktp_entries (teacher_id, section, topic, objective_code) VALUES (?, ?, ?, ?)",
        (teacher_id, "Механика", "Закон Ома", "10.1.1.1"),
    )
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, template_id, content_json) VALUES (?, ?, ?, ?)",
        ("old-97", teacher_id, template_id, json.dumps({"klass": "10"})),
    )

    state = _state()
    await cmd_generate(FakeMessage(text="/generate", user_id=97), state)
    message = FakeMessage(text="Закон Ома", user_id=97, chat_id=97)
    await generate_topic_received(message, state)

    data = await state.get_data()
    assert await state.get_state() == Generate.waiting_for_confirmation.state
    assert data["razdel"] == "Механика"
    assert data["objective_code"] == "10.1.1.1"
    assert data["klass"] == "10"
    assert data["duration_minutes"] == 45
    buttons = message.sent[-1]["reply_markup"].inline_keyboard[0]
    assert [button.callback_data for button in buttons] == ["gen_confirm", "gen_change", "gen_cancel"]


async def test_generate_klass_keeps_only_number_for_new_generation(isolated_env):
    state = _state()
    await state.set_state(Generate.waiting_for_klass)
    message = FakeMessage(text="10А")
    await generate_klass_received(message, state)

    assert (await state.get_data())["klass"] == "10"


async def test_generate_option_button_changes_same_options_dictionary(isolated_env):
    from bot.handlers import generate_option_button_pressed

    state = _state()
    await state.set_state(Generate.waiting_for_extra_options)
    message = FakeMessage()
    callback = FakeCallbackQuery(data="opt:ima_oop", message=message)
    await generate_option_button_pressed(callback, state)

    assert (await state.get_data())["options"]["ima_oop"] is True
    assert await state.get_state() == Generate.waiting_for_extra_options.state


async def test_generate_option_button_sets_lesson_type(isolated_env):
    from bot.handlers import generate_option_button_pressed

    state = _state()
    await state.set_state(Generate.waiting_for_extra_options)
    callback = FakeCallbackQuery(data="opt:type:control", message=FakeMessage())
    await generate_option_button_pressed(callback, state)

    assert (await state.get_data())["options"]["tip_uroka"] == "контроль"


async def test_generate_value_button_sets_same_key_as_text_input(isolated_env):
    from bot.handlers import generate_option_button_pressed

    state = _state()
    await state.set_state(Generate.waiting_for_extra_options)
    callback = FakeCallbackQuery(data="opt:value:zakon_poryadok", message=FakeMessage())
    await generate_option_button_pressed(callback, state)

    assert (await state.get_data())["options"]["cennost_key"] == "zakon_poryadok"


async def test_generate_adal_azamat_project_button_sets_project_key(isolated_env):
    from bot.handlers import generate_option_button_pressed

    state = _state()
    await state.set_state(Generate.waiting_for_extra_options)
    callback = FakeCallbackQuery(data="opt:project:smart_bala", message=FakeMessage())
    await generate_option_button_pressed(callback, state)

    assert (await state.get_data())["options"]["adal_azamat_project_key"] == "smart_bala"


async def test_generate_confirmation_shows_source_of_fast_values(isolated_env):
    from bot.handlers import _render_generate_confirmation

    teacher_id = _create_teacher(98)
    template_id = query("SELECT id FROM templates WHERE is_builtin = 1")[0]["id"]
    state = _state()
    await state.update_data(
        teacher_id=teacher_id, template_id=template_id, topic="Тема", razdel="Раздел",
        objective_code="10.1.1.1", klass="10", duration_minutes=45,
        sources={"razdel": "КТП", "klass": "прошлая генерация"},
    )
    message = FakeMessage()
    assert await _render_generate_confirmation(message, state)
    assert "Раздел (КТП)" in message.sent[-1]["text"]
    assert "Класс: 10 (прошлая генерация)" in message.sent[-1]["text"]


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
    assert "Выдуманная ценность" in unrecognized[0]


@pytest.mark.parametrize("key", ["ООП", "СОР", "Физкультминутка"])
def test_parse_lesson_options_text_rejects_unknown_boolean_value(key):
    from bot.handlers import _parse_lesson_options_text

    options, errors = _parse_lesson_options_text(f"{key}: ага")

    assert len(errors) == 1
    assert "ага" in errors[0]
    assert key in errors[0]
    assert "да" in errors[0] and "нет" in errors[0]
    assert options.ima_oop is False
    assert options.sor_instead_of_reflection is False
    assert options.fizkultminutka is False


@pytest.mark.parametrize(
    ("line", "bad_value"),
    [
        ("Ориентация: квадратная", "квадратная"),
        ("Тип урока: какой-нибудь", "какой-нибудь"),
    ],
)
def test_parse_lesson_options_text_rejects_invalid_known_value(line, bad_value):
    from bot.handlers import _parse_lesson_options_text

    _, errors = _parse_lesson_options_text(line)

    assert len(errors) == 1
    assert bad_value in errors[0]


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


async def test_generate_extra_options_invalid_line_refuses_and_stays_on_step(isolated_env):
    from bot.handlers import generate_extra_options_received

    _create_teacher(1)
    state = _state()
    await state.update_data(teacher_id=1, topic="Т", razdel="Р", klass="10А", duration_minutes=40)
    await state.set_state(Generate.waiting_for_extra_options)

    message = FakeMessage(text="Чепуха: ерунда\nООП: да")
    await generate_extra_options_received(message, state)

    warning = message.sent[0]["text"]
    assert "Чепуха" in warning
    assert await state.get_state() == Generate.waiting_for_extra_options.state
    data = await state.get_data()
    assert "options" not in data


async def test_generate_extra_options_invalid_known_value_is_explained(isolated_env):
    from bot.handlers import generate_extra_options_received

    _create_teacher(1)
    state = _state()
    await state.update_data(teacher_id=1, topic="Т", razdel="Р", klass="10А", duration_minutes=40)
    await state.set_state(Generate.waiting_for_extra_options)

    message = FakeMessage(text="ООП: ага")
    await generate_extra_options_received(message, state)

    assert "ага" in message.sent[0]["text"]
    assert "ООП" in message.sent[0]["text"]
    assert "да" in message.sent[0]["text"] and "нет" in message.sent[0]["text"]
    assert await state.get_state() == Generate.waiting_for_extra_options.state


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

    async def fail_if_pdf_attempted(*args, **kwargs):
        raise AssertionError("КТП не должен запускать PDF-конвертацию")

    monkeypatch.setattr(handlers_module, "_try_send_pdf", fail_if_pdf_attempted)

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
# /class (У2, PLAN.md) — педагог создаёт класс и выдаёт код приглашения
# =====================================================================


async def _create_class_via_dialog(user_id: int, name: str = "10 А", subject: str | None = "физика") -> tuple[FakeMessage, str]:
    """Проходит диалог создания класса целиком, возвращает финальное
    сообщение с кодом приглашения и сам код."""
    state = _state()
    callback = FakeCallbackQuery(data="class_create", message=FakeMessage(user_id=user_id), user_id=user_id)
    await class_create_started(callback, state)

    name_message = FakeMessage(text=name, user_id=user_id)
    await class_name_received(name_message, state)

    subject_message = FakeMessage(text=subject if subject is not None else "-", user_id=user_id)
    await class_subject_received(subject_message, state)

    confirm_callback = FakeCallbackQuery(data="class_confirm_create", message=FakeMessage(user_id=user_id), user_id=user_id)
    await class_create_confirmed(confirm_callback, state)

    invite_message = confirm_callback.message.sent[-1]
    code = invite_message["text"].splitlines()[2]  # CLASS_INVITE_CODE_MESSAGE: код на третьей строке
    return confirm_callback.message, code


async def test_class_list_empty_offers_create_button(isolated_env):
    _create_teacher(1)
    message = FakeMessage(user_id=1)
    await cmd_class(message)
    assert message.sent[-1]["text"] == texts.CLASS_LIST_EMPTY
    assert message.sent[-1]["reply_markup"] is not None


async def test_class_create_dialog_creates_row_with_unique_invite_code(isolated_env):
    teacher_id = _create_teacher(1)
    _, code = await _create_class_via_dialog(1, name="10 А", subject="физика")

    rows = query("SELECT * FROM classes WHERE teacher_id = ?", (teacher_id,))
    assert len(rows) == 1
    assert rows[0]["name"] == "10 А"
    assert rows[0]["subject"] == "физика"
    assert rows[0]["invite_code"] == code
    # Ловушка блока У1: без похожих символов 0/O/1/I/L.
    assert not (set(code) & set("0O1IL"))


async def test_class_create_invite_code_sent_as_separate_message(isolated_env):
    _create_teacher(1)
    final_message, code = await _create_class_via_dialog(1)

    # Первое сообщение после подтверждения — "класс создан" с главным
    # меню, второе (отдельное!) — код приглашения крупно, план требует
    # это дословно, чтобы код было удобно сфотографировать/продиктовать
    # отдельно от остального текста.
    assert len(final_message.sent) == 2
    assert texts.CLASS_CREATED.format(name="10 А") == final_message.sent[0]["text"]
    assert code in final_message.sent[1]["text"]
    assert final_message.sent[1]["text"] != final_message.sent[0]["text"]


async def test_class_subject_dash_means_not_specified(isolated_env):
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="9 Б", subject=None)

    rows = query("SELECT * FROM classes WHERE teacher_id = ?", (teacher_id,))
    assert rows[0]["subject"] is None


async def test_class_list_shows_member_count(isolated_env):
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    student_id = execute("INSERT INTO students (telegram_id, name) VALUES (500, 'Ученик')")
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))

    message = FakeMessage(user_id=1)
    await cmd_class(message)
    assert "1 уч." in message.sent[-1]["text"] or "10 А" in str(message.sent[-1]["reply_markup"])


async def test_class_card_shows_invite_code_and_count(isolated_env):
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    callback = FakeCallbackQuery(data=f"class_view:{class_id}", message=FakeMessage(user_id=1), user_id=1)
    await class_view_requested(callback)

    text = callback.message.sent[-1]["text"]
    assert "10 А" in text
    assert "физика" in text
    assert "Учеников: 0" in text


async def test_class_view_of_foreign_class_says_not_found(isolated_env):
    _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes")[0]["id"]

    _create_teacher(2)
    callback = FakeCallbackQuery(data=f"class_view:{class_id}", message=FakeMessage(user_id=2), user_id=2)
    await class_view_requested(callback)

    assert callback.answered[-1]["text"] == texts.CLASS_NOT_FOUND
    assert callback.answered[-1]["show_alert"] is True


async def test_class_regenerate_changes_code(isolated_env):
    teacher_id = _create_teacher(1)
    _, old_code = await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    callback = FakeCallbackQuery(data=f"class_regen:{class_id}", message=FakeMessage(user_id=1), user_id=1)
    await class_regen_requested(callback)

    new_code = query("SELECT invite_code FROM classes WHERE id = ?", (class_id,))[0]["invite_code"]
    assert new_code != old_code
    assert old_code in "".join(m["text"] for m in callback.message.sent) or new_code in callback.message.sent[-1]["text"]
    # старый код больше не находится
    assert query("SELECT 1 FROM classes WHERE invite_code = ?", (old_code,)) == []


async def test_class_delete_requires_confirmation(isolated_env):
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    callback = FakeCallbackQuery(data=f"class_delete:{class_id}", message=FakeMessage(user_id=1), user_id=1)
    await class_delete_requested(callback)

    # ничего не удалено без подтверждения
    assert query("SELECT * FROM classes WHERE id = ?", (class_id,))
    assert callback.message.sent[-1]["reply_markup"] is not None


async def test_class_delete_cancel_deletes_nothing(isolated_env):
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    callback = FakeCallbackQuery(data=f"class_delete_cancel:{class_id}", message=FakeMessage(user_id=1), user_id=1)
    await class_delete_cancelled(callback)

    assert query("SELECT * FROM classes WHERE id = ?", (class_id,))
    assert callback.message.sent[-1]["text"] == texts.CLASS_DELETE_CANCELLED


async def test_class_delete_confirm_removes_class_but_keeps_students(isolated_env):
    """У1, ловушка дословно: удаление класса не удаляет учеников — только
    их связь с этим классом."""
    teacher_id = _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (teacher_id,))[0]["id"]

    student_id = execute("INSERT INTO students (telegram_id, name) VALUES (500, 'Ученик')")
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))

    callback = FakeCallbackQuery(
        data=f"class_delete_confirm:{class_id}", message=FakeMessage(user_id=1), user_id=1
    )
    await class_delete_confirmed(callback)

    assert query("SELECT * FROM classes WHERE id = ?", (class_id,)) == []
    assert query("SELECT * FROM class_members WHERE class_id = ?", (class_id,)) == []
    # ученик остался — удалилась только связь
    assert query("SELECT * FROM students WHERE id = ?", (student_id,))
    assert "10 А" in callback.message.sent[-1]["text"]


async def test_class_delete_of_foreign_class_says_not_found_and_deletes_nothing(isolated_env):
    _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")
    class_id = query("SELECT id FROM classes")[0]["id"]

    _create_teacher(2)
    callback = FakeCallbackQuery(
        data=f"class_delete_confirm:{class_id}", message=FakeMessage(user_id=2), user_id=2
    )
    await class_delete_confirmed(callback)

    assert callback.answered[-1]["text"] == texts.CLASS_NOT_FOUND
    assert query("SELECT * FROM classes WHERE id = ?", (class_id,))  # не удалён чужим


async def test_class_list_callback_shows_classes_again(isolated_env):
    _create_teacher(1)
    await _create_class_via_dialog(1, name="10 А")

    callback = FakeCallbackQuery(data="class_list", message=FakeMessage(user_id=1), user_id=1)
    await class_list_requested(callback)

    assert callback.message.sent[-1]["text"] == texts.CLASS_LIST_HEADER


async def test_class_create_back_button_returns_to_previous_step(isolated_env):
    """М3.2: «← Назад» на шаге предмета обязан вернуть на шаг названия,
    не бросить диалог и не съесть ввод как что-то другое (грабля 2.4)."""
    _create_teacher(1)
    state = _state()
    callback = FakeCallbackQuery(data="class_create", message=FakeMessage(user_id=1), user_id=1)
    await class_create_started(callback, state)
    assert await state.get_state() == "ClassCreate:waiting_for_name"

    await class_name_received(FakeMessage(text="10 А", user_id=1), state)
    assert await state.get_state() == "ClassCreate:waiting_for_subject"

    back_message = FakeMessage(user_id=1)
    await back_button_pressed(back_message, state)
    assert await state.get_state() == "ClassCreate:waiting_for_name"
    assert texts.CLASS_ASK_NAME in back_message.sent[-1]["text"]
    assert "10 А" in back_message.sent[-1]["text"]  # CURRENT_VALUE_NOTE — не забыл введённое


async def test_class_added_to_bot_commands_and_menu():
    command_names = {name for name, _ in texts.BOT_COMMANDS}
    assert "class" in command_names
    assert texts.MENU_BUTTON_CLASS in keyboards.MAIN_MENU_BUTTON_TEXTS


# =====================================================================
# У3 (PLAN.md) — /start впервые (педагог или ученик), ученик вступает
# в класс по коду приглашения
# =====================================================================


async def _create_class_and_get_code(teacher_user_id: int, name: str = "10 А") -> str:
    _create_teacher(teacher_user_id)
    _, code = await _create_class_via_dialog(teacher_user_id, name=name)
    return code


async def test_role_teacher_chosen_shows_consent_when_not_given(isolated_env):
    callback = FakeCallbackQuery(data="role_teacher", message=FakeMessage(user_id=800), user_id=800)
    await role_teacher_chosen(callback)
    assert callback.message.sent[-1]["text"] == texts.CONSENT_TEXT


async def test_role_teacher_chosen_shows_greeting_when_already_given(isolated_env):
    record_consent(801)
    callback = FakeCallbackQuery(data="role_teacher", message=FakeMessage(user_id=801), user_id=801)
    await role_teacher_chosen(callback)
    assert callback.message.sent[-1]["text"] == texts.START
    assert callback.message.sent[-1]["reply_markup"] is keyboards.MAIN_MENU


async def test_role_student_chosen_creates_student_row_and_shows_student_consent(isolated_env):
    callback = FakeCallbackQuery(data="role_student", message=FakeMessage(user_id=802), user_id=802)
    await role_student_chosen(callback, _state())

    assert query("SELECT * FROM students WHERE telegram_id = 802")
    assert callback.message.sent[-1]["text"] == texts.STUDENT_CONSENT_TEXT
    assert "законного представителя" in texts.STUDENT_CONSENT_TEXT
    assert "трансграничная передача" in texts.STUDENT_CONSENT_TEXT


async def test_role_student_chosen_twice_does_not_duplicate_student_row(isolated_env):
    callback1 = FakeCallbackQuery(data="role_student", message=FakeMessage(user_id=803), user_id=803)
    await role_student_chosen(callback1, _state())
    callback2 = FakeCallbackQuery(data="role_student", message=FakeMessage(user_id=803), user_id=803)
    await role_student_chosen(callback2, _state())

    assert len(query("SELECT * FROM students WHERE telegram_id = 803")) == 1


async def test_student_consent_accept_leads_straight_to_code_entry(isolated_env):
    state = _state()
    await role_student_chosen(
        FakeCallbackQuery(data="role_student", message=FakeMessage(user_id=804), user_id=804), state
    )
    callback = FakeCallbackQuery(data="student_consent_accept", message=FakeMessage(user_id=804), user_id=804)
    await student_consent_accepted(callback, state)

    assert has_given_consent(804)
    assert await state.get_state() == "StudentJoin:waiting_for_code"
    assert texts.STUDENT_JOIN_ASK_CODE in callback.message.sent[-1]["text"]


async def test_student_consent_decline_does_not_record_consent(isolated_env):
    callback = FakeCallbackQuery(data="student_consent_decline", message=FakeMessage(user_id=805), user_id=805)
    await student_consent_declined(callback)
    assert not has_given_consent(805)
    assert callback.message.sent[-1]["text"] == texts.CONSENT_DECLINED


async def test_join_code_not_found_stays_in_same_state_and_does_not_kick_to_menu(isolated_env):
    """У3, дословно: "код не найден" + предложение ввести заново, НЕ
    выкидывая в главное меню — состояние не должно меняться."""
    state = _state()
    message = FakeMessage(text="/join", user_id=806)
    await cmd_join(message, state)
    assert await state.get_state() == "StudentJoin:waiting_for_code"

    wrong_code_message = FakeMessage(text="ZZZZZZ", user_id=806)
    await student_join_code_received(wrong_code_message, state)

    assert await state.get_state() == "StudentJoin:waiting_for_code"
    assert texts.STUDENT_JOIN_CODE_NOT_FOUND in wrong_code_message.sent[-1]["text"]
    assert texts.STUDENT_JOIN_ASK_CODE in wrong_code_message.sent[-1]["text"]


async def test_join_valid_code_shows_class_and_teacher_name(isolated_env):
    code = await _create_class_and_get_code(810, name="10 А")

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=811), state)
    code_message = FakeMessage(text=code, user_id=811)
    await student_join_code_received(code_message, state)

    assert await state.get_state() == "StudentJoin:waiting_for_confirmation"
    text = code_message.sent[-1]["text"]
    assert "10 А" in text
    assert "Тестов Тест" in text  # _create_teacher default name


async def test_join_code_is_case_and_whitespace_insensitive(isolated_env):
    code = await _create_class_and_get_code(812, name="9 Б")

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=813), state)
    code_message = FakeMessage(text=f"  {code.lower()}  ", user_id=813)
    await student_join_code_received(code_message, state)

    assert await state.get_state() == "StudentJoin:waiting_for_confirmation"
    assert "9 Б" in code_message.sent[-1]["text"]


async def test_join_confirm_creates_membership(isolated_env):
    code = await _create_class_and_get_code(814, name="10 А")

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=815), state)
    await student_join_code_received(FakeMessage(text=code, user_id=815), state)

    callback = FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=815), user_id=815)
    await student_join_confirmed(callback, state)

    student_id = query("SELECT id FROM students WHERE telegram_id = 815")[0]["id"]
    class_id = query("SELECT id FROM classes WHERE name = '10 А'")[0]["id"]
    assert query(
        "SELECT * FROM class_members WHERE class_id = ? AND student_id = ?", (class_id, student_id)
    )
    assert await state.get_state() is None
    assert texts.STUDENT_JOIN_SUCCESS.format(class_name="10 А", teacher_name="Тестов Тест") == callback.message.sent[-1]["text"]


async def test_join_confirm_second_time_says_already_member_and_does_not_duplicate(isolated_env):
    code = await _create_class_and_get_code(816, name="10 А")

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=817), state)
    await student_join_code_received(FakeMessage(text=code, user_id=817), state)
    await student_join_confirmed(
        FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=817), user_id=817), state
    )

    # вступает снова тем же кодом
    await cmd_join(FakeMessage(text="/join", user_id=817), state)
    await student_join_code_received(FakeMessage(text=code, user_id=817), state)
    callback2 = FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=817), user_id=817)
    await student_join_confirmed(callback2, state)

    assert callback2.message.sent[-1]["text"] == texts.STUDENT_JOIN_ALREADY_MEMBER.format(class_name="10 А")
    student_id = query("SELECT id FROM students WHERE telegram_id = 817")[0]["id"]
    class_id = query("SELECT id FROM classes WHERE name = '10 А'")[0]["id"]
    rows = query("SELECT * FROM class_members WHERE class_id = ? AND student_id = ?", (class_id, student_id))
    assert len(rows) == 1  # не задвоилось


async def test_join_cancel_creates_no_membership(isolated_env):
    code = await _create_class_and_get_code(818, name="10 А")

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=819), state)
    await student_join_code_received(FakeMessage(text=code, user_id=819), state)

    callback = FakeCallbackQuery(data="student_join_cancel", message=FakeMessage(user_id=819), user_id=819)
    await student_join_cancelled(callback, state)

    assert query("SELECT * FROM class_members") == []
    assert callback.message.sent[-1]["text"] == texts.STUDENT_JOIN_CANCELLED


async def test_student_can_join_two_different_classes(isolated_env):
    code_a = await _create_class_and_get_code(820, name="10 А")
    code_b = await _create_class_and_get_code(821, name="10 Б")

    state = _state()
    for code in (code_a, code_b):
        await cmd_join(FakeMessage(text="/join", user_id=822), state)
        await student_join_code_received(FakeMessage(text=code, user_id=822), state)
        await student_join_confirmed(
            FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=822), user_id=822), state
        )

    student_id = query("SELECT id FROM students WHERE telegram_id = 822")[0]["id"]
    rows = query("SELECT class_id FROM class_members WHERE student_id = ?", (student_id,))
    assert len(rows) == 2


async def test_start_for_known_student_without_consent_shows_student_consent(isolated_env):
    execute("INSERT INTO students (telegram_id, name) VALUES (823, 'Ученик')")
    message = FakeMessage(text="/start", user_id=823)
    await cmd_start(message, _state())
    assert message.sent[-1]["text"] == texts.STUDENT_CONSENT_TEXT


async def test_start_for_known_student_with_consent_shows_home_without_teacher_menu(isolated_env):
    execute("INSERT INTO students (telegram_id, name) VALUES (824, 'Ученик')")
    record_consent(824)
    message = FakeMessage(text="/start", user_id=824)
    await cmd_start(message, _state())
    assert message.sent[-1]["text"] == texts.STUDENT_HOME_NO_CLASSES
    assert message.sent[-1]["reply_markup"] is not keyboards.MAIN_MENU


async def test_start_for_known_student_lists_their_classes(isolated_env):
    code = await _create_class_and_get_code(825, name="10 А")
    execute("INSERT INTO students (telegram_id, name) VALUES (826, 'Ученик Тестов')")
    record_consent(826)

    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=826), state)
    await student_join_code_received(FakeMessage(text=code, user_id=826), state)
    await student_join_confirmed(
        FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=826), user_id=826), state
    )

    message = FakeMessage(text="/start", user_id=826)
    await cmd_start(message, _state())
    assert "10 А" in message.sent[-1]["text"]
    assert "Тестов Тест" in message.sent[-1]["text"]


async def test_consent_gate_exempts_role_and_student_consent_callbacks(isolated_env):
    """Э3/У3: выбор роли и согласие ученика происходят ДО того, как
    согласие вообще может быть дано — гейт не должен их блокировать."""
    calls = []

    async def dummy(event, data):
        calls.append(event)

    for data in ("role_teacher", "role_student", "student_consent_accept", "student_consent_decline"):
        calls.clear()
        callback = FakeCallbackQuery(data=data, message=FakeMessage(), user_id=830)
        await _consent_gate(dummy, callback, {})
        assert calls == [callback], f"{data} не должен блокироваться _consent_gate"


# =====================================================================
# Находка 1 AUDIT.md — _student_gate: педагогические команды для ученика
# не существуют. Как и у _consent_gate выше, прямые вызовы хендлеров
# минуют router и middleware, поэтому гейт вызывается здесь сам по себе.
# =====================================================================


def _register_student(telegram_id: int, name: str = "Ученик Тестовый") -> None:
    execute("INSERT INTO students (telegram_id, name) VALUES (?, ?)", (telegram_id, name))
    record_consent(telegram_id)


async def _pass_through_student_gate(text: str, user_id: int):
    """Возвращает (список пропущенных событий, само событие)."""
    calls = []

    async def dummy(event, data):
        calls.append(event)

    message = FakeMessage(text=text, user_id=user_id)
    await _student_gate(dummy, message, {})
    return calls, message


async def test_student_gate_blocks_menu_command_for_student(isolated_env):
    """Главный сценарий находки: ребёнок набирает /menu и получает
    клавиатуру педагога со всеми его функциями."""
    _register_student(840)
    calls, message = await _pass_through_student_gate("/menu", 840)

    assert calls == []
    assert message.sent[0]["text"] == texts.STUDENT_TEACHER_COMMAND_UNAVAILABLE
    assert message.sent[-1]["reply_markup"] is not keyboards.MAIN_MENU


async def test_student_gate_blocks_teacher_command_for_student(isolated_env):
    """Вторая половина находки: бот сам называл ребёнку команду, которой
    система обходится, и после неё ребёнок становился педагогом."""
    _register_student(841)
    calls, message = await _pass_through_student_gate("/teacher", 841)

    assert calls == []
    assert query("SELECT 1 FROM teachers WHERE telegram_user_id = 841") == []


async def test_student_gate_blocks_every_teacher_command(isolated_env):
    _register_student(842)
    for command in ("/generate", "/generate_ktp", "/upload_ksp", "/upload_ktp", "/konspekt",
                    "/templates", "/upload_template", "/status", "/history", "/class",
                    "/dashboard", "/admin"):
        calls, _ = await _pass_through_student_gate(command, 842)
        assert calls == [], f"{command} не должна доходить до хендлера у ученика"


async def test_student_gate_blocks_menu_buttons_for_student(isolated_env):
    """Кнопки постоянного меню — такая же точка входа педагога, как
    команды: menu_button_pressed раздавал их кому угодно."""
    _register_student(843)
    for button_text in sorted(keyboards.MAIN_MENU_BUTTON_TEXTS):
        calls, _ = await _pass_through_student_gate(button_text, 843)
        assert calls == [], f"кнопка {button_text!r} не должна доходить до хендлера у ученика"


async def test_student_gate_allows_student_own_commands(isolated_env):
    _register_student(844)
    for command in ("/start", "/join", "/sverka", "/cancel", "/back", "/delete_my_data"):
        calls, _ = await _pass_through_student_gate(command, 844)
        assert calls != [], f"{command} — команда ученика, гейт не должен её блокировать"


async def test_student_gate_allows_free_text_of_student_dialogs(isolated_env):
    """Код приглашения, подпись к фото и прочий свободный текст ученика
    гейт пропускает и в базу за ролью при этом не ходит."""
    _register_student(845)
    calls, _ = await _pass_through_student_gate("ABCDEF", 845)
    assert calls != []


async def test_student_gate_does_not_touch_teacher(isolated_env):
    """Страховка: педагог, который дал согласие, но не проходил /teacher,
    обязан продолжать работать — гейт про роль, а не про профиль."""
    record_consent(846)
    for command in ("/menu", "/generate", "/teacher"):
        calls, _ = await _pass_through_student_gate(command, 846)
        assert calls != [], f"{command} у педагога не должна блокироваться"


async def test_student_gate_does_not_query_db_for_ordinary_text(isolated_env, monkeypatch):
    """Э3: лишнего похода в Supabase на каждое сообщение быть не должно —
    роль спрашивается только на точке входа педагога."""
    import bot.handlers as handlers_module

    def _boom(telegram_id, db_path=None):
        raise AssertionError("гейт не должен спрашивать роль на обычном тексте")

    monkeypatch.setattr(handlers_module, "_is_student", _boom)
    calls, _ = await _pass_through_student_gate("просто текст", 847)
    assert calls != []


async def test_student_gate_reports_db_unavailable_instead_of_letting_through(isolated_env, monkeypatch):
    """Э3, та же сторона отказа: не зная роли, вперёд не пропускаем."""
    import bot.handlers as handlers_module

    def _boom(telegram_id, db_path=None):
        raise SupabaseDatabaseError("Supabase недоступна")

    monkeypatch.setattr(handlers_module, "_is_student", _boom)
    calls, message = await _pass_through_student_gate("/menu", 848)

    assert calls == []
    assert message.sent[-1]["text"] == texts.ROLE_CHECK_UNAVAILABLE


async def test_student_gate_lists_classes_instead_of_teacher_menu(isolated_env):
    """Ученику вместо меню педагога — его собственное домашнее сообщение."""
    code = await _create_class_and_get_code(849, name="11 Б")
    _register_student(850)
    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=850), state)
    await student_join_code_received(FakeMessage(text=code, user_id=850), state)
    await student_join_confirmed(
        FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=850), user_id=850), state
    )

    calls, message = await _pass_through_student_gate("/menu", 850)
    assert calls == []
    assert "11 Б" in message.sent[-1]["text"]


def test_command_name_parses_mention_and_arguments():
    from bot.handlers import _command_name

    assert _command_name("/generate") == "generate"
    assert _command_name("/generate@ksp_navigator_bot") == "generate"
    assert _command_name("/generate тема урока") == "generate"
    assert _command_name("обычный текст") is None
    assert _command_name("") is None


# =====================================================================
# /sverka (У4, PLAN.md) — диалог: выбор класса/урока, приём фото
# =====================================================================


async def _become_student_in_one_class(teacher_user_id: int, student_user_id: int, class_name: str = "10 А") -> dict:
    code = await _create_class_and_get_code(teacher_user_id, name=class_name)
    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=student_user_id), state)
    await student_join_code_received(FakeMessage(text=code, user_id=student_user_id), state)
    await student_join_confirmed(
        FakeCallbackQuery(data="student_join_confirm", message=FakeMessage(user_id=student_user_id), user_id=student_user_id),
        state,
    )
    class_id = query("SELECT id FROM classes WHERE name = ?", (class_name,))[0]["id"]
    return {"class_id": class_id}


async def test_sverka_not_a_student_gets_polite_refusal(isolated_env):
    _create_teacher(860)
    message = FakeMessage(text="/sverka", user_id=860)
    await cmd_sverka(message)
    assert message.sent[-1]["text"] == texts.SVERKA_NOT_A_STUDENT


async def test_sverka_student_without_classes_is_told_to_join(isolated_env):
    execute("INSERT INTO students (telegram_id, name) VALUES (861, 'Ученик')")
    message = FakeMessage(text="/sverka", user_id=861)
    await cmd_sverka(message)
    assert message.sent[-1]["text"] == texts.SVERKA_NO_CLASSES


async def test_sverka_single_class_skips_class_choice_shows_no_transcript(isolated_env):
    """Ловушка У4, дословно: если у урока нет расшифровки — сказать
    прямо, не выдавать пустой результат."""
    await _become_student_in_one_class(862, 863)
    message = FakeMessage(text="/sverka", user_id=863)
    await cmd_sverka(message)
    assert message.sent[-1]["text"] == texts.SVERKA_NO_TRANSCRIPT


async def test_sverka_shows_recent_lessons_when_transcript_exists(isolated_env):
    seed = await _seed_sverka_lesson(864, 865, "расшифровка про давление")
    message = FakeMessage(text="/sverka", user_id=865)
    await cmd_sverka(message)

    assert message.sent[-1]["text"] == texts.SVERKA_ASK_LESSON
    assert message.sent[-1]["reply_markup"] is not None
    button_text = message.sent[-1]["reply_markup"].inline_keyboard[0][0].text
    assert texts.SVERKA_LESSON_NO_TOPIC in button_text  # тема не указана в этом сиде


async def test_sverka_multiple_classes_asks_which_one_first(isolated_env):
    teacher_id = _create_teacher(866)
    class_a = execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (teacher_id,))
    class_b = execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 Б', 'BBB222')", (teacher_id,))
    student_id = execute("INSERT INTO students (telegram_id, name) VALUES (867, 'Ученик')")
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_a, student_id))
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_b, student_id))

    message = FakeMessage(text="/sverka", user_id=867)
    await cmd_sverka(message)
    assert message.sent[-1]["text"] == texts.SVERKA_ASK_CLASS


async def test_sverka_lesson_choice_from_foreign_teacher_is_rejected(isolated_env):
    """Callback_data с transcript_id нельзя доверять без проверки — чужой
    учитель не должен быть доступен через подделанный callback."""
    await _become_student_in_one_class(868, 869)
    other_seed = await _seed_sverka_lesson(870, 999999, "чужая расшифровка")

    state = _state()
    callback = FakeCallbackQuery(
        data=f"sverka_lesson:{other_seed['transcript_id']}", message=FakeMessage(user_id=869), user_id=869
    )
    await sverka_lesson_chosen(callback, state)

    assert callback.answered[-1]["text"] == texts.SVERKA_LESSON_NOT_FOUND
    assert await state.get_state() is None


async def test_sverka_lesson_choice_leads_to_photo_request(isolated_env):
    seed = await _seed_sverka_lesson(871, 872, "расшифровка")
    state = _state()
    callback = FakeCallbackQuery(
        data=f"sverka_lesson:{seed['transcript_id']}", message=FakeMessage(user_id=872), user_id=872
    )
    await sverka_lesson_chosen(callback, state)

    assert await state.get_state() == "SverkaCheck:waiting_for_photo"
    assert callback.message.sent[-1]["text"] == texts.SVERKA_ASK_PHOTO
    assert (await state.get_data())["transcript_id"] == seed["transcript_id"]


async def test_sverka_wrong_input_reasks_for_photo(isolated_env):
    message = FakeMessage(text="какой-то текст", user_id=873)
    await sverka_wrong_input(message)
    assert message.sent[-1]["text"] == texts.SVERKA_ASK_PHOTO


async def test_sverka_photo_received_enqueues_task_and_clears_state(isolated_env):
    seed = await _seed_sverka_lesson(874, 875, "расшифровка")
    state = _state()
    await state.update_data(transcript_id=seed["transcript_id"])
    await state.set_state(SverkaCheck.waiting_for_photo)

    message = FakeMessage(user_id=875, photo=[FakePhotoSize(file_size=1000)])
    bot = FakeBot()
    await sverka_photo_received(message, state, bot)

    tasks = query("SELECT * FROM tasks WHERE type = 'sverka_tetradi'")
    assert len(tasks) == 1
    payload = json.loads(tasks[0]["payload"])
    assert payload["transcript_id"] == seed["transcript_id"]
    assert payload["student_id"] == seed["student_id"]
    assert Path(payload["photo_path"]).exists()  # скачано на диск, ждёт обработчика задачи

    assert message.sent[-1]["text"] == texts.SVERKA_PROCESSING
    assert await state.get_state() is None


async def test_sverka_photo_checks_student_limit_before_enqueue(isolated_env, monkeypatch):
    """Находка 8 AUDIT.md: check_student_sverka_limit не вызывался
    ниоткуда — включение STUDENT_TARIFFS_ENABLED не изменило бы ничего.
    Флаг здесь не трогаем: проверяем, что вызов вообще есть, подменив
    саму функцию."""
    from core.limits import LimitExceeded, next_reset_kostanay

    seed = await _seed_sverka_lesson(878, 879, "расшифровка")
    state = _state()
    await state.update_data(transcript_id=seed["transcript_id"])
    await state.set_state(SverkaCheck.waiting_for_photo)

    def _exhausted(telegram_user_id, tariff="free", db_path=None):
        raise LimitExceeded(
            "лимит исчерпан", reset_at=next_reset_kostanay(), used=5, limit=5
        )

    monkeypatch.setattr("bot.handlers.check_student_sverka_limit", _exhausted)

    message = FakeMessage(user_id=879, photo=[FakePhotoSize(file_size=1000)])
    bot = FakeBot()
    await sverka_photo_received(message, state, bot)

    assert query("SELECT * FROM tasks WHERE type = 'sverka_tetradi'") == []
    assert "5" in message.sent[-1]["text"]
    assert message.sent[-1]["reply_markup"] is not keyboards.MAIN_MENU
    assert await state.get_state() is None
    # фото не скачивалось — отказ раньше загрузки (грабля 2.8)
    assert list(settings.uploads_dir.glob("*.jpg")) == []


async def test_sverka_pilot_stays_unlimited_for_student(isolated_env):
    """КГ 15 плана: ученик делает подряд больше сверок, чем стоит в самом
    щедром тарифе заглушки, и не получает отказа — пилот безлимитный
    (MASTER.md 0.10 п.1). Проверяется на живом пути диалога, а не только
    прямым вызовом функции лимита."""
    from core.limits import STUDENT_DAILY_SVERKA_LIMITS, record_student_usage

    seed = await _seed_sverka_lesson(880, 881, "расшифровка")
    most_generous = max(STUDENT_DAILY_SVERKA_LIMITS.values())

    for attempt in range(most_generous + 2):
        state = _state()
        await state.update_data(transcript_id=seed["transcript_id"])
        await state.set_state(SverkaCheck.waiting_for_photo)
        message = FakeMessage(user_id=881, photo=[FakePhotoSize(file_size=1000)])
        await sverka_photo_received(message, state, FakeBot())
        assert message.sent[-1]["text"] == texts.SVERKA_PROCESSING, f"отказ на попытке {attempt + 1}"
        # расход ученика при этом честно пишется — счётчик из блока У3
        record_student_usage(881)

    assert len(query("SELECT * FROM tasks WHERE type = 'sverka_tetradi'")) == most_generous + 2


async def test_sverka_photo_too_large_is_rejected_before_download(isolated_env):
    seed = await _seed_sverka_lesson(876, 877, "расшифровка")
    state = _state()
    await state.update_data(transcript_id=seed["transcript_id"])
    await state.set_state(SverkaCheck.waiting_for_photo)

    huge_photo = FakePhotoSize(file_size=MAX_FILE_SIZE_BYTES + 1)
    message = FakeMessage(user_id=877, photo=[huge_photo])
    bot = FakeBot()
    await sverka_photo_received(message, state, bot)

    assert query("SELECT * FROM tasks WHERE type = 'sverka_tetradi'") == []
    assert bot.downloaded == []


# =====================================================================
# У5 (PLAN.md) — педагог отправляет конспект пропустившему ученику
# =====================================================================


async def _seed_student_konspekt(teacher_user_id: int, tmp_path, tema: str = "Импульс") -> dict:
    """Заводит педагога и конспект в режиме 'student' (единственный
    режим, который У5 разрешает отправлять) с настоящим файлом на диске."""
    teacher_id = _create_teacher(teacher_user_id)
    docx_path = tmp_path / "konspekt.docx"
    docx_path.write_bytes(b"fake konspekt docx")
    konspekt_id = str(uuid.uuid4())
    execute(
        "INSERT INTO konspekty (id, teacher_id, mode, tema, content_json, docx_path) "
        "VALUES (?, ?, 'student', ?, '{}', ?)",
        (konspekt_id, teacher_id, tema, str(docx_path)),
    )
    return {"teacher_id": teacher_id, "konspekt_id": konspekt_id, "docx_path": docx_path}


async def _seed_teacher_mode_konspekt(teacher_user_id: int) -> dict:
    """Голая расшифровка (режим 'teacher', блок К0) — У5 обязан
    отказаться её отправлять: это и есть полная запись урока."""
    teacher_id = _create_teacher(teacher_user_id)
    konspekt_id = str(uuid.uuid4())
    execute(
        "INSERT INTO konspekty (id, teacher_id, mode, tema, content_json) "
        "VALUES (?, ?, 'teacher', 'Расшифровка урока', '{}')",
        (konspekt_id, teacher_id),
    )
    return {"teacher_id": teacher_id, "konspekt_id": konspekt_id}


async def test_konspekt_final_message_has_both_ksp_and_send_buttons(isolated_env, monkeypatch):
    """У5: кнопка «Отправить ученику» появляется на той же карточке, что
    и «Собрать КСП по этому конспекту», не заменяет её."""
    teacher_id = _create_teacher(960)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-960', ?, 'audio', 'расшифровка', 47, 'ru')",
        (teacher_id,),
    )

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return dict(_SAMPLE_KONSPEKT_CONTENT)

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k-960",
        "type": "generate_konspekt",
        "telegram_chat_id": 960,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-960"},
    }
    result = await handler(task)

    last_markup = bot.sent_messages[-1][2]
    assert last_markup.inline_keyboard[0][0].text == texts.KSP_FROM_KONSPEKT_BUTTON
    assert last_markup.inline_keyboard[1][0].text == texts.SEND_KONSPEKT_BUTTON
    assert last_markup.inline_keyboard[1][0].callback_data == f"sk:{result['konspekt_id']}"


async def test_send_konspekt_teacher_mode_is_rejected(isolated_env):
    """У5, дословно (через MASTER.md 0.9 п.3): голую расшифровку
    (режим 'teacher') отправлять ученику нельзя — это и есть полная
    запись урока."""
    seed = await _seed_teacher_mode_konspekt(961)
    callback = FakeCallbackQuery(data=f"sk:{seed['konspekt_id']}", message=FakeMessage(user_id=961), user_id=961)
    await send_konspekt_requested(callback)
    assert callback.answered[-1]["text"] == texts.SEND_KONSPEKT_NOT_FOUND


async def test_send_konspekt_no_classes_tells_teacher_to_create_one(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(962, tmp_path)
    callback = FakeCallbackQuery(data=f"sk:{seed['konspekt_id']}", message=FakeMessage(user_id=962), user_id=962)
    await send_konspekt_requested(callback)
    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_NO_CLASSES


async def test_send_konspekt_single_class_skips_straight_to_student_picker(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(963, tmp_path)
    execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (seed["teacher_id"],)
    )
    execute("INSERT INTO students (telegram_id, name) VALUES (964, 'Ученик Тестов')")
    class_id = query("SELECT id FROM classes")[0]["id"]
    student_id = query("SELECT id FROM students")[0]["id"]
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))

    callback = FakeCallbackQuery(data=f"sk:{seed['konspekt_id']}", message=FakeMessage(user_id=963), user_id=963)
    await send_konspekt_requested(callback)

    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_ASK_STUDENT
    button = callback.message.sent[-1]["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Ученик Тестов"
    assert button.callback_data == f"sks:{seed['konspekt_id']}:{student_id}"


async def test_send_konspekt_multiple_classes_asks_which_first(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(965, tmp_path)
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (seed["teacher_id"],))
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 Б', 'BBB222')", (seed["teacher_id"],))

    callback = FakeCallbackQuery(data=f"sk:{seed['konspekt_id']}", message=FakeMessage(user_id=965), user_id=965)
    await send_konspekt_requested(callback)
    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_ASK_CLASS


async def test_send_konspekt_class_with_no_members(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(966, tmp_path)
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (seed["teacher_id"],))
    class_id = query("SELECT id FROM classes")[0]["id"]

    callback = FakeCallbackQuery(
        data=f"skc:{seed['konspekt_id']}:{class_id}", message=FakeMessage(user_id=966), user_id=966
    )
    await send_konspekt_class_chosen(callback)
    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_NO_MEMBERS


async def test_send_konspekt_student_chosen_sends_document_with_caption(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(967, tmp_path, tema="Импульс")
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (seed["teacher_id"],))
    execute("INSERT INTO students (telegram_id, name) VALUES (968, 'Ученик Тестов')")
    class_id = query("SELECT id FROM classes")[0]["id"]
    student_id = query("SELECT id FROM students")[0]["id"]
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))

    bot = FakeBot()
    callback = FakeCallbackQuery(
        data=f"sks:{seed['konspekt_id']}:{student_id}", message=FakeMessage(user_id=967), user_id=967
    )
    await send_konspekt_student_chosen(callback, bot)

    assert len(bot.sent_documents) == 1
    sent = bot.sent_documents[0]
    assert sent["chat_id"] == 968
    assert sent["caption"] == texts.SEND_KONSPEKT_CAPTION.format(teacher_name="Тестов Тест", tema="Импульс")
    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_SENT.format(student_name="Ученик Тестов")


async def test_send_konspekt_to_student_of_another_teacher_is_rejected(isolated_env, tmp_path):
    """Регрессия: student_id в callback_data не привязан к конкретному
    классу — без перепроверки владения через class_members/classes
    подделанный student_id отправил бы конспект чужому ученику любого
    учителя в системе."""
    seed = await _seed_student_konspekt(969, tmp_path)
    other_teacher_id = _create_teacher(970)
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '9 В', 'ZZZ999')", (other_teacher_id,))
    execute("INSERT INTO students (telegram_id, name) VALUES (971, 'Чужой Ученик')")
    other_class_id = query("SELECT id FROM classes WHERE teacher_id = ?", (other_teacher_id,))[0]["id"]
    other_student_id = query("SELECT id FROM students WHERE telegram_id = 971")[0]["id"]
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (other_class_id, other_student_id))

    bot = FakeBot()
    callback = FakeCallbackQuery(
        data=f"sks:{seed['konspekt_id']}:{other_student_id}", message=FakeMessage(user_id=969), user_id=969
    )
    await send_konspekt_student_chosen(callback, bot)

    assert bot.sent_documents == []
    assert callback.answered[-1]["text"] == texts.SEND_KONSPEKT_STUDENT_NOT_FOUND


async def test_send_konspekt_missing_docx_file(isolated_env, tmp_path):
    seed = await _seed_student_konspekt(972, tmp_path)
    seed["docx_path"].unlink()  # файл пропал с диска
    execute("INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', 'AAA111')", (seed["teacher_id"],))
    execute("INSERT INTO students (telegram_id, name) VALUES (973, 'Ученик')")
    class_id = query("SELECT id FROM classes")[0]["id"]
    student_id = query("SELECT id FROM students")[0]["id"]
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))

    bot = FakeBot()
    callback = FakeCallbackQuery(
        data=f"sks:{seed['konspekt_id']}:{student_id}", message=FakeMessage(user_id=972), user_id=972
    )
    await send_konspekt_student_chosen(callback, bot)

    assert bot.sent_documents == []
    assert callback.message.sent[-1]["text"] == texts.SEND_KONSPEKT_FILE_MISSING


# =====================================================================
# /delete_my_data (Ю2, PLAN.md)
# =====================================================================


async def _seed_personal_data(user_id: int, tmp_path) -> dict:
    """Заводит по одной строке в каждой таблице, которую трогает Ю2, плюс
    реальные файлы на диске (загруженный КСП, конспект, сгенерированный
    КСП) — чтобы тест проверял настоящее удаление файлов, а не только строк."""
    teacher_id = _create_teacher(user_id)

    uploaded_ksp = tmp_path / "uploaded.docx"
    uploaded_ksp.write_bytes(b"fake uploaded ksp")
    execute(
        "INSERT INTO tasks (id, type, status, payload, telegram_chat_id) "
        "VALUES (?, 'parse_ksp', 'done', ?, ?)",
        (f"task-{user_id}", json.dumps({"teacher_id": teacher_id, "file_paths": [str(uploaded_ksp)]}), user_id),
    )

    execute(
        "INSERT INTO style_profiles (teacher_id, goal_phrasing, stage_structure, "
        "assessment_methods, resources_used, raw_samples_count) VALUES (?, '[]', '[]', '[]', '[]', 2)",
        (teacher_id,),
    )

    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES (?, ?, 'audio', 'расшифровка', 47, 'ru')",
        (f"tr-{user_id}", teacher_id),
    )

    konspekt_docx = tmp_path / "konspekt.docx"
    konspekt_docx.write_bytes(b"fake konspekt")
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, tema, content_json, docx_path) "
        "VALUES (?, ?, ?, 'Тема', '{}', ?)",
        (f"ks-{user_id}", teacher_id, f"tr-{user_id}", str(konspekt_docx)),
    )

    generated_docx = tmp_path / "generated.docx"
    generated_docx.write_bytes(b"fake generated ksp")
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json, docx_path) VALUES (?, ?, '{}', ?)",
        (f"gen-{user_id}", teacher_id, str(generated_docx)),
    )

    return {
        "teacher_id": teacher_id,
        "uploaded_ksp": uploaded_ksp,
        "konspekt_docx": konspekt_docx,
        "generated_docx": generated_docx,
    }


async def test_delete_my_data_shows_counts_and_deletes_nothing_yet(isolated_env, tmp_path):
    paths = await _seed_personal_data(950, tmp_path)

    message = FakeMessage(text="/delete_my_data", user_id=950)
    await cmd_delete_my_data(message)

    text = message.sent[-1]["text"]
    assert "расшифровок: 1" in text
    assert "конспектов: 1" in text
    assert "профиль стиля: есть" in text
    assert "загруженных файлов КСП: 1" in text
    assert "сгенерированных документов: 1" in text
    assert message.sent[-1]["reply_markup"] is not None

    # ничего не удалено без подтверждения
    assert query("SELECT * FROM transcripts WHERE teacher_id = ?", (paths["teacher_id"],))
    assert query("SELECT * FROM konspekty WHERE teacher_id = ?", (paths["teacher_id"],))
    assert query("SELECT * FROM style_profiles WHERE teacher_id = ?", (paths["teacher_id"],))
    assert query("SELECT * FROM generated_ksp WHERE teacher_id = ?", (paths["teacher_id"],))
    assert paths["uploaded_ksp"].exists()
    assert paths["konspekt_docx"].exists()
    assert paths["generated_docx"].exists()


async def test_delete_my_data_nothing_to_delete_says_so(isolated_env):
    _create_teacher(951)
    message = FakeMessage(text="/delete_my_data", user_id=951)
    await cmd_delete_my_data(message)

    assert message.sent[-1]["text"] == texts.DELETE_MY_DATA_NOTHING_TO_DELETE
    assert message.sent[-1]["reply_markup"] is None


async def test_delete_my_data_confirm_deletes_rows_and_files_keeps_teacher(isolated_env, tmp_path):
    paths = await _seed_personal_data(952, tmp_path)
    teacher_id = paths["teacher_id"]

    # usage_daily/incidents — обезличенные, должны пережить удаление
    execute(
        "INSERT INTO usage_daily (telegram_user_id, day, operation, count, tokens) "
        "VALUES (952, '2026-08-31', 'generate_ksp', 1, 100)"
    )
    execute("INSERT INTO incidents (started_at, reason) VALUES ('2026-08-31T00:00:00', 'dns_fail')")

    callback = FakeCallbackQuery(data="delete_my_data_confirm", message=FakeMessage(), user_id=952)
    await delete_my_data_confirmed(callback)

    assert query("SELECT * FROM transcripts WHERE teacher_id = ?", (teacher_id,)) == []
    assert query("SELECT * FROM konspekty WHERE teacher_id = ?", (teacher_id,)) == []
    assert query("SELECT * FROM style_profiles WHERE teacher_id = ?", (teacher_id,)) == []
    assert query("SELECT * FROM generated_ksp WHERE teacher_id = ?", (teacher_id,)) == []
    assert not paths["uploaded_ksp"].exists()
    assert not paths["konspekt_docx"].exists()
    assert not paths["generated_docx"].exists()

    # профиль педагога остаётся
    assert query("SELECT * FROM teachers WHERE id = ?", (teacher_id,))
    # обезличенное не тронуто
    assert query("SELECT * FROM usage_daily WHERE telegram_user_id = 952")
    assert query("SELECT * FROM incidents")

    assert "Готово" in callback.message.sent[-1]["text"]


async def test_delete_my_data_confirm_also_revokes_consent(isolated_env, tmp_path):
    """Э3: удаление данных — это и отзыв согласия, и сброс кэша
    has_given_consent, иначе следующее сообщение прошло бы _consent_gate
    без проверки до перезапуска процесса."""
    from bot.handlers import _consent_given_cache

    paths = await _seed_personal_data(954, tmp_path)
    record_consent(954)
    assert 954 in _consent_given_cache

    callback = FakeCallbackQuery(data="delete_my_data_confirm", message=FakeMessage(), user_id=954)
    await delete_my_data_confirmed(callback)

    assert query("SELECT * FROM consents WHERE telegram_user_id = ?", (954,)) == []
    assert 954 not in _consent_given_cache
    assert has_given_consent(954) is False


# --- Находка 5 AUDIT.md: ветка ученика и данные групп У ---


async def _make_student_in_class(teacher_user_id: int, student_user_id: int, class_name: str = "10 А") -> str:
    """Педагог заводит класс, ученик в него вступает. Возвращает код.

    Педагог создаётся, только если его ещё нет: часть тестов заводит его
    заранее через _seed_personal_data, а telegram_user_id в teachers
    уникален."""
    if not query("SELECT 1 FROM teachers WHERE telegram_user_id = ?", (teacher_user_id,)):
        _create_teacher(teacher_user_id)
    _, code = await _create_class_via_dialog(teacher_user_id, name=class_name)
    execute("INSERT INTO students (telegram_id, name) VALUES (?, 'Ученик Тестовый')", (student_user_id,))
    record_consent(student_user_id)
    state = _state()
    await cmd_join(FakeMessage(text="/join", user_id=student_user_id), state)
    await student_join_code_received(FakeMessage(text=code, user_id=student_user_id), state)
    await student_join_confirmed(
        FakeCallbackQuery(
            data="student_join_confirm", message=FakeMessage(user_id=student_user_id), user_id=student_user_id
        ),
        state,
    )
    return code


async def test_delete_my_data_for_student_shows_own_summary_not_teacher_offer(isolated_env):
    """Ученику обещали эту команду в тексте согласия, а он получал
    «Сначала заведите профиль: /teacher»."""
    await _make_student_in_class(960, 961)

    message = FakeMessage(text="/delete_my_data", user_id=961)
    await cmd_delete_my_data(message)

    text = message.sent[-1]["text"]
    assert text != texts.ERROR_NO_TEACHER_PROFILE
    assert "членство в классах: 1" in text
    assert message.sent[-1]["reply_markup"] is not None
    # без подтверждения не удалено ничего
    assert query("SELECT 1 FROM students WHERE telegram_id = 961")


async def test_delete_my_data_for_student_confirm_removes_student_and_membership(isolated_env):
    await _make_student_in_class(962, 963)
    from bot.handlers import _consent_given_cache

    callback = FakeCallbackQuery(data="delete_my_data_confirm", message=FakeMessage(user_id=963), user_id=963)
    await delete_my_data_confirmed(callback)

    assert query("SELECT 1 FROM students WHERE telegram_id = 963") == []
    assert query("SELECT 1 FROM class_members") == []
    assert query("SELECT 1 FROM consents WHERE telegram_user_id = 963") == []
    assert 963 not in _consent_given_cache
    # класс педагога и сам педагог не тронуты
    assert query("SELECT 1 FROM classes")
    assert query("SELECT 1 FROM teachers WHERE telegram_user_id = 962")


async def test_delete_my_data_for_student_without_data_says_nothing_to_delete(isolated_env):
    record_consent(964)
    execute("INSERT INTO students (telegram_id, name) VALUES (964, 'Ученик')")
    execute("DELETE FROM students WHERE telegram_id = 964")
    message = FakeMessage(text="/delete_my_data", user_id=964)
    await cmd_delete_my_data(message)
    # строки students нет — это уже не ученик, ветка педагога
    assert message.sent[-1]["text"] == texts.ERROR_NO_TEACHER_PROFILE


async def test_delete_my_data_for_teacher_removes_classes_but_keeps_students(isolated_env, tmp_path):
    """Вторая половина Находки 5: блок Ю2 писался до У1 и о таблицах
    classes/class_members не знал вообще."""
    await _seed_personal_data(965, tmp_path)
    await _make_student_in_class(965, 966, class_name="11 В")

    message = FakeMessage(text="/delete_my_data", user_id=965)
    await cmd_delete_my_data(message)
    assert "классов: 1" in message.sent[-1]["text"]

    callback = FakeCallbackQuery(data="delete_my_data_confirm", message=FakeMessage(user_id=965), user_id=965)
    await delete_my_data_confirmed(callback)

    assert query("SELECT 1 FROM classes") == []
    assert query("SELECT 1 FROM class_members") == []
    # сам ученик остаётся — то же правило, что при удалении класса (У2)
    assert query("SELECT 1 FROM students WHERE telegram_id = 966")


async def test_delete_my_data_cancel_deletes_nothing(isolated_env, tmp_path):
    paths = await _seed_personal_data(953, tmp_path)

    callback = FakeCallbackQuery(data="delete_my_data_cancel", message=FakeMessage(), user_id=953)
    await delete_my_data_cancelled(callback)

    assert callback.message.sent[-1]["text"] == texts.DELETE_MY_DATA_CANCELLED
    assert query("SELECT * FROM transcripts WHERE teacher_id = ?", (paths["teacher_id"],))
    assert paths["uploaded_ksp"].exists()


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


async def test_generate_consumes_fresh_template_selected_from_chat_menu(isolated_env):
    """И1: серверный выбор из синей кнопки подхватывается командой /generate."""
    teacher_id = _create_teacher(1)
    template = list_templates(teacher_id)[0]
    execute(
        "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) "
        "VALUES (1, ?, CURRENT_TIMESTAMP)",
        (template["id"],),
    )

    state = _state()
    message = FakeMessage(text="/generate", user_id=1)
    await cmd_generate(message, state)

    data = await state.get_data()
    assert data["template_id"] == template["id"]
    assert template["name"] in message.sent[0]["text"]
    assert message.sent[-1]["text"] == texts.GENERATE_ASK_TOPIC
    assert query("SELECT * FROM template_selections WHERE telegram_user_id = 1") == []


async def test_generate_discards_expired_template_selection(isolated_env):
    """И1: выбор из Mini App не живёт дольше тридцати минут."""
    teacher_id = _create_teacher(1)
    template = list_templates(teacher_id)[0]
    execute(
        "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) "
        "VALUES (1, ?, '2000-01-01 00:00:00')",
        (template["id"],),
    )

    state = _state()
    message = FakeMessage(text="/generate", user_id=1)
    await cmd_generate(message, state)

    data = await state.get_data()
    assert "template_id" not in data
    assert query("SELECT * FROM template_selections WHERE telegram_user_id = 1") == []


async def test_reply_web_app_choice_clears_server_fallback(isolated_env):
    """И1: доставленный sendData не оставляет дублирующий выбор на потом."""
    teacher_id = _create_teacher(1)
    template = list_templates(teacher_id)[0]
    execute(
        "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) "
        "VALUES (1, ?, CURRENT_TIMESTAMP)",
        (template["id"],),
    )

    message = FakeMessage(user_id=1)
    message.web_app_data = type(
        "FakeWebAppData", (), {"data": json.dumps({"template_id": template["id"]})}
    )()
    state = _state()

    await templates_web_app_choice(message, state)

    assert query("SELECT * FROM template_selections WHERE telegram_user_id = 1") == []
    assert (await state.get_data())["template_id"] == template["id"]
    assert template["name"] in message.sent[0]["text"]


async def test_web_app_upload_template_opens_file_dialog(isolated_env):
    """Ш3: Mini App открывает существующий диалог загрузки без команды."""
    _create_teacher(1)
    message = FakeMessage(user_id=1)
    message.web_app_data = type(
        "FakeWebAppData", (), {"data": json.dumps({"action": "upload_template"})}
    )()
    state = _state()

    await templates_web_app_choice(message, state)

    assert await state.get_state() == UploadTemplate.waiting_for_file
    assert (await state.get_data())["teacher_id"] == 1
    assert message.sent[-1]["text"] == texts.UPLOAD_TEMPLATE_PROMPT


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

    async def fail_if_pdf_attempted(*args, **kwargs):
        raise AssertionError("КСП не должен запускать PDF-конвертацию")

    monkeypatch.setattr("bot.handlers._try_send_pdf", fail_if_pdf_attempted)

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


async def test_generate_ksp_task_handler_does_not_attempt_pdf_for_real_docx(isolated_env, monkeypatch):
    """П1: даже существующий .docx КСП уходит без PDF-конвертации."""
    real_docx = str(FIXTURES_DIR / "ksp_sample_1_single_table.docx")

    async def fake_generate_and_save_ksp(**kwargs):
        return {"id": "gen-pdf", "docx_path": real_docx}

    monkeypatch.setattr("bot.handlers.generate_and_save_ksp", fake_generate_and_save_ksp)

    async def fail_if_pdf_attempted(*args, **kwargs):
        raise AssertionError("КСП не должен запускать PDF-конвертацию")

    monkeypatch.setattr("bot.handlers._try_send_pdf", fail_if_pdf_attempted)

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

    assert result["pdf_path"] is None
    assert len(bot.sent_documents) == 1
    assert bot.sent_documents[0]["chat_id"] == 42


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
    record_consent(701)
    message = FakeMessage(text="/start", user_id=701)
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

    Прежняя формулировка КГ («три раза — оказаться на шаге темы») была
    арифметически неверна: между темой и подтверждением лежат ещё код
    цели, фото учебника, доп. опции и выбор шаблона, то есть восемь шагов,
    а не три. По аудиту этапа 2 (находка 9) текст КГ в PLAN_STAGE2.md
    исправлен — расхождения больше нет. Тест идёт назад ровно до темы,
    сколько бы шагов это ни заняло, и проверяет сохранность данных на
    каждом шаге: это строже, чем любое фиксированное число."""
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
    assert data["klass"] == "10"
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
    """Н1: кнопка «Убрать последний файл» убирает последний файл, а не
    переключает шаг (шаг там один). До Н1 это была ветка общего «Назад» —
    теперь у неё своя подпись и свой обработчик, BUTTON_BACK здесь не
    участвует вовсе."""
    teacher_id = _create_teacher(782)
    state = _state()
    await state.set_state(UploadKSP.collecting_files)
    await state.update_data(teacher_id=teacher_id, file_paths=["/tmp/fake1.docx", "/tmp/fake2.docx"])

    message = FakeMessage(user_id=782)
    await upload_ksp_remove_last_file_pressed(message, state)

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
    await upload_ksp_remove_last_file_pressed(message, state)

    assert await state.get_state() == UploadKSP.collecting_files.state
    assert message.sent[-1]["text"] == texts.UPLOAD_KSP_NOTHING_TO_REMOVE


async def test_back_command_synonym_works_same_as_button():
    """/back — синоним кнопки «← Назад» в обычных шаговых диалогах (М3.2).

    В UploadKSP и /konspekt (Н1) синонимом больше не является: там
    «Назад» переименована в «Убрать последний файл/часть» — отдельная
    кнопка со своим обработчиком, не связанным с BUTTON_BACK/Command("back").
    """
    state = _state()
    await cmd_teacher(FakeMessage(text="/teacher", user_id=784), state)
    await teacher_name_received(FakeMessage(text="Иванов И.И.", user_id=784), state)
    assert await state.get_state() == TeacherProfile.waiting_for_subject.state

    message = FakeMessage(text="/back", user_id=784)
    await back_button_pressed(message, state)

    assert await state.get_state() == TeacherProfile.waiting_for_name.state
    data = await state.get_data()
    assert data["name"] == "Иванов И.И."


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
    from core.limits import LimitExceeded, check_count_limit

    _create_teacher(952)
    check_count_limit(952, "generate_ksp")
    check_count_limit(952, "generate_ksp")
    usage = get_usage_today(952)
    assert usage["counts"].get("generate_ksp", 0) == 0


async def test_admin_command_grants_temporary_access_and_deletes_password_message(isolated_env):
    from bot.handlers import cmd_admin
    from core.config import settings as core_settings
    from core.limits import LimitExceeded, check_count_limit

    original_password = core_settings.admin_password
    object.__setattr__(core_settings, "admin_password", "проверочный-пароль")
    try:
        message = FakeMessage(text="/admin проверочный-пароль", user_id=960, chat_id=960)
        bot = FakeBot()
        await cmd_admin(message, bot)

        assert bot.deleted_messages == [(960, 1)]
        assert "сняты" in message.sent[-1]["text"]
        for _ in range(5):
            record_usage(960, "generate_ksp", count_delta=1)
        check_count_limit(960, "generate_ksp")
    finally:
        object.__setattr__(core_settings, "admin_password", original_password)


async def test_admin_command_rejects_wrong_password_without_bypass(isolated_env):
    from bot.handlers import cmd_admin
    from core.config import settings as core_settings
    from core.limits import LimitExceeded, check_count_limit

    original_password = core_settings.admin_password
    object.__setattr__(core_settings, "admin_password", "верный")
    try:
        message = FakeMessage(text="/admin неверный", user_id=961, chat_id=961)
        bot = FakeBot()
        await cmd_admin(message, bot)

        assert bot.deleted_messages == [(961, 1)]
        assert message.sent[-1]["text"] == texts.ADMIN_DENIED
        for _ in range(5):
            record_usage(961, "generate_ksp", count_delta=1)
        with pytest.raises(LimitExceeded):
            check_count_limit(961, "generate_ksp")
    finally:
        object.__setattr__(core_settings, "admin_password", original_password)


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
    message = FakeMessage(text="/konspekt", user_id=910)
    await cmd_konspekt(message, state)

    assert await state.get_state() == Konspekt.choosing_mode.state
    buttons = [button.text for row in message.sent[-1]["reply_markup"].keyboard for button in row]
    assert texts.KONSPEKT_MODE_STUDENT_BUTTON in buttons
    assert texts.KONSPEKT_MODE_TEACHER_BUTTON in buttons


@pytest.mark.parametrize(
    ("button_text", "expected_mode"),
    [
        (texts.KONSPEKT_MODE_STUDENT_BUTTON, "student"),
        (texts.KONSPEKT_MODE_TEACHER_BUTTON, "teacher"),
    ],
)
async def test_konspekt_mode_choice_is_saved_before_audio(isolated_env, button_text, expected_mode):
    from bot.handlers import konspekt_mode_chosen

    _create_teacher(909)
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=909), state)

    message = FakeMessage(text=button_text, user_id=909)
    await konspekt_mode_chosen(message, state)

    assert await state.get_state() == Konspekt.collecting_audio.state
    assert (await state.get_data())["mode"] == expected_mode
    assert "Пришлите запись урока" in message.sent[-1]["text"]


async def test_konspekt_without_profile_shows_error(isolated_env):
    state = _state()
    await cmd_konspekt(FakeMessage(text="/konspekt", user_id=999999), state)
    assert await state.get_state() is None


async def test_konspekt_accepts_voice_message(isolated_env):
    teacher_id = _create_teacher(911)
    state = _state()
    await _start_konspekt(state, 911)

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
    await _start_konspekt(state, 912)

    message = FakeMessage(user_id=912, audio=FakeAudio(file_name="urok.mp3"))
    bot = FakeBot()
    await konspekt_audio_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == 1
    assert data["audio_paths"][0].endswith(".mp3")


async def test_konspekt_rejects_non_audio_document(isolated_env):
    _create_teacher(913)
    state = _state()
    await _start_konspekt(state, 913)

    message = FakeMessage(user_id=913, document=FakeDocument(file_name="report.docx", mime_type="application/msword"))
    bot = FakeBot()
    await konspekt_document_received(message, state, bot)

    data = await state.get_data()
    assert data["audio_paths"] == []
    assert "не похоже на аудио" in message.sent[-1]["text"]


async def test_konspekt_accepts_audio_document_with_correct_mime(isolated_env):
    _create_teacher(914)
    state = _state()
    await _start_konspekt(state, 914)

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
    await _start_konspekt(state, 915)

    bot = FakeBot()
    # столько частей, сколько разрешено сейчас, — не жёсткое число, иначе
    # тест ломается при каждом изменении лимита вместо того, чтобы его
    # проверять (лимит менялся: было 10, стало 2)
    for i in range(MAX_KONSPEKT_PARTS):
        message = FakeMessage(user_id=915, voice=FakeVoice(duration=60 + i))
        await konspekt_voice_received(message, state, bot)

    data = await state.get_data()
    assert len(data["audio_paths"]) == MAX_KONSPEKT_PARTS
    assert data["audio_durations"] == [60 + i for i in range(MAX_KONSPEKT_PARTS)]


async def test_konspekt_max_parts_limit(isolated_env):
    _create_teacher(916)
    state = _state()
    await _start_konspekt(state, 916)

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
    await _start_konspekt(state, 917)

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
    await _start_konspekt(state, 918)

    message = FakeMessage(text="/done", user_id=918)
    await konspekt_done(message, state)

    assert await state.get_state() == Konspekt.collecting_audio.state
    assert "нет ни одной части" in message.sent[-1]["text"]


async def test_konspekt_done_enqueues_transcribe_task(isolated_env):
    _create_teacher(919)
    state = _state()
    await _start_konspekt(state, 919)

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
    await _start_konspekt(state, 920)

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
# К3.2 — задача очереди 'transcribe'
# =====================================================================


async def test_transcribe_handler_creates_transcript_and_notifies(isolated_env, fake_xai_transcriber):
    """Обработчик сохраняет ответ xAI и уведомляет учителя."""
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


async def test_transcribe_handler_deletes_audio_after_success(isolated_env, fake_xai_transcriber):
    """К2.4 КГ: после успешной транскрипции на диске не остаётся ни
    исходника; за него отвечает сам обработчик."""
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


async def test_transcribe_handler_deletes_audio_even_on_failure(isolated_env, monkeypatch):
    """К2.4 КГ: и при ПРОВАЛЕ транскрипции — тоже ноль мусора на диске."""
    teacher_id = _create_teacher(932)
    broken_audio = settings.uploads_dir / "broken.m4a"
    broken_audio.write_text("это не аудиофайл, а текст")

    async def failed_transcribe(_path):
        raise TranscriptionError("xAI отклонил повреждённый файл")

    monkeypatch.setattr("bot.handlers.transcribe", failed_transcribe)

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


async def test_transcribe_handler_concatenates_multiple_parts_in_order(isolated_env, fake_xai_transcriber):
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
    assert text.count("Кинематика") == 2
    assert rows[0]["duration_seconds"] == 94


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
    assert rows[0]["mode"] == "student"
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


async def test_transcribe_handler_enqueues_generate_konspekt_task(isolated_env, fake_xai_transcriber):
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


async def _seed_sverka_lesson(teacher_user_id: int, student_user_id: int, transcript_text: str) -> dict:
    """Заводит учителя, класс, ученика-члена класса и расшифровку урока —
    минимальный набор, нужный make_sverka_handler."""
    teacher_id = _create_teacher(teacher_user_id)
    class_id = execute(
        "INSERT INTO classes (teacher_id, name, invite_code) VALUES (?, '10 А', ?)",
        (teacher_id, f"CODE{teacher_user_id}"),
    )
    student_id = execute(
        "INSERT INTO students (telegram_id, name) VALUES (?, 'Ученик Тестов')", (student_user_id,)
    )
    execute("INSERT INTO class_members (class_id, student_id) VALUES (?, ?)", (class_id, student_id))
    transcript_id = f"tr-sverka-{teacher_user_id}"
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES (?, ?, 'audio', ?, 47, 'ru')",
        (transcript_id, teacher_id, transcript_text),
    )
    return {"teacher_id": teacher_id, "class_id": class_id, "student_id": student_id, "transcript_id": transcript_id}


# Расшифровка настоящего урока — тысячи символов; в тестах ниже она
# должна быть правдоподобной длины, иначе два коротких пункта пропусков
# перевешивают её и срабатывает потолок выдачи (Находка 7 AUDIT.md).
LONG_TRANSCRIPT = "расшифровка урока про импульс. " * 60


async def test_sverka_handler_sends_missing_items_and_records_usage(isolated_env, monkeypatch, tmp_path):
    seed = await _seed_sverka_lesson(950, 951, LONG_TRANSCRIPT)
    photo_path = tmp_path / "notebook.jpg"
    photo_path.write_bytes(b"fake photo bytes")

    async def fake_ocr(image_bytes, image_mime, llm_client=None):
        assert image_bytes == b"fake photo bytes"
        return "конспект ученика: импульс это ..."

    async def fake_compare(transcript_text, notebook_text, llm_client=None):
        assert transcript_text == LONG_TRANSCRIPT
        assert notebook_text == "конспект ученика: импульс это ..."
        return ["пропущен вывод формулы сохранения импульса", "не записано домашнее задание"]

    monkeypatch.setattr("bot.handlers.recognize_textbook_page", fake_ocr)
    monkeypatch.setattr("bot.handlers.compare_notebook_to_transcript", fake_compare)

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    task = {
        "id": "sv1",
        "type": "sverka_tetradi",
        "telegram_chat_id": 951,
        "retries": 0,
        "payload": {
            "student_id": seed["student_id"],
            "transcript_id": seed["transcript_id"],
            "photo_path": str(photo_path),
        },
    }
    result = await handler(task)

    assert result["missing_items"] == ["пропущен вывод формулы сохранения импульса", "не записано домашнее задание"]
    assert len(bot.sent_messages) == 1
    sent_text = bot.sent_messages[0][1]
    assert "пропущен вывод формулы сохранения импульса" in sent_text
    assert "не записано домашнее задание" in sent_text

    # У4, ловушка: фото удалено сразу после сверки.
    assert not photo_path.exists()

    # Счётчик ученика (У3) — записан.
    from core.limits import get_student_usage_today

    assert get_student_usage_today(951, db_path=None) == 1


async def _run_sverka_handler(seed, missing_items, tmp_path, monkeypatch, chat_id):
    photo_path = tmp_path / f"notebook-{chat_id}.jpg"
    photo_path.write_bytes(b"fake photo bytes")
    monkeypatch.setattr(
        "bot.handlers.recognize_textbook_page",
        lambda *a, **k: _async_return("Конспект ученика: импульс, формула p = m*v, задача."),
    )
    monkeypatch.setattr(
        "bot.handlers.compare_notebook_to_transcript", lambda *a, **k: _async_return(missing_items)
    )
    bot = FakeBot()
    handler = make_sverka_handler(bot)
    await handler(
        {
            "id": f"sv-{chat_id}",
            "type": "sverka_tetradi",
            "telegram_chat_id": chat_id,
            "retries": 0,
            "payload": {
                "student_id": seed["student_id"],
                "transcript_id": seed["transcript_id"],
                "photo_path": str(photo_path),
            },
        }
    )
    return bot


async def test_sverka_handler_refuses_to_send_a_retelling_of_the_lesson(
    isolated_env, monkeypatch, tmp_path
):
    """Находка 7 AUDIT.md: гарантия «ученик не получает полную
    расшифровку» (MASTER.md 0.9 п.3) держалась только на фразе в
    промпте. Здесь модель нарочно возвращает пересказ урока — код обязан
    его не отправить."""
    from core.konspekt_compare import MAX_MISSING_SHARE_OF_TRANSCRIPT

    transcript = "речь учителя на уроке. " * 100
    seed = await _seed_sverka_lesson(954, 955, transcript)
    retelling = [transcript[:2000]]
    assert len(retelling[0]) > len(transcript) * MAX_MISSING_SHARE_OF_TRANSCRIPT

    bot = await _run_sverka_handler(seed, retelling, tmp_path, monkeypatch, 955)

    assert bot.sent_messages[0][1] == texts.SVERKA_TOO_MUCH_MISSING
    assert transcript[:200] not in bot.sent_messages[0][1]


async def test_sverka_handler_refuses_when_items_are_too_many(isolated_env, monkeypatch, tmp_path):
    """Второе условие потолка: длинный урок можно пересказать множеством
    коротких пунктов, доля от расшифровки при этом останется небольшой."""
    from core.konspekt_compare import MAX_MISSING_ITEMS

    seed = await _seed_sverka_lesson(958, 959, "речь учителя на уроке. " * 400)
    many = [f"пункт {i}" for i in range(MAX_MISSING_ITEMS + 1)]

    bot = await _run_sverka_handler(seed, many, tmp_path, monkeypatch, 959)

    assert bot.sent_messages[0][1] == texts.SVERKA_TOO_MUCH_MISSING


async def test_sverka_handler_sends_ordinary_result_below_the_ceiling(
    isolated_env, monkeypatch, tmp_path
):
    """Замер аудита на настоящем уроке — 13 пунктов и 7,1 % от
    расшифровки: обычная честная сверка потолком задеваться не должна."""
    transcript = "речь учителя на уроке. " * 700
    seed = await _seed_sverka_lesson(968, 969, transcript)
    realistic = [f"Отсутствует пункт номер {i} из разобранного на уроке" for i in range(13)]

    bot = await _run_sverka_handler(seed, realistic, tmp_path, monkeypatch, 969)

    assert bot.sent_messages[0][1].startswith(texts.SVERKA_RESULT_HEADER)
    assert "Отсутствует пункт номер 0" in bot.sent_messages[0][1]


async def test_sverka_handler_unreadable_notebook_does_not_say_everything_is_fine(
    isolated_env, monkeypatch, tmp_path
):
    """Находка 6 AUDIT.md: чистый лист получал подтверждение, что всё в
    порядке. Модель сравнения при этом не должна вызываться вовсе."""
    seed = await _seed_sverka_lesson(956, 957, "расшифровка урока")
    photo_path = tmp_path / "notebook.jpg"
    photo_path.write_bytes(b"fake photo bytes")

    monkeypatch.setattr("bot.handlers.recognize_textbook_page", lambda *a, **k: _async_return("-"))

    def _forbidden(*args, **kwargs):
        raise AssertionError("сравнение не должно вызываться на нечитаемой тетради")

    monkeypatch.setattr("bot.handlers.compare_notebook_to_transcript", _forbidden)

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    result = await handler(
        {
            "id": "sv3",
            "type": "sverka_tetradi",
            "telegram_chat_id": 957,
            "retries": 0,
            "payload": {
                "student_id": seed["student_id"],
                "transcript_id": seed["transcript_id"],
                "photo_path": str(photo_path),
            },
        }
    )

    assert bot.sent_messages[0][1] == texts.SVERKA_NOTEBOOK_UNREADABLE
    assert bot.sent_messages[0][1] != texts.SVERKA_NOTHING_MISSING
    assert result["notebook_unreadable"] is True
    # фото удаляется и на этой ветке тоже (У4, ловушка / грабля 2.8)
    assert not photo_path.exists()


async def test_sverka_handler_nothing_missing_sends_positive_message(isolated_env, monkeypatch, tmp_path):
    seed = await _seed_sverka_lesson(952, 953, "расшифровка")
    photo_path = tmp_path / "notebook.jpg"
    photo_path.write_bytes(b"fake photo bytes")

    # Текст тетради заведомо длиннее MIN_NOTEBOOK_TEXT_LENGTH: после
    # Находки 6 короткая строка означала бы «не разобрать», а этот тест
    # про другое — про читаемую тетрадь без пропусков.
    monkeypatch.setattr(
        "bot.handlers.recognize_textbook_page",
        lambda *a, **k: _async_return("Конспект: скорость, формула v = s / t, разобрана задача."),
    )
    monkeypatch.setattr("bot.handlers.compare_notebook_to_transcript", lambda *a, **k: _async_return([]))

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    task = {
        "id": "sv2",
        "type": "sverka_tetradi",
        "telegram_chat_id": 953,
        "retries": 0,
        "payload": {
            "student_id": seed["student_id"],
            "transcript_id": seed["transcript_id"],
            "photo_path": str(photo_path),
        },
    }
    result = await handler(task)

    assert result["missing_items"] == []
    assert bot.sent_messages[0][1] == texts.SVERKA_NOTHING_MISSING
    assert not photo_path.exists()


async def test_sverka_handler_deletes_photo_even_when_ocr_fails(isolated_env, monkeypatch, tmp_path):
    """У4, ловушка (та же, что у аудио): фото удаляется и при провале, не
    только при успехе."""
    from core.textbook_ocr import TextbookOCRError

    seed = await _seed_sverka_lesson(954, 955, "расшифровка")
    photo_path = tmp_path / "notebook.jpg"
    photo_path.write_bytes(b"fake photo bytes")

    async def failing_ocr(image_bytes, image_mime, llm_client=None):
        raise TextbookOCRError("не распозналось")

    monkeypatch.setattr("bot.handlers.recognize_textbook_page", failing_ocr)

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    task = {
        "id": "sv3",
        "type": "sverka_tetradi",
        "telegram_chat_id": 955,
        "retries": 0,
        "payload": {
            "student_id": seed["student_id"],
            "transcript_id": seed["transcript_id"],
            "photo_path": str(photo_path),
        },
    }
    with pytest.raises(TextbookOCRError):
        await handler(task)

    assert not photo_path.exists()
    assert bot.sent_messages == []


async def test_sverka_handler_missing_transcript_raises_and_deletes_photo(isolated_env, tmp_path):
    from core.konspekt_compare import KonspektCompareError

    photo_path = tmp_path / "notebook.jpg"
    photo_path.write_bytes(b"fake photo bytes")

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    task = {
        "id": "sv4",
        "type": "sverka_tetradi",
        "telegram_chat_id": 956,
        "retries": 0,
        "payload": {"student_id": 1, "transcript_id": "нет-такого-id", "photo_path": str(photo_path)},
    }
    with pytest.raises(KonspektCompareError):
        await handler(task)

    assert not photo_path.exists()


async def test_sverka_handler_does_not_retry_after_photo_already_deleted(isolated_env, monkeypatch):
    """Регрессия: фото удаляется в finally и при провале тоже (та же
    ловушка 2.8, что у аудио) — значит вторая попытка (retries > 0) НЕ
    имеет права заново пытаться читать файл, которого уже нет. Раньше
    (до фикса, по образцу TRANSCRIBE_REAL_ATTEMPT_LIMIT в этом же файле)
    повторная попытка падала бы FileNotFoundError вместо честного
    SVERKA_RETRY_DISABLED — при этом даже не дойдя до записи в лог,
    почему именно она не удалась."""
    seed = await _seed_sverka_lesson(957, 958, "расшифровка")

    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("OCR не должен вызываться повторно — фото уже удалено")

    monkeypatch.setattr("bot.handlers.recognize_textbook_page", should_not_be_called)

    bot = FakeBot()
    handler = make_sverka_handler(bot)
    # Фото физически не существует — как было бы после первой попытки,
    # чей finally уже его удалил.
    missing_photo_path = "/tmp/этого-файла-точно-нет-nonexistent-sverka.jpg"
    task = {
        "id": "sv5",
        "type": "sverka_tetradi",
        "telegram_chat_id": 958,
        "retries": 1,  # вторая попытка
        "payload": {
            "student_id": seed["student_id"],
            "transcript_id": seed["transcript_id"],
            "photo_path": missing_photo_path,
        },
    }
    with pytest.raises(KonspektCompareError, match="повторные попытки отключены"):
        await handler(task)


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner()


async def test_same_audio_has_student_and_teacher_paths_without_teacher_llm(isolated_env, monkeypatch):
    """К0: один файл в ученическом режиме продолжает цепочку с LLM, а в
    учительском заканчивается сохранённой расшифровкой и кнопкой КСП."""
    student_id = _create_teacher(944)
    teacher_id = _create_teacher(945)
    student_audio = settings.uploads_dir / "student.m4a"
    teacher_audio = settings.uploads_dir / "teacher.m4a"
    student_audio.write_bytes(b"same audio")
    teacher_audio.write_bytes(b"same audio")

    async def fake_probe(_path):
        return 47

    async def fake_transcribe(_path):
        return {"text": "Дословная расшифровка одного урока", "duration_seconds": 47}

    monkeypatch.setattr("bot.handlers._safe_probe", fake_probe)
    monkeypatch.setattr("bot.handlers.transcribe", fake_transcribe)

    class ForbiddenLLMClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("учительский путь не должен создавать LLMClient")

    monkeypatch.setattr("bot.handlers.LLMClient", ForbiddenLLMClient)

    bot = FakeBot()
    handler = make_transcribe_handler(bot)
    student_result = await handler(
        {
            "id": "tr-student",
            "type": "transcribe",
            "telegram_chat_id": 944,
            "retries": 0,
            "payload": {
                "teacher_id": student_id,
                "audio_paths": [str(student_audio)],
                "mode": "student",
            },
        }
    )
    teacher_result = await handler(
        {
            "id": "tr-teacher",
            "type": "transcribe",
            "telegram_chat_id": 945,
            "retries": 0,
            "payload": {
                "teacher_id": teacher_id,
                "audio_paths": [str(teacher_audio)],
                "mode": "teacher",
            },
        }
    )

    transcripts = {
        row["id"]: row for row in query("SELECT id, mode, text FROM transcripts ORDER BY id")
    }
    assert transcripts[student_result["transcript_id"]]["mode"] == "student"
    assert transcripts[teacher_result["transcript_id"]]["mode"] == "teacher"

    tasks = query("SELECT payload FROM tasks WHERE type = 'generate_konspekt'")
    assert len(tasks) == 1
    assert json.loads(tasks[0]["payload"])["transcript_id"] == student_result["transcript_id"]

    teacher_rows = query(
        "SELECT id, mode, content_json FROM konspekty WHERE transcript_id = ?",
        (teacher_result["transcript_id"],),
    )
    assert len(teacher_rows) == 1
    assert teacher_rows[0]["mode"] == "teacher"
    assert json.loads(teacher_rows[0]["content_json"])["transcript_text"] == "Дословная расшифровка одного урока"

    teacher_messages = [item for item in bot.sent_messages if item[0] == 945]
    assert any("Дословная расшифровка одного урока" in item[1] for item in teacher_messages)
    final_markup = teacher_messages[-1][2]
    assert final_markup is not None
    callback_data = final_markup.inline_keyboard[0][0].callback_data
    assert callback_data.startswith("ksp_from_konspekt:")

    state = _state()
    callback = FakeCallbackQuery(
        data=callback_data,
        message=FakeMessage(user_id=945, chat_id=945),
        user_id=945,
    )
    await ksp_from_konspekt_pressed(callback, state)
    assert await state.get_state() == Generate.waiting_for_objective_code.state
    assert callback.answered[-1]["show_alert"] is False


async def test_teacher_transcript_without_line_breaks_is_split_for_telegram(isolated_env, monkeypatch):
    teacher_id = _create_teacher(947)
    audio_path = settings.uploads_dir / "long-teacher.m4a"
    audio_path.write_bytes(b"audio")
    long_transcript = "д" * 8500

    async def fake_probe(_path):
        return 120

    async def fake_transcribe(_path):
        return {"text": long_transcript, "duration_seconds": 120}

    monkeypatch.setattr("bot.handlers._safe_probe", fake_probe)
    monkeypatch.setattr("bot.handlers.transcribe", fake_transcribe)

    bot = FakeBot()
    await make_transcribe_handler(bot)(
        {
            "id": "tr-long-teacher",
            "type": "transcribe",
            "telegram_chat_id": 947,
            "retries": 0,
            "payload": {
                "teacher_id": teacher_id,
                "audio_paths": [str(audio_path)],
                "mode": "teacher",
            },
        }
    )

    teacher_messages = [item for item in bot.sent_messages if item[0] == 947][1:]
    assert len(teacher_messages) >= 3
    assert all(len(item[1]) <= 4000 for item in teacher_messages)
    assert teacher_messages[-1][2] is not None


async def test_konspekt_done_carries_selected_mode_to_queue(isolated_env):
    teacher_id = _create_teacher(946)
    state = _state()
    audio_path = settings.uploads_dir / "teacher-mode.m4a"
    audio_path.write_bytes(b"audio")
    await state.set_state(Konspekt.collecting_audio)
    await state.update_data(
        teacher_id=teacher_id,
        mode="teacher",
        audio_paths=[str(audio_path)],
        audio_durations=[47],
    )

    await konspekt_done(FakeMessage(text="/done", user_id=946, chat_id=946), state)

    task = query("SELECT payload FROM tasks WHERE type = 'transcribe'")[0]
    assert json.loads(task["payload"])["mode"] == "teacher"


def test_format_konspekt_text_includes_all_sections():
    text = format_konspekt_text(_SAMPLE_KONSPEKT_CONTENT)
    assert text.startswith("📝 Конспект урока")
    assert "Конспект для ученика:" in text
    assert "Различать путь и перемещение" in text
    assert "s = v*t — путь при равномерном движении" in text
    assert "перемещение: вектор из начальной точки в конечную" in text
    assert "Чем отличается путь от перемещения?" in text
    # пустые секции (primery, domashnee_zadanie) не оставляют "хвостов" в тексте
    assert "Примеры:" not in text
    assert "Домашнее задание:" not in text


def test_format_konspekt_text_shows_replics_before_student_summary():
    content = {
        "opornye_repliki": ["Здравствуйте, начинаем урок.", "Откройте тетради."],
        "konspekt_uchenika": dict(_SAMPLE_KONSPEKT_CONTENT),
    }

    text = format_konspekt_text(content)

    assert "Опорные реплики учителя:" in text
    assert "Конспект для ученика:" in text
    assert texts.KONSPEKT_REPLICAS_NOTICE in text
    assert text.index("Здравствуйте, начинаем урок.") < text.index("Конспект для ученика:")


def test_format_konspekt_text_empty_celi_says_so_instead_of_hiding_section():
    """Аудит этапа 2, находка 1: пустые цели — законный результат, а не
    недоделка. В тексте для чата раздел остаётся и прямо это говорит."""
    content = {**_SAMPLE_KONSPEKT_CONTENT, "celi": []}
    text = format_konspekt_text(content)
    assert "Цели:" in text
    assert CELI_NOT_STATED_NOTE in text


def test_format_konspekt_text_with_celi_has_no_honest_note():
    """Граница правки: есть цели — честной строки быть не должно."""
    text = format_konspekt_text(_SAMPLE_KONSPEKT_CONTENT)
    assert CELI_NOT_STATED_NOTE not in text


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


async def test_ksp_from_teacher_transcript_passes_raw_recording_to_ksp(isolated_env):
    """К2: учительский режим передаёт в КСП запись как первоисточник, без
    попытки отформатировать её в ученический конспект."""
    teacher_id = _create_teacher(957)
    transcript_id = "tr-957"
    konspekt_id = "ksp-src-957"
    transcript_text = "Откройте тетради. Сегодня разберём второй закон Ньютона."
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, mode, text, duration_seconds, language) "
        "VALUES (?, ?, 'audio', 'teacher', ?, 3700, 'ru')",
        (transcript_id, teacher_id, transcript_text),
    )
    execute(
        "INSERT INTO konspekty (id, teacher_id, transcript_id, mode, tema, content_json) "
        "VALUES (?, ?, ?, 'teacher', ?, ?)",
        (konspekt_id, teacher_id, transcript_id, "Расшифровка урока", json.dumps({
            "tema": "Расшифровка урока", "transcript_text": transcript_text,
        }, ensure_ascii=False)),
    )

    state = _state()
    message = FakeMessage(user_id=957, chat_id=957)
    callback = FakeCallbackQuery(data=f"ksp_from_konspekt:{konspekt_id}", message=message, user_id=957)
    await ksp_from_konspekt_pressed(callback, state)

    data = await state.get_data()
    assert data["konspekt_text"] == transcript_text
    assert any("дольше часа" in item["text"] for item in message.sent)
    assert callback.answered


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


# =====================================================================
# К6 — конспект файлом (.docx/.pdf)
# =====================================================================


async def test_konspekt_handler_sends_docx_and_pdf(isolated_env, monkeypatch, tmp_path):
    """К6/П1: после текста в чат уходят .docx и .pdf конспекта."""
    teacher_id = _create_teacher(960)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-960', ?, 'audio', 'расшифровка урока про кинематику', 47, 'ru')",
        (teacher_id,),
    )

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return dict(_SAMPLE_KONSPEKT_CONTENT)

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    def fake_convert_docx_to_pdf(docx_path):
        pdf_path = tmp_path / "konspekt.pdf"
        pdf_path.write_bytes(b"fake pdf")
        return pdf_path

    monkeypatch.setattr("bot.handlers.convert_docx_to_pdf", fake_convert_docx_to_pdf)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k5",
        "type": "generate_konspekt",
        "telegram_chat_id": 960,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-960"},
    }
    result = await handler(task)

    assert result["docx_path"].endswith(".docx")
    assert Path(result["docx_path"]).exists()

    assert len(bot.sent_documents) == 2
    docx_sent, pdf_sent = bot.sent_documents
    assert docx_sent["chat_id"] == 960
    assert _SAMPLE_KONSPEKT_CONTENT["tema"] in docx_sent["caption"]
    assert pdf_sent["chat_id"] == 960
    assert pdf_sent["caption"] == texts.KONSPEKT_PDF_CAPTION

    # текстовые сообщения (К4/К5) никуда не делись
    assert len(bot.sent_messages) == 1
    assert _SAMPLE_KONSPEKT_CONTENT["tema"] in bot.sent_messages[0][1]


async def test_konspekt_handler_survives_optional_pdf_failure(isolated_env, monkeypatch):
    """П1: ошибка необязательного PDF не отменяет готовый конспект."""
    teacher_id = _create_teacher(965)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-965', ?, 'audio', 'расшифровка урока', 47, 'ru')",
        (teacher_id,),
    )

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return dict(_SAMPLE_KONSPEKT_CONTENT)

    def fail_pdf_conversion(docx_path):
        raise PdfExportError("LibreOffice недоступен")

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)
    monkeypatch.setattr("bot.handlers.convert_docx_to_pdf", fail_pdf_conversion)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k5-pdf-failed",
        "type": "generate_konspekt",
        "telegram_chat_id": 965,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-965"},
    }

    result = await handler(task)

    assert result["docx_path"].endswith(".docx")
    assert len(bot.sent_documents) == 1
    assert len(bot.sent_messages) == 1


async def test_konspekt_handler_stores_docx_path_in_db(isolated_env, monkeypatch):
    teacher_id = _create_teacher(961)
    execute(
        "INSERT INTO transcripts (id, teacher_id, source, text, duration_seconds, language) "
        "VALUES ('tr-961', ?, 'audio', 'расшифровка', 47, 'ru')",
        (teacher_id,),
    )

    async def fake_generate_konspekt(transcript_text, *, llm_client=None, **kwargs):
        return dict(_SAMPLE_KONSPEKT_CONTENT)

    monkeypatch.setattr("bot.handlers.generate_konspekt", fake_generate_konspekt)

    bot = FakeBot()
    handler = make_konspekt_handler(bot)
    task = {
        "id": "k6",
        "type": "generate_konspekt",
        "telegram_chat_id": 961,
        "retries": 0,
        "payload": {"teacher_id": teacher_id, "transcript_id": "tr-961"},
    }
    result = await handler(task)

    rows = query("SELECT docx_path FROM konspekty WHERE id = ?", (result["konspekt_id"],))
    assert rows[0]["docx_path"] == result["docx_path"]
    assert Path(rows[0]["docx_path"]).exists()


# =====================================================================
# Аудит этапа 2, находка 2 — брошенное аудио не остаётся на диске
# =====================================================================


async def _konspekt_with_parts(telegram_id: int, parts: int | None = None):
    """Доводит /konspekt до состояния «принято N частей» и возвращает
    (state, список путей на диске). По умолчанию берёт максимум, который
    разрешён сейчас, — жёсткое число здесь ломалось бы при каждом
    изменении лимита."""
    parts = MAX_KONSPEKT_PARTS if parts is None else parts
    _create_teacher(telegram_id)
    state = _state()
    await _start_konspekt(state, telegram_id)
    bot = FakeBot()
    for _ in range(parts):
        await konspekt_voice_received(
            FakeMessage(voice=FakeVoice(), user_id=telegram_id), state, bot
        )
    paths = [Path(p) for p in (await state.get_data())["audio_paths"]]
    assert all(p.exists() for p in paths)
    return state, paths


async def test_cancel_deletes_collected_audio_parts(isolated_env):
    """/cancel посреди сбора записи не должен оставлять части на диске:
    до правки пути терялись вместе с данными FSM, и файлы (до 20 МБ
    каждый) не удалял уже никто и никогда."""
    state, paths = await _konspekt_with_parts(970)

    await cmd_cancel(FakeMessage(text="/cancel", user_id=970), state)

    assert not any(p.exists() for p in paths)
    assert list(settings.uploads_dir.iterdir()) == []


async def test_menu_button_deletes_collected_audio_parts(isolated_env):
    """Тот же обрыв, но через кнопку постоянного меню (М2.1)."""
    state, paths = await _konspekt_with_parts(971)

    await menu_button_pressed(
        FakeMessage(text=texts.MENU_BUTTON_DASHBOARD, user_id=971), state
    )

    assert not any(p.exists() for p in paths)


async def test_back_in_konspekt_removes_only_last_part(isolated_env):
    """Н1 для состояния из К2: кнопка «Убрать последнюю часть» убирает
    последнюю присланную часть, а не выходит из диалога. Раньше это была
    ветка общего «Назад»; теперь своя подпись и свой обработчик, не
    связанный с BUTTON_BACK."""
    state, paths = await _konspekt_with_parts(972)
    collected = len(paths)
    assert collected >= 2, "тесту нужно минимум две части, чтобы «последняя» имела смысл"

    message = FakeMessage(text=texts.BUTTON_REMOVE_LAST_PART, user_id=972)
    await konspekt_remove_last_part_pressed(message, state)

    assert not paths[-1].exists()          # последняя убрана с диска
    assert all(p.exists() for p in paths[:-1])  # остальные на месте
    assert await state.get_state() == Konspekt.collecting_audio.state  # из диалога не вышли
    assert len((await state.get_data())["audio_paths"]) == collected - 1
    assert f"Осталось частей: {collected - 1}" in message.sent[-1]["text"]


async def test_back_in_konspekt_without_parts_says_nothing_to_remove(isolated_env):
    """Пустой список частей — не ошибка и не выход из диалога, тот же
    приём, что у UploadKSP."""
    _create_teacher(973)
    state = _state()
    await _start_konspekt(state, 973)

    message = FakeMessage(text=texts.BUTTON_REMOVE_LAST_PART, user_id=973)
    await konspekt_remove_last_part_pressed(message, state)

    assert message.sent[-1]["text"] == texts.KONSPEKT_NOTHING_TO_REMOVE
    assert await state.get_state() == Konspekt.collecting_audio.state


async def test_done_does_not_delete_audio_parts(isolated_env):
    """Граница правки: штатный путь /done файлы НЕ трогает — их заберёт и
    удалит задача очереди (К2.4). Удалить их здесь значило бы сломать
    расшифровку."""
    state, paths = await _konspekt_with_parts(974)

    await konspekt_done(FakeMessage(text="/done", user_id=974, chat_id=974), state)

    assert all(p.exists() for p in paths)
    assert len(query("SELECT * FROM tasks WHERE type = 'transcribe'")) == 1


async def test_recovered_transcribe_task_refuses_second_real_attempt(isolated_env):
    """Аудит этапа 2, находка 8, смысл целиком. Восстановление зависшей
    задачи расходует попытку, поэтому обработчик транскрипции узнаёт
    повторный заход и сразу отказывает понятным текстом. До правки он
    считал заход первым: слал «Начал расшифровку», а потом падал на
    файлах, которые прошлая попытка уже удалила."""
    teacher_id = _create_teacher(975)
    gone = settings.uploads_dir / "уже-удалён.m4a"  # первая попытка удалила его в finally

    task_id = enqueue(
        "transcribe", {"teacher_id": teacher_id, "audio_paths": [str(gone)]}, chat_id=975
    )
    execute(
        "UPDATE tasks SET status = 'processing', updated_at = datetime('now', '-90 minutes') WHERE id = ?",
        (task_id,),
    )
    assert recover_stuck_tasks() == 1

    # Следствие той же правки: у восстановленной задачи retries=1, поэтому
    # claim_next выдерживает перед повтором обычную растущую паузу (10с).
    # В тесте её не ждём, а состариваем отметку — как и в тестах очереди.
    execute("UPDATE tasks SET updated_at = datetime('now', '-60 seconds') WHERE id = ?", (task_id,))

    claimed = dict(claim_next())
    claimed["payload"] = json.loads(claimed["payload"])
    assert claimed["retries"] == 1, "восстановление не засчитало израсходованную попытку"

    bot = FakeBot()
    with pytest.raises(TranscriptionError):
        await make_transcribe_handler(bot)(claimed)

    assert bot.sent_messages == [], "ушло «Начал расшифровку» — значит повторный заход не распознан"


# =====================================================================
# Аудит этапа 2, находка 6 — команда /menu и порядок BOT_COMMANDS
# =====================================================================


def test_bot_commands_match_plan_order_exactly():
    """М1.1 дословно: «в этом порядке — Telegram показывает как задано».
    До правки не было команды menu вовсе, а dashboard стоял в хвосте
    вместо пятого места."""
    expected = [
        "menu", "generate", "konspekt", "generate_ktp", "dashboard", "teacher",
        "class", "upload_ksp", "upload_ktp", "templates", "upload_template", "status",
        "history", "delete_my_data", "cancel",
    ]
    assert [name for name, _ in texts.BOT_COMMANDS] == expected


async def test_menu_command_returns_keyboard(isolated_env):
    """Смысл команды: постоянное меню можно свернуть в клиенте, и до
    правки развернуть его было нечем, кроме /start."""
    _create_teacher(980)
    message = FakeMessage(text="/menu", user_id=980)
    await cmd_menu(message, _state())

    assert message.sent[-1]["reply_markup"] is keyboards.MAIN_MENU


async def test_menu_command_interrupts_dialog_and_discards_audio(isolated_env):
    """Просьба показать меню посреди диалога — это выход из диалога, тем
    же смыслом, что у кнопок меню. Скачанные части записи при этом не
    остаются на диске (находка 2 того же аудита)."""
    state, paths = await _konspekt_with_parts(981)

    message = FakeMessage(text="/menu", user_id=981)
    await cmd_menu(message, state)

    assert await state.get_state() is None
    assert not any(p.exists() for p in paths)
    assert message.sent[0]["text"] == texts.MENU_DIALOG_INTERRUPTED
    assert message.sent[-1]["reply_markup"] is keyboards.MAIN_MENU


# =====================================================================
# Аудит этапа 2, находка 7 — отказ по размеру объясняет, что делать
# =====================================================================


async def test_oversized_audio_gets_instruction_not_just_refusal(isolated_env):
    """К2.3 дословно: при превышении лимита выдать не «файл слишком
    большой», а инструкцию, что делать. Общий текст этапа 1 советует
    «пришлите файл поменьше» — для уже записанного урока это бесполезно."""
    _create_teacher(982)
    state = _state()
    await _start_konspekt(state, 982)

    huge = FakeAudio(file_size=25 * 1024 * 1024)  # 25 МБ, лимит Telegram — 20
    message = FakeMessage(audio=huge, user_id=982)
    await konspekt_audio_received(message, state, FakeBot())

    answer = message.sent[-1]["text"]
    assert "20 МБ" in answer                      # общая часть про лимит осталась
    assert "моно" in answer                        # что именно делать
    assert "на части" in answer
    assert str(MAX_KONSPEKT_PARTS) in answer       # сколько частей можно
    # файл не приняли и на диск ничего не положили
    assert (await state.get_data()).get("audio_paths", []) == []
    assert list(settings.uploads_dir.iterdir()) == []


async def test_oversized_docx_upload_keeps_plain_message(isolated_env):
    """Граница правки: подсказка про части записи — только для аудио.
    В /upload_ksp остаётся прежний общий текст, его не трогали."""
    _create_teacher(983)
    state = _state()
    await cmd_upload_ksp(FakeMessage(text="/upload_ksp", user_id=983), state)

    message = FakeMessage(document=FakeDocument(file_size=25 * 1024 * 1024), user_id=983)
    await upload_ksp_file_received(message, state, FakeBot())

    assert "моно" not in message.sent[-1]["text"]


# =====================================================================
# Кнопка «Начать расшифровку» — /done одним нажатием
# =====================================================================


async def test_start_button_launches_transcription_like_done(isolated_env):
    """Шаг сбора записи сам не двигается дальше и таймера не имеет —
    без явной команды расшифровка не начнётся вообще. Кнопка делает то
    же, что /done, чтобы команду не приходилось помнить."""
    state, paths = await _konspekt_with_parts(990, parts=2)

    message = FakeMessage(text=texts.KONSPEKT_START_BUTTON, user_id=990, chat_id=990)
    await konspekt_done(message, state)

    tasks = query("SELECT * FROM tasks WHERE type = 'transcribe'")
    assert len(tasks) == 1
    assert json.loads(tasks[0]["payload"])["audio_paths"] == [str(p) for p in paths]
    assert await state.get_state() is None


async def test_start_button_is_actually_wired_to_router(isolated_env):
    """Кнопка бесполезна, если её текст не привязан к хендлеру в роутере:
    сообщение уйдёт в konspekt_wrong_input («не похоже на аудио»).
    Проверяем именно привязку — прогоняем текст кнопки через настоящие
    фильтры роутера, а не зовём функцию напрямую."""
    from types import SimpleNamespace

    from bot.handlers import router

    event = SimpleNamespace(
        text=texts.KONSPEKT_START_BUTTON, voice=None, audio=None, document=None, caption=None
    )
    matched = []
    for handler in router.message.handlers:
        for f in handler.filters:
            if "MagicFilter" not in str(f.callback):
                continue  # фильтр состояния/команды — не про текст
            try:
                if f.callback(event):
                    matched.append(handler.callback.__name__)
            except Exception:
                pass

    assert "konspekt_done" in matched, "текст кнопки не привязан ни к одному хендлеру"
    # и порядок: кнопка обязана стоять выше «не похоже на аудио»
    names = [
        h.callback.__name__
        for h in router.message.handlers
        if any("Konspekt" in str(f.callback) for f in h.filters)
    ]
    assert names.index("konspekt_done") < names.index("konspekt_wrong_input")


async def test_collecting_keyboard_offers_start_button(isolated_env):
    """Кнопка должна реально приходить пользователю на этом шаге —
    иначе нажимать нечего."""
    _create_teacher(991)
    state = _state()
    message = await _start_konspekt(state, 991)

    keyboard = message.sent[-1]["reply_markup"]
    texts_on_keyboard = [b.text for row in keyboard.keyboard for b in row]
    assert texts.KONSPEKT_START_BUTTON in texts_on_keyboard
    # Н1: «Назад» здесь удаляла бы файл, а не двигала диалог назад — у
    # кнопки своя, честная подпись, BUTTON_BACK на этом шаге не показывается.
    assert texts.BUTTON_REMOVE_LAST_PART in texts_on_keyboard
    assert texts.BUTTON_BACK not in texts_on_keyboard
    assert texts.BUTTON_CANCEL in texts_on_keyboard


async def test_start_button_without_parts_does_not_enqueue(isolated_env):
    """Граница: нажать кнопку, ничего не прислав — не задача в очередь,
    а понятный ответ, тот же что у /done."""
    _create_teacher(992)
    state = _state()
    await _start_konspekt(state, 992)

    message = FakeMessage(text=texts.KONSPEKT_START_BUTTON, user_id=992, chat_id=992)
    await konspekt_done(message, state)

    assert query("SELECT * FROM tasks WHERE type = 'transcribe'") == []
    assert message.sent[-1]["text"] == texts.KONSPEKT_NO_PARTS_YET


async def test_konspekt_accepts_no_more_than_two_parts(isolated_env):
    """Решение автора от 27.08.2026: не больше двух частей записи.
    Урок на 45 минут укладывается в один файл до 20 МБ (проверено на
    настоящей записи: 39 минут — 19,2 МБ), вторая нужна разве что на пару."""
    assert MAX_KONSPEKT_PARTS == 2

    _create_teacher(993)
    state = _state()
    await _start_konspekt(state, 993)

    bot = FakeBot()
    third = None
    for _ in range(3):
        third = FakeMessage(user_id=993, voice=FakeVoice())
        await konspekt_voice_received(third, state, bot)

    assert len((await state.get_data())["audio_paths"]) == 2
    assert "больше не приму" in third.sent[-1]["text"]
    # третий файл на диск не лёг — принято ровно два
    assert len(list(settings.uploads_dir.iterdir())) == 2


def test_texts_about_parts_show_the_actual_limit():
    """Число в текстах подставляется из константы, а не написано руками —
    иначе бот обещал бы одно, а делал другое."""
    prompt = texts.KONSPEKT_PROMPT.format(max_parts=MAX_KONSPEKT_PARTS)
    assert "не более 2" in prompt
    assert "10" not in prompt

    hint = texts.KONSPEKT_FILE_TOO_LARGE_HINT.format(max_parts=MAX_KONSPEKT_PARTS)
    assert "не больше 2" in hint

    reached = texts.KONSPEKT_MAX_PARTS_REACHED.format(max=MAX_KONSPEKT_PARTS)
    assert "частей: 2" in reached
