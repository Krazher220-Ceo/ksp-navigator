"""
bot/navigation.py — стек навигации «Назад» поверх FSMContext (блок М3).

Зачем модуль: до этого блока переход между шагами диалога был
однонаправленным присваиванием `await state.set_state(...)` — вернуться
на предыдущий шаг было физически некуда, история не хранилась. Этот
модуль — тонкий слой поверх FSMContext: `go_to` кладёт текущее состояние
в стек перед переключением на следующее, `go_back` снимает верхушку.

Стек, а не «предыдущее состояние по списку из bot/states.py» — потому что
в /generate два шага необязательные (фото учебника, опции урока) и могут
быть пропущены. «Назад» обязан вернуть на предыдущий фактически
показанный шаг, а не на предыдущий по порядку объявления состояний.

Что осознанно не делает: не хранит сами введённые данные шага — это по-
прежнему `state.update_data(...)` в хендлерах, как и раньше. Не решает,
какой вопрос задать при возврате на шаг — это ответственность хендлера
(bot/handlers.py), который вызвавший его код должен позвать заново.

На что опирается: aiogram.fsm.context.FSMContext. Стек живёт в
state.get_data() под ключом _NAV_STACK_KEY, поэтому переживает переключения
состояния (state.set_state не трогает данные) и исчезает вместе с data при
state.clear() — отдельно чистить его в /cancel не нужно, но reset_nav
экспортирован для мест, где нужно сбросить именно стек, не трогая
остальные данные диалога.
"""

from aiogram.fsm.context import FSMContext

_NAV_STACK_KEY = "_nav_stack"


async def go_to(state: FSMContext, new_state) -> None:
    """Запоминает текущее состояние (если оно есть) в стеке и переключается
    на new_state. Первый шаг диалога (когда current is None) в стек не
    кладётся — иначе go_back с первого шага уводил бы в состояние None,
    неотличимое от "стек пуст", что ломало бы сигнал "мы на первом шаге"."""
    data = await state.get_data()
    stack = list(data.get(_NAV_STACK_KEY, []))
    current = await state.get_state()
    if current is not None:
        stack.append(current)
    await state.update_data(**{_NAV_STACK_KEY: stack})
    await state.set_state(new_state)


async def go_back(state: FSMContext) -> str | None:
    """Снимает верхушку стека, переключает состояние на неё, возвращает её
    имя. Пустой стек — не ошибка: это значит, что дальше назад некуда (мы
    на первом шаге диалога, или бот перезапускался и MemoryStorage потерял
    историю — PLAN_STAGE2.md, М3.1, ловушка). В этом случае состояние
    очищается целиком и возвращается None — вызывающий код обязан свести
    пользователя в главное меню, а не оставить его в подвисшем диалоге."""
    data = await state.get_data()
    stack = list(data.get(_NAV_STACK_KEY, []))
    if not stack:
        await state.clear()
        return None
    previous = stack.pop()
    await state.update_data(**{_NAV_STACK_KEY: stack})
    await state.set_state(previous)
    return previous


async def reset_nav(state: FSMContext) -> None:
    """Убирает только стек навигации, не трогая остальные данные диалога
    и текущее состояние. state.clear() (используется в /cancel и в конце
    успешного диалога) стек тоже сбрасывает — эта функция для мест, где
    нужно явно обнулить именно историю переходов."""
    data = await state.get_data()
    data.pop(_NAV_STACK_KEY, None)
    await state.set_data(data)
