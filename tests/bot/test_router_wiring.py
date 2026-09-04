"""tests/bot/test_router_wiring.py — прогон через настоящий Dispatcher aiogram.

Зачем файл: весь `tests/bot/test_bot_handlers.py` вызывает хендлеры напрямую
и потому минует router целиком — а значит, и цепочку middleware, и
порядок регистрации. `AUDIT.md` (раздел 8, пункт 3) прямо это отмечает:
логику находки удалось доказать только прямым вызовом. Здесь события
проходят тем же путём, каким их проводит Telegram: `dp.feed_update` →
outer middleware (`_consent_gate`, затем `_student_gate`) → хендлер,
выбранный по порядку регистрации.

Что осознанно не делает: не ходит в сеть — сессия бота подменена
`RecordingSession`, которая запоминает вызовы Bot API вместо отправки, и
ни одного HTTP-запроса не делает. Не проверяет содержимое ответов
подробно — это дело тестов конкретных хендлеров; здесь проверяется
проводка.

На что опирается: aiogram (уже в requirements.txt), стандартная
библиотека. Новых зависимостей не вводит.
"""

from datetime import datetime
from pathlib import Path

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

import bot.handlers as handlers
from bot import keyboards, texts
from core.config import settings
from core.db import execute, init_db, query

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

# Токен заведомо ненастоящий: сессия подменена, наружу он не уходит, но
# Bot проверяет формат строки при создании.
FAKE_TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class RecordingSession(BaseSession):
    """Запоминает вызовы Bot API вместо отправки их в Telegram."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if type(method).__name__ == "SendMessage":
            return Message(
                message_id=1,
                date=datetime.now(),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            ).as_(bot)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    def sent_messages(self):
        return [call for call in self.calls if type(call).__name__ == "SendMessage"]


@pytest.fixture(scope="session")
def dispatcher():
    """Router в проекте один на процесс, и aiogram не даёт подключить его
    ко второму Dispatcher'у — поэтому dispatcher тоже один на прогон.
    Тесты этого файла разведены по разным telegram-идентификаторам, так
    что состояния диалогов друг друга не задевают."""
    instance = Dispatcher(storage=MemoryStorage())
    instance.include_router(handlers.router)
    return instance


@pytest.fixture
def wired_bot(tmp_path, dispatcher):
    """Своя чистая база и своя записывающая сессия на каждый тест."""
    db_path = tmp_path / "wiring.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    original_db_path = settings.db_path
    original_db_backend = settings.db_backend
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")

    session = RecordingSession()
    telegram_bot = Bot(token=FAKE_TOKEN, session=session)
    try:
        yield dispatcher, telegram_bot, session
    finally:
        object.__setattr__(settings, "db_path", original_db_path)
        object.__setattr__(settings, "db_backend", original_db_backend)


def _text_update(text: str, user_id: int) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Тест"),
            text=text,
        ),
    )


async def _send(wired, text: str, user_id: int):
    dispatcher, telegram_bot, session = wired
    session.calls.clear()
    await dispatcher.feed_update(telegram_bot, _text_update(text, user_id))
    return session.sent_messages()


def _got_teacher_menu(sent) -> bool:
    return any(message.reply_markup is keyboards.MAIN_MENU for message in sent)


# --- Находка 1 AUDIT.md: тот же сценарий, что воспроизводил аудит ---


async def test_student_gets_no_teacher_menu_through_the_real_router(wired_bot):
    execute("INSERT INTO students (telegram_id, name) VALUES (700100, 'Ученик')")
    handlers.record_consent(700100)
    handlers._gate_role_cache.clear()

    for command in ("/menu", "/generate", "/dashboard", "/class"):
        sent = await _send(wired_bot, command, 700100)
        assert sent, f"{command}: бот вообще ничего не ответил"
        assert not _got_teacher_menu(sent), f"{command}: ученик получил меню педагога"
        assert sent[0].text == texts.STUDENT_TEACHER_COMMAND_UNAVAILABLE


async def test_teacher_command_does_not_register_a_student_as_teacher(wired_bot):
    """Дословно из аудита: бот сам называл ребёнку команду, которой
    система обходится, и после неё ребёнок становился педагогом.

    Проходится ВЕСЬ диалог /teacher (имя → предмет → школа), потому что
    строка в teachers появляется только на последнем шаге — проверка
    одной первой команды была бы зелёной и без починки."""
    execute("INSERT INTO students (telegram_id, name) VALUES (700101, 'Ученик')")
    handlers.record_consent(700101)
    handlers._gate_role_cache.clear()

    for step in ("/teacher", "Иванов Иван Иванович", "физика", "-"):
        await _send(wired_bot, step, 700101)

    assert query("SELECT 1 FROM teachers WHERE telegram_user_id = 700101") == [], (
        "ученик прошёл диалог /teacher до конца и стал педагогом"
    )
    assert handlers._is_student(700101) is True


async def test_teacher_still_gets_the_menu_through_the_real_router(wired_bot):
    """Страховка: гейт не должен задеть настоящего педагога."""
    handlers.record_consent(700102)
    handlers._gate_role_cache.clear()

    sent = await _send(wired_bot, "/menu", 700102)
    assert _got_teacher_menu(sent), "педагог перестал получать меню"


# --- Ю3/Э3: согласие проверяется раньше роли, тоже через router ---


async def test_consent_gate_runs_before_the_role_gate(wired_bot):
    """Порядок регистрации middleware: без согласия человек не должен
    доходить даже до разговора о роли."""
    sent = await _send(wired_bot, "/menu", 700103)

    assert sent
    assert sent[0].text == texts.CONSENT_REQUIRED_REDIRECT
    assert not _got_teacher_menu(sent)


async def test_start_is_exempt_from_the_consent_gate(wired_bot):
    sent = await _send(wired_bot, "/start", 700104)

    assert sent
    assert sent[0].text != texts.CONSENT_REQUIRED_REDIRECT


# --- Грабля 2.4: порядок регистрации хендлеров, проверенный router'ом ---


async def test_menu_button_text_is_not_eaten_by_a_dialog_state(wired_bot):
    """Кнопка меню, нажатая посреди диалога, обязана сработать как
    кнопка меню, а не как ответ на вопрос диалога («тема урока:
    Дашборд»). Эта ловушка срабатывала дважды (грабля 2.4).

    Профиль педагога здесь обязателен: без него /generate обрывается на
    «Сначала заведите профиль» и диалога, который можно было бы перебить,
    просто не возникает — тест проверял бы пустоту."""
    execute(
        "INSERT INTO teachers (name, subject, telegram_user_id) VALUES ('Тестов Тест', 'физика', 700105)"
    )
    handlers.record_consent(700105)
    handlers._gate_role_cache.clear()

    started = await _send(wired_bot, "/generate", 700105)
    assert started and started[-1].text != texts.ERROR_NO_TEACHER_PROFILE, (
        "диалог /generate не начался — проверять нечего"
    )

    sent = await _send(wired_bot, texts.MENU_BUTTON_DASHBOARD, 700105)

    assert sent
    assert any(message.text == texts.MENU_DIALOG_INTERRUPTED for message in sent), (
        "текст кнопки меню съеден как ответ на вопрос диалога — грабля 2.4"
    )
