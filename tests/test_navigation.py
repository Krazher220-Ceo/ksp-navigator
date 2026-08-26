"""
tests/test_navigation.py — тесты bot/navigation.py (блок М3.1).

Реального Bot/aiogram-приложения не нужно — FSMContext собирается на
MemoryStorage напрямую, тем же способом, что и в tests/test_bot_handlers.py.
"""

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.navigation import go_back, go_to, reset_nav


class _Steps(StatesGroup):
    one = State()
    two = State()
    three = State()
    four = State()


def _state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=0, chat_id=1, user_id=1))


async def test_go_back_on_empty_stack_returns_none_and_clears_state():
    state = _state()
    await state.set_state(_Steps.one)
    result = await go_back(state)
    assert result is None
    assert await state.get_state() is None


async def test_go_to_then_go_back_returns_previous_state_name():
    state = _state()
    await go_to(state, _Steps.one)
    await go_to(state, _Steps.two)
    previous = await go_back(state)
    assert previous == _Steps.one.state
    assert await state.get_state() == _Steps.one.state


async def test_push_three_pop_three_fourth_pop_is_none():
    """КГ дословно из PLAN_STAGE2.md, М3.1: положили три состояния, сняли
    три, на четвёртом получили None."""
    state = _state()
    await go_to(state, _Steps.one)
    await go_to(state, _Steps.two)
    await go_to(state, _Steps.three)
    await go_to(state, _Steps.four)

    assert await go_back(state) == _Steps.three.state
    assert await go_back(state) == _Steps.two.state
    assert await go_back(state) == _Steps.one.state
    assert await go_back(state) is None


async def test_go_to_preserves_data_set_before_it():
    """Данные диалога (тема, класс и т.д.) не должны стираться переходом
    по стеку — только состояние переключается, data остаётся."""
    state = _state()
    await go_to(state, _Steps.one)
    await state.update_data(topic="Закон Ома")
    await go_to(state, _Steps.two)
    data = await state.get_data()
    assert data["topic"] == "Закон Ома"

    await go_back(state)
    data_after_back = await state.get_data()
    assert data_after_back["topic"] == "Закон Ома"


async def test_first_step_not_pushed_go_back_from_second_step_returns_to_none():
    """Первый шаг диалога (когда до него состояние было None) в стек не
    кладётся — иначе go_back с первого шага увёл бы в None, неотличимое
    от «стек пуст»."""
    state = _state()
    await go_to(state, _Steps.one)  # первый шаг, current был None
    result = await go_back(state)
    assert result is None
    assert await state.get_state() is None


async def test_reset_nav_clears_stack_but_not_other_data():
    state = _state()
    await go_to(state, _Steps.one)
    await state.update_data(topic="Закон Ома")
    await go_to(state, _Steps.two)

    await reset_nav(state)

    data = await state.get_data()
    assert "_nav_stack" not in data
    assert data["topic"] == "Закон Ома"
    # состояние (текущий шаг) reset_nav не трогает
    assert await state.get_state() == _Steps.two.state

    # стек пуст - go_back теперь уводит в None
    result = await go_back(state)
    assert result is None
