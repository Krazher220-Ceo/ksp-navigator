"""Сквозная серверная граница выбора шаблона Mini App → бот (Т1)."""

import json
from pathlib import Path

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import (
    cmd_generate, generate_confirmed, generate_duration_received,
    generate_extra_options_received, generate_klass_received,
    generate_objective_code_received, generate_razdel_received,
    generate_textbook_photos_skipped, generate_topic_received,
    templates_web_app_choice,
)
from core.config import settings
from core.db import execute, init_db, query
from core.templates import list_templates, load_builtin_templates


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _User:
    def __init__(self, user_id: int):
        self.id = user_id


class _Chat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class _Message:
    def __init__(self, text: str | None = None, user_id: int = 1):
        self.text = text
        self.from_user = _User(user_id)
        self.chat = _Chat(user_id)
        self.sent: list[dict] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.sent.append({"text": text, "reply_markup": reply_markup})
        return _Message(text=text, user_id=self.from_user.id)


class _Callback:
    def __init__(self, message: _Message, user_id: int):
        self.data = "gen_confirm"
        self.message = message
        self.from_user = _User(user_id)

    async def answer(self, *args, **kwargs):
        return None


@pytest.fixture
def isolated_flow(tmp_path):
    db_path = tmp_path / "flow.db"
    init_db(db_path=db_path, schema_path=PROJECT_ROOT / "storage" / "schema.sql")
    previous_db_path = settings.db_path
    previous_db_backend = settings.db_backend
    # Тот же приём, что в test_bot_handlers.py и test_api.py: connect()
    # смотрит на settings.db_backend, подмены одного db_path недостаточно
    # (блок Н0 PLAN.md).
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    load_builtin_templates(db_path=db_path)
    try:
        yield db_path
    finally:
        object.__setattr__(settings, "db_path", previous_db_path)
        object.__setattr__(settings, "db_backend", previous_db_backend)


def _state(user_id: int) -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=0, chat_id=user_id, user_id=user_id))


async def _finish_dialog(state: FSMContext, user_id: int) -> _Callback:
    await generate_topic_received(_Message("Тема урока", user_id), state)
    await generate_objective_code_received(_Message("-", user_id), state)
    await generate_razdel_received(_Message("Раздел", user_id), state)
    await generate_klass_received(_Message("10А", user_id), state)
    await generate_duration_received(_Message("40", user_id), state)
    await generate_textbook_photos_skipped(_Message("/skip", user_id), state)
    message = _Message("-", user_id)
    await generate_extra_options_received(message, state)
    return _Callback(message, user_id)


@pytest.mark.parametrize("delivery", ["send_data", "server_fallback"])
async def test_miniapp_template_choice_reaches_queue_with_same_template_id(isolated_flow, delivery):
    user_id = 777
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Тест', 'физика', ?)",
        (user_id,), db_path=isolated_flow,
    )
    template_id = list_templates(1, db_path=isolated_flow)[0]["id"]
    state = _state(user_id)

    if delivery == "send_data":
        message = _Message(user_id=user_id)
        message.web_app_data = type("WebAppData", (), {"data": json.dumps({"template_id": template_id})})()
        await templates_web_app_choice(message, state)
    else:
        execute(
            "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (user_id, template_id), db_path=isolated_flow,
        )
        await cmd_generate(_Message("/generate", user_id), state)

    callback = await _finish_dialog(state, user_id)
    await generate_confirmed(callback, state)

    tasks = query("SELECT payload FROM tasks WHERE type = 'generate_ksp'", db_path=isolated_flow)
    assert len(tasks) == 1
    assert json.loads(tasks[0]["payload"])["template_id"] == template_id
