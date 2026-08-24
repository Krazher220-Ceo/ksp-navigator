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
    waiting_for_template = State()
    waiting_for_confirmation = State()
