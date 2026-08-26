"""
bot/keyboards.py — сборка многократно используемых клавиатур бота, в одном месте.

Зачем модуль: с появлением постоянного меню (блок М2, PLAN_STAGE2.md)
клавиатуры перестали помещаться в местах, где раньше собирались прямо в
bot/handlers.py — там же, где текст ответа. Здесь — единственное место
для клавиатур, у которых фиксированная структура и которые нужны больше
чем в одном хендлере.

Что осознанно не делает: не содержит текстов кнопок (bot/texts.py,
PLAN_STAGE1.md Б8.1) и не решает, что происходит по нажатию — это
bot/handlers.py. Одноразовые inline-клавиатуры, чья структура зависит
от данных запроса (выбор конкретного шаблона, конкретной генерации из
истории), по-прежнему собираются на месте в bot/handlers.py — здесь для
них только вспомогательный `with_back_row`, добавляющий одинаковый на
всех шагах ряд «← Назад» (М3.2).

На что опирается: bot.texts (тексты кнопок).
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot import texts

# Постоянное меню (М2.1). is_persistent=True — клавиатура не исчезает
# после ответа, как это происходит с обычной ReplyKeyboardMarkup без
# этого флага. resize_keyboard=True — не растягивает кнопки на весь
# экран телефона.
MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text=texts.MENU_BUTTON_GENERATE_KSP),
            KeyboardButton(text=texts.MENU_BUTTON_GENERATE_KTP),
        ],
        [
            KeyboardButton(text=texts.MENU_BUTTON_TEACHER),
            KeyboardButton(text=texts.TEMPLATES_BUTTON),
        ],
        [
            KeyboardButton(text=texts.MENU_BUTTON_STATUS),
            KeyboardButton(text=texts.MENU_BUTTON_HISTORY),
        ],
        [
            KeyboardButton(text=texts.MENU_BUTTON_UPLOAD_KSP),
            KeyboardButton(text=texts.MENU_BUTTON_UPLOAD_KTP),
        ],
        [
            # М5.2: место рядом было обещано блоку "конспект урока" (К4) в
            # комментарии М2 — К4 ещё не сделан, дашборд сам по себе не
            # в паре ни с чем, ряд из одной кнопки.
            KeyboardButton(text=texts.MENU_BUTTON_DASHBOARD),
        ],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# Множество текстов кнопок меню — используется bot/handlers.py, чтобы
# отфильтровать сообщения, которые надо перехватить как нажатие кнопки,
# не задавая тот же список второй раз.
MAIN_MENU_BUTTON_TEXTS = {
    texts.MENU_BUTTON_GENERATE_KSP,
    texts.MENU_BUTTON_GENERATE_KTP,
    texts.MENU_BUTTON_TEACHER,
    texts.TEMPLATES_BUTTON,
    texts.MENU_BUTTON_STATUS,
    texts.MENU_BUTTON_HISTORY,
    texts.MENU_BUTTON_UPLOAD_KSP,
    texts.MENU_BUTTON_UPLOAD_KTP,
    texts.MENU_BUTTON_DASHBOARD,
}


# --- М3.2: клавиатуры шагов диалога с кнопкой «Назад» ---


def back_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для шагов с текстовым вводом: «← Назад» и «Отменить» в
    одном ряду. Новый объект на каждый вызов (не константа модуля, в
    отличие от MAIN_MENU) — ReplyKeyboardMarkup неизменяем содержательно
    здесь, но так проще расширить, если в будущем кнопки станут зависеть
    от контекста шага."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BUTTON_BACK), KeyboardButton(text=texts.BUTTON_CANCEL)]],
        resize_keyboard=True,
    )


def with_back_row(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Добавляет ряд «← Назад» под уже собранной inline-клавиатурой шага
    (выбор шаблона, подтверждение генерации/КТП) — callback_data="nav_back",
    один и тот же на все такие шаги (М3.2)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[*rows, [InlineKeyboardButton(text=texts.BUTTON_BACK, callback_data="nav_back")]]
    )
