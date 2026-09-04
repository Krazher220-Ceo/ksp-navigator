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
            # К4: место было обещано этой кнопке ещё в комментарии блока
            # М5.2 — теперь /konspekt работает целиком (аудио -> xAI STT
            # -> транскрипт -> конспект), заняла обещанное место.
            KeyboardButton(text=texts.MENU_BUTTON_DASHBOARD),
            KeyboardButton(text=texts.MENU_BUTTON_KONSPEKT),
        ],
        [
            # У2 (PLAN.md): класс и ученики — новая группа функций, кнопка
            # в отдельном ряду одна, не в паре — следующая функция группы
            # У (сверка тетради) кнопки не получает, это команда ученика,
            # не педагога, а этот ряд — про педагога.
            KeyboardButton(text=texts.MENU_BUTTON_CLASS),
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
    texts.MENU_BUTTON_KONSPEKT,
    texts.MENU_BUTTON_CLASS,
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


def consent_keyboard() -> InlineKeyboardMarkup:
    """Ю3: экран согласия при первом /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.CONSENT_ACCEPT_BUTTON, callback_data="consent_accept"),
                InlineKeyboardButton(text=texts.CONSENT_DECLINE_BUTTON, callback_data="consent_decline"),
            ]
        ]
    )


def login_confirm_keyboard(код: str) -> InlineKeyboardMarkup:
    """Подтверждение входа в кабинет из бота (core/login_codes.py).

    Код едет в callback_data: у Telegram на неё 64 байта, код занимает 12
    знаков плюс префикс — помещается с запасом. Класть код в состояние
    FSM вместо этого было бы хуже: человек может открыть две ссылки
    подряд, и состояние помнит только последнюю.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Это я", callback_data=f"login_ok:{код}"),
                InlineKeyboardButton(text="🚫 Это не я", callback_data=f"login_no:{код}"),
            ]
        ]
    )


def role_choice_keyboard() -> InlineKeyboardMarkup:
    """У3: первый /start незнакомого человека — педагог или ученик."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.ROLE_TEACHER_BUTTON, callback_data="role_teacher"),
                InlineKeyboardButton(text=texts.ROLE_STUDENT_BUTTON, callback_data="role_student"),
            ]
        ]
    )


def student_consent_keyboard() -> InlineKeyboardMarkup:
    """У3: экран согласия для ученика — своя редакция текста (Ю3, в
    формулировке для несовершеннолетнего), но те же подписи кнопок и
    физически другая callback_data, чтобы не путать с consent_keyboard
    учителя — от этого зависит, куда вести после согласия (bot/handlers.py)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.CONSENT_ACCEPT_BUTTON, callback_data="student_consent_accept"),
                InlineKeyboardButton(text=texts.CONSENT_DECLINE_BUTTON, callback_data="student_consent_decline"),
            ]
        ]
    )


def cancel_only_keyboard() -> ReplyKeyboardMarkup:
    """Один «Отменить» — для шагов без осмысленного «Назад» (У3: ввод
    кода приглашения — первый и единственный шаг диалога, стеку
    навигации возвращаться некуда)."""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=texts.BUTTON_CANCEL)]], resize_keyboard=True)


def upload_ksp_collecting_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура сбора файлов КСП (/upload_ksp, блок Н1 PLAN.md).

    Отличается от back_cancel_keyboard подписью первой кнопки: в этом
    диалоге шаг всего один, файлы копятся, и нажатие удаляет последний
    загруженный файл с диска — это не переход на предыдущий шаг, и
    подпись «← Назад» вводила пользователя в заблуждение."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.BUTTON_REMOVE_LAST_FILE), KeyboardButton(text=texts.BUTTON_CANCEL)]],
        resize_keyboard=True,
    )


def konspekt_mode_keyboard() -> ReplyKeyboardMarkup:
    """Два продуктовых режима обработки записи урока (К0)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=texts.KONSPEKT_MODE_STUDENT_BUTTON),
                KeyboardButton(text=texts.KONSPEKT_MODE_TEACHER_BUTTON),
            ],
            [KeyboardButton(text=texts.BUTTON_CANCEL)],
        ],
        resize_keyboard=True,
    )


def konspekt_collecting_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура шага сбора записи урока (/konspekt, К2.3).

    Отличается от back_cancel_keyboard одной кнопкой — «Начать
    расшифровку». Нужна потому, что этот шаг единственный, где диалог сам
    не двигается дальше: бот ждёт /done и без него ничего не начнёт, а
    таймера тут нет и быть не должно (расшифровка стоит минут работы, её
    нельзя запускать за пользователя, пока он, возможно, ещё досылает
    части). Команду /done приходилось помнить и набирать руками — кнопка
    делает то же самое одним нажатием."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.KONSPEKT_START_BUTTON)],
            [KeyboardButton(text=texts.BUTTON_REMOVE_LAST_PART), KeyboardButton(text=texts.BUTTON_CANCEL)],
        ],
        resize_keyboard=True,
    )


def with_back_row(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Добавляет ряд «← Назад» под уже собранной inline-клавиатурой шага
    (выбор шаблона, подтверждение генерации/КТП) — callback_data="nav_back",
    один и тот же на все такие шаги (М3.2)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[*rows, [InlineKeyboardButton(text=texts.BUTTON_BACK, callback_data="nav_back")]]
    )


def lesson_options_quick_keyboard(
    values: list[dict] | None = None, projects: list[dict] | None = None
) -> InlineKeyboardMarkup:
    """Главные настройки Ф2; состояние остаётся одним и тем же."""
    rows = [
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_OOP, callback_data="opt:ima_oop")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_SOR, callback_data="opt:sor")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_FIZ, callback_data="opt:fiz")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_TYPE_COMBINED, callback_data="opt:type:combined")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_TYPE_NEW, callback_data="opt:type:new")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_TYPE_PRACTICE, callback_data="opt:type:practice")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_TYPE_CONTROL, callback_data="opt:type:control")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_ORIENTATION_ALBUM, callback_data="opt:orientation:album")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_ORIENTATION_BOOK, callback_data="opt:orientation:book")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_MORE, callback_data="opt:more")],
        [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_DONE, callback_data="opt:done")],
    ]
    if values:
        rows = [[InlineKeyboardButton(text=texts.GENERATE_OPTIONS_VALUE_NONE, callback_data="opt:value:none")]] + [
            [InlineKeyboardButton(text=value["name"], callback_data=f"opt:value:{value['key']}")]
            for value in values
        ] + rows
    if projects:
        rows = [
            [InlineKeyboardButton(text=texts.GENERATE_OPTIONS_PROJECT_NONE, callback_data="opt:project:none")],
            *[
                [InlineKeyboardButton(text=project["name"], callback_data=f"opt:project:{project['key']}")]
                for project in projects
            ],
            *rows,
        ]
    return with_back_row(rows)
