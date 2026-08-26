"""
bot/keyboards.py — сборка многократно используемых клавиатур бота, в одном месте.

Зачем модуль: с появлением постоянного меню (блок М2, PLAN_STAGE2.md)
клавиатуры перестали помещаться в местах, где раньше собирались прямо в
bot/handlers.py — там же, где текст ответа. Здесь — единственное место
для клавиатур, у которых фиксированная структура и которые нужны больше
чем в одном хендлере.

Что осознанно не делает: не содержит текстов кнопок (bot/texts.py,
PLAN_STAGE1.md Б8.1) и не решает, что происходит по нажатию — это
bot/handlers.py. Не содержит одноразовых inline-клавиатур (выбор
конкретного шаблона, конкретной генерации из истории, подтверждение) —
их структура зависит от данных запроса, они по-прежнему собираются на
месте.

На что опирается: bot.texts (тексты кнопок).
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

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
}
