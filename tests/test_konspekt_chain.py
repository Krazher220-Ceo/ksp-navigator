"""
tests/test_konspekt_chain.py — путь «запись → конспект» в кабинете.

Сторожит одну находку живого прода 02.09.2026: расшифровка завершалась
успешно, но кабинету не за что было зацепиться, чтобы показать конспект.
В Telegram это не всплывало — там о готовности сообщает бот, — а в
браузере экран «Конспект урока» не доходил до результата никогда, в
обоих режимах.

Фикстуры и помощники берутся из tests/test_bot_handlers.py: заводить
второй набор тех же заглушек значило бы, что они разойдутся.
"""

from pathlib import Path

import pytest

from bot.handlers import make_transcribe_handler
from core.db import query
from tests.test_bot_handlers import (  # noqa: F401 — фикстуры нужны по имени
    FIXTURES_DIR,
    FakeBot,
    _create_teacher,
    fake_xai_transcriber,
    isolated_env,
)
from core.config import settings


async def _расшифровать(режим: str, chat_id: int):
    teacher_id = _create_teacher(chat_id)
    копия = settings.uploads_dir / f"part-{режим}.m4a"
    копия.write_bytes((FIXTURES_DIR / "audio_lesson_snippet.m4a").read_bytes())
    задача = {
        "id": f"tr-{режим}", "type": "transcribe", "telegram_chat_id": chat_id, "retries": 0,
        "payload": {"teacher_id": teacher_id, "audio_paths": [str(копия)], "mode": режим},
    }
    return teacher_id, await make_transcribe_handler(FakeBot())(задача)


@pytest.mark.parametrize("режим", ["student", "teacher"])
async def test_расшифровка_отдаёт_кабинету_ручку_конспекта(
    режим, isolated_env, fake_xai_transcriber,
):
    """
    Ровно одно из двух заполнено: готовый конспект или задача, которая
    его соберёт.

    Без этого кабинет упирался в тупик: задача расшифровки в состоянии
    done, konspekt_id в её результате нет, а второй задачи никто не
    назвал — и экран ждал вечно.
    """
    _, результат = await _расшифровать(режим, 940 if режим == "student" else 941)

    assert "konspekt_id" in результат and "konspekt_task_id" in результат, (
        "результат расшифровки перестал называть конспект"
    )
    ручки = [результат["konspekt_id"], результат["konspekt_task_id"]]
    assert sum(1 for р in ручки if р) == 1, (
        f"кабинету нужна ровно одна ручка, пришло {ручки}"
    )


async def test_учительский_режим_отдаёт_готовую_строку(isolated_env, fake_xai_transcriber):
    """Учительская ветка LLM не зовёт — конспект уже есть, id настоящий."""
    _, результат = await _расшифровать("teacher", 942)

    assert результат["konspekt_id"], "учительский режим не назвал konspekt_id"
    строки = query("SELECT * FROM konspekty WHERE id = ?", (результат["konspekt_id"],))
    assert len(строки) == 1, "konspekt_id указывает в пустоту"
    assert строки[0]["transcript_id"] == результат["transcript_id"]


async def test_ученический_режим_отдаёт_задачу_а_не_пустоту(isolated_env, fake_xai_transcriber):
    """Ученическая ветка ставит вторую задачу — её id и уходит наверх."""
    _, результат = await _расшифровать("student", 943)

    assert результат["konspekt_id"] is None
    задачи = query("SELECT * FROM tasks WHERE id = ?", (результат["konspekt_task_id"],))
    assert len(задачи) == 1, "konspekt_task_id указывает в пустоту"
    assert задачи[0]["type"] == "generate_konspekt"


def test_кабинет_переходит_на_вторую_задачу():
    """
    Пустой `return` вместо перехода — та самая строка, из-за которой
    экран ждал вечно. Тест держит именно её.
    """
    путь = (
        Path(__file__).resolve().parent.parent
        / "frontend" / "app" / "(cabinet)" / "app" / "urok" / "page.tsx"
    )
    assert путь.exists(), "экран «Конспект урока» пропал"
    текст = путь.read_text(encoding="utf-8")
    assert "konspekt_task_id" in текст, "кабинет не знает про вторую задачу"
    assert "следитьЗа(konspekt_task_id)" in текст, "кабинет её не отслеживает"
    assert "setШаг('ошибка')" in текст, "случай «ни конспекта, ни задачи» снова молчит"
