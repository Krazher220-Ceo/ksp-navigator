"""
bot/states.py — состояния диалогов (aiogram FSM) для многошаговых команд.

Зачем модуль: /teacher, /upload_ksp и /generate — не однострочные
команды, а короткие диалоги. FSM-состояния описывают, на каком шаге
диалога находится пользователь, чтобы bot/handlers.py мог отличить
"это ответ на вопрос про класс" от "это новая команда".

Что осознанно не делает: не хранит сами данные диалога — это делает
aiogram FSMContext.storage (in-memory MemoryStorage, см. bot/main.py);
здесь только имена состояний.
"""

from aiogram.fsm.state import State, StatesGroup


class TeacherProfile(StatesGroup):
    waiting_for_name = State()
    waiting_for_subject = State()
    waiting_for_school = State()  # Р9, необязательный шаг


class UploadKSP(StatesGroup):
    collecting_files = State()


class UploadKTP(StatesGroup):
    waiting_for_file = State()


class UploadTemplate(StatesGroup):
    waiting_for_file = State()


class Generate(StatesGroup):
    waiting_for_topic = State()
    waiting_for_objective_code = State()
    waiting_for_razdel = State()
    waiting_for_klass = State()
    waiting_for_duration = State()
    waiting_for_textbook_photos = State()  # Р6.1, необязательный шаг
    waiting_for_extra_options = State()  # Р5.2/Р5.3, необязательный шаг
    waiting_for_template = State()
    waiting_for_confirmation = State()


class GenerateKTP(StatesGroup):
    """/generate_ktp — блок Р4.3 (PLAN_STAGE1_EXT.md)."""

    waiting_for_predmet = State()
    waiting_for_klass = State()
    waiting_for_hours_week = State()
    waiting_for_hours_year = State()
    waiting_for_topics = State()
    waiting_for_confirmation = State()


class ClassCreate(StatesGroup):
    """/class -> «Создать класс» (блок У2, PLAN.md): название и предмет,
    два коротких шага, третий — подтверждение кнопкой «Готово»."""

    waiting_for_name = State()
    waiting_for_subject = State()
    waiting_for_confirmation = State()


class StudentJoin(StatesGroup):
    """Ученик вводит код приглашения (блок У3, PLAN.md)."""

    waiting_for_code = State()
    waiting_for_confirmation = State()


class SverkaCheck(StatesGroup):
    """/sverka: ученик присылает фото тетради для сверки с расшифровкой
    урока (блок У4, PLAN.md). Выбор класса и урока — обычные inline-шаги
    без FSM (короткие, не текстовый ввод) — состояние заводится только
    на последнем шаге, ожидании фото."""

    waiting_for_photo = State()


class Konspekt(StatesGroup):
    """/konspekt: сначала выбор назначения записи, затем сбор аудио.

    Файлы копятся (как UploadKSP), пока учитель не отправит /done. Режим
    определяет продолжение после расшифровки: конспект ученику или чистый
    транскрипт учителю.
    """

    choosing_mode = State()
    collecting_audio = State()
