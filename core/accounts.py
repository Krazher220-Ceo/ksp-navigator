"""
core/accounts.py — кто человек в системе: профиль, роль, согласие, класс.

Зачем модуль: с блока Ф4 в продукт ведут две двери — Telegram и почта, —
и обеим нужны одни и те же действия: завести профиль педагога, вступить
в класс по коду, записать согласие. Если каждая дверь напишет это сама,
они разойдутся на первой же правке. Поэтому логика здесь, а web/ и bot/
только вызывают.

Что осознанно не делает: не проверяет подпись токена и не разбирает
initData — это web/auth.py. Не решает, показывать ли человеку экран, —
это дело эндпоинта. Не создаёт классы: их заводит педагог из бота
(блок У1), и веб-часть приезжает блоком Ф8.

На что опирается: core.db. Ни одной новой зависимости.

⚠️ Требует колонок teachers.auth_user_id, students.auth_user_id и
таблицы consents_web — они появились в блоке Ф4. В проде схему меняет
только автор руками: RPC ksp_execute_sql не пропускает DDL. Пока SQL не
выполнен, работает бот и старые /api/*, а регистрация через сайт
отвечает ошибкой — бот от этого не падает.
"""

import secrets

from core.db import execute, query

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"

# Код приглашения читается вслух на уроке, поэтому в алфавите нет ни
# похожих символов (0/O, 1/l/I), ни строчных букв — bot/handlers.py,
# _INVITE_CODE_ALPHABET. Разделители человек дописывает сам, когда
# записывает код с голоса, и они здесь просто выбрасываются.
_РАЗДЕЛИТЕЛИ = " \t -‐‑‒–—_"


# Алфавит кода приглашения: без похожих символов (0/O, 1/l/I) — код
# диктуется вслух на уроке, и «ноль или буква О» превращает урок в
# перекличку. Живёт здесь, а не в bot/handlers.py, потому что коды
# теперь заводят обе двери: правило одно, значит и место одно.
INVITE_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
INVITE_CODE_LENGTH = 6
_INVITE_CODE_MAX_ATTEMPTS = 10


def generate_invite_code(db_path=None) -> str:
    """Короткий код, читаемый вслух. Уникальность проверяется у базы, а
    не предполагается по размеру алфавита: при 32 символах и длине 6
    коллизия на масштабе пилота практически невозможна, но убедиться
    дешевле, чем гадать."""
    for _ in range(_INVITE_CODE_MAX_ATTEMPTS):
        код = "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))
        if not query("SELECT 1 FROM classes WHERE invite_code = ?", (код,), db_path=db_path):
            return код
    raise RuntimeError("не удалось подобрать уникальный код приглашения за отведённое число попыток")


def normalize_invite_code(raw: str) -> str:
    """Приводит введённый код к тому виду, в каком он лежит в базе.

    Регистр не важен, пробелы и дефисы отбрасываются: человек услышал
    код вслух и записал его так, как ему удобно, — «kz 4h7m», «KZ-4H7M»
    и «kz4h7m» обязаны сработать одинаково.
    """
    return "".join(символ for символ in (raw or "").upper() if символ not in _РАЗДЕЛИТЕЛИ)


def find_class_by_invite_code(code: str, db_path=None) -> dict | None:
    """Класс и имя педагога по коду приглашения, либо None."""
    нормальный = normalize_invite_code(code)
    if not нормальный:
        return None
    rows = query(
        "SELECT c.id, c.name, c.subject, t.name AS teacher_name "
        "FROM classes c JOIN teachers t ON t.id = c.teacher_id "
        "WHERE c.invite_code = ?",
        (нормальный,),
        db_path=db_path,
    )
    return dict(rows[0]) if rows else None


# --- профиль по аккаунту Supabase Auth ---

def find_teacher_by_auth_user(auth_user_id: str, db_path=None) -> dict | None:
    rows = query("SELECT * FROM teachers WHERE auth_user_id = ?", (auth_user_id,), db_path=db_path)
    return dict(rows[0]) if rows else None


def find_student_by_auth_user(auth_user_id: str, db_path=None) -> dict | None:
    rows = query("SELECT * FROM students WHERE auth_user_id = ?", (auth_user_id,), db_path=db_path)
    return dict(rows[0]) if rows else None


def resolve_role_by_auth_user(auth_user_id: str, db_path=None) -> str | None:
    """Роль вошедшего по почте. Ученик проверяется первым — по той же
    причине, что и в web/auth.py: цена ошибки в эту сторону меньше."""
    if find_student_by_auth_user(auth_user_id, db_path=db_path):
        return ROLE_STUDENT
    if find_teacher_by_auth_user(auth_user_id, db_path=db_path):
        return ROLE_TEACHER
    return None


def find_teacher_by_telegram(telegram_user_id: int, db_path=None) -> dict | None:
    rows = query(
        "SELECT * FROM teachers WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path
    )
    return dict(rows[0]) if rows else None


def create_teacher(
    name: str,
    subject: str,
    school: str | None = None,
    city: str | None = None,
    auth_user_id: str | None = None,
    telegram_user_id: int | None = None,
    db_path=None,
) -> dict:
    """Заводит профиль педагога — тем же путём, что /teacher в боте:
    та же таблица и те же поля, плюс город с экрана регистрации.

    Идентификатор нужен ровно один, зато обязательно: профиль без двери,
    через которую в него входят, никому не принадлежит.

    Повторный вызов профиль не задваивает — возвращает существующий:
    кнопку «создать аккаунт» нажимают дважды чаще, чем кажется.
    """
    if (auth_user_id is None) == (telegram_user_id is None):
        raise ValueError("нужен ровно один идентификатор: auth_user_id или telegram_user_id")

    найти = (
        (lambda: find_teacher_by_auth_user(auth_user_id, db_path=db_path))
        if auth_user_id is not None
        else (lambda: find_teacher_by_telegram(telegram_user_id, db_path=db_path))
    )
    уже_есть = найти()
    if уже_есть:
        return уже_есть
    execute(
        "INSERT INTO teachers (name, subject, school, city, auth_user_id, telegram_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, subject, school, city, auth_user_id, telegram_user_id),
        db_path=db_path,
    )
    созданный = найти()
    if созданный is None:
        raise RuntimeError("профиль педагога не сохранился")
    return созданный


def find_student_by_telegram(telegram_id: int, db_path=None) -> dict | None:
    rows = query("SELECT * FROM students WHERE telegram_id = ?", (telegram_id,), db_path=db_path)
    return dict(rows[0]) if rows else None


def ensure_student(
    name: str | None = None,
    auth_user_id: str | None = None,
    telegram_id: int | None = None,
    db_path=None,
) -> dict:
    """Строка ученика для той двери, через которую он вошёл. Идемпотентна.

    Об ученике хранится только имя и идентификатор: ни ИИН, ни фамилии в
    документах, ни даты рождения, ни оценок (MASTER.md 0.9).
    """
    if (auth_user_id is None) == (telegram_id is None):
        raise ValueError("нужен ровно один идентификатор: auth_user_id или telegram_id")

    найти = (
        (lambda: find_student_by_auth_user(auth_user_id, db_path=db_path))
        if auth_user_id is not None
        else (lambda: find_student_by_telegram(telegram_id, db_path=db_path))
    )
    уже_есть = найти()
    if уже_есть:
        return уже_есть
    execute(
        "INSERT INTO students (auth_user_id, telegram_id, name) VALUES (?, ?, ?)",
        (auth_user_id, telegram_id, name),
        db_path=db_path,
    )
    созданный = найти()
    if созданный is None:
        raise RuntimeError("профиль ученика не сохранился")
    return созданный


# --- класс ---

def is_class_member(class_id: int, student_id: int, db_path=None) -> bool:
    return bool(query(
        "SELECT 1 FROM class_members WHERE class_id = ? AND student_id = ?",
        (class_id, student_id),
        db_path=db_path,
    ))


def join_class(class_id: int, student_id: int, db_path=None) -> bool:
    """Вступление в класс. True — вступил сейчас, False — уже состоял."""
    if is_class_member(class_id, student_id, db_path=db_path):
        return False
    execute(
        "INSERT INTO class_members (class_id, student_id) VALUES (?, ?)",
        (class_id, student_id),
        db_path=db_path,
    )
    return True


# --- согласие ---
#
# Хранится в двух таблицах, потому что ключи разные: у пришедших из
# Telegram — telegram_user_id (таблица consents, блок Ю3), у пришедших с
# сайта — идентификатор аккаунта (consents_web, блок Ф4). Читать и писать
# их через две функции ниже, а не по месту: забыть одну из таблиц —
# значит спросить согласие второй раз у того, кто его уже дал.

def has_given_consent(
    telegram_user_id: int | None = None,
    auth_user_id: str | None = None,
    db_path=None,
) -> bool:
    if telegram_user_id is not None and query(
        "SELECT 1 FROM consents WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path
    ):
        return True
    if auth_user_id is not None and query(
        "SELECT 1 FROM consents_web WHERE auth_user_id = ?", (auth_user_id,), db_path=db_path
    ):
        return True
    return False


def record_consent(
    telegram_user_id: int | None = None,
    auth_user_id: str | None = None,
    db_path=None,
) -> None:
    """Записывает согласие. Хотя бы один идентификатор обязателен."""
    if telegram_user_id is None and auth_user_id is None:
        raise ValueError("согласие некому записать: не передан ни один идентификатор")
    if telegram_user_id is not None:
        execute(
            "INSERT INTO consents (telegram_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(telegram_user_id) DO UPDATE SET given_at = excluded.given_at",
            (telegram_user_id,),
            db_path=db_path,
        )
    if auth_user_id is not None:
        execute(
            "INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(auth_user_id) DO UPDATE SET given_at = excluded.given_at",
            (auth_user_id,),
            db_path=db_path,
        )


# --- классы педагога (блок Ф8) ---

def list_classes(teacher_id: int, db_path=None) -> list[dict]:
    """Классы педагога с числом учеников в каждом.

    Считает база, а не Python: список классов открывается на каждом
    заходе в раздел, и вытягивать ради счётчика всех участников значило
    бы возить туда-обратно данные, которые нужны одним числом.
    """
    строки = query(
        "SELECT c.id, c.name, c.subject, c.invite_code, c.created_at, "
        "       (SELECT COUNT(*) FROM class_members m WHERE m.class_id = c.id) AS students_count "
        "FROM classes c WHERE c.teacher_id = ? ORDER BY c.created_at, c.id",
        (teacher_id,),
        db_path=db_path,
    )
    return [dict(строка) for строка in строки]


def get_class(class_id: int, teacher_id: int, db_path=None) -> dict | None:
    """Класс, если он принадлежит этому педагогу. Иначе None — и
    вызывающий код обязан отдать 404, а не 403."""
    строки = query(
        "SELECT * FROM classes WHERE id = ? AND teacher_id = ?", (class_id, teacher_id), db_path=db_path
    )
    return dict(строки[0]) if строки else None


def create_class(teacher_id: int, name: str, subject: str | None = None, db_path=None) -> dict:
    """Заводит класс и сразу выдаёт код приглашения."""
    код = generate_invite_code(db_path=db_path)
    execute(
        "INSERT INTO classes (teacher_id, name, subject, invite_code) VALUES (?, ?, ?, ?)",
        (teacher_id, name, subject, код),
        db_path=db_path,
    )
    строки = query(
        "SELECT * FROM classes WHERE teacher_id = ? AND invite_code = ?",
        (teacher_id, код),
        db_path=db_path,
    )
    if not строки:
        raise RuntimeError("класс не сохранился")
    return dict(строки[0])


def regenerate_invite_code(class_id: int, teacher_id: int, db_path=None) -> str | None:
    """Новый код вместо прежнего. Старый перестаёт действовать сразу —
    ради этого перевыпуск и существует: код продиктовали не тому классу."""
    if get_class(class_id, teacher_id, db_path=db_path) is None:
        return None
    новый = generate_invite_code(db_path=db_path)
    execute("UPDATE classes SET invite_code = ? WHERE id = ?", (новый, class_id), db_path=db_path)
    return новый


def delete_class(class_id: int, teacher_id: int, db_path=None) -> bool:
    """Удаляет класс.

    Учеников не трогает: удаляется только их связь с этим классом. Тот
    же ребёнок может состоять у другого педагога, и стереть его вместе с
    классом значило бы выкинуть чужие данные. Порядок обязателен —
    сначала связи, потом класс: у class_members внешний ключ на classes,
    и удаление родителя первым упало бы и в SQLite, и в Postgres.
    """
    if get_class(class_id, teacher_id, db_path=db_path) is None:
        return False
    execute("DELETE FROM class_members WHERE class_id = ?", (class_id,), db_path=db_path)
    execute("DELETE FROM classes WHERE id = ?", (class_id,), db_path=db_path)
    return True


def list_class_students(class_id: int, db_path=None) -> list[dict]:
    """
    Ученики класса: имя, когда вступил, сколько сверок сделал и когда в
    последний раз что-то делал.

    Больше об ученике не хранится ничего — ни ИИН, ни фамилии в
    документах, ни даты рождения, ни оценок. Считать нечего сверх того,
    что уже посчитано в usage_daily.

    Сверки ключуются telegram_id: у ученика, пришедшего с сайта, их
    просто нет, и в ответе будет ноль без даты. Это честно — а не «0
    сверок» вперемешку с настоящими нулями.
    """
    строки = query(
        "SELECT s.id, s.name, s.telegram_id, s.auth_user_id, m.joined_at, "
        "       (SELECT COALESCE(SUM(u.count), 0) FROM usage_daily u "
        "         WHERE u.telegram_user_id = s.telegram_id AND u.operation = 'tetrad_sverka') AS sverki, "
        "       (SELECT MAX(u.day) FROM usage_daily u "
        "         WHERE u.telegram_user_id = s.telegram_id) AS last_activity "
        "FROM class_members m JOIN students s ON s.id = m.student_id "
        "WHERE m.class_id = ? ORDER BY m.joined_at, s.id",
        (class_id,),
        db_path=db_path,
    )
    return [dict(строка) for строка in строки]


def student_in_class(class_id: int, student_id: int, db_path=None) -> dict | None:
    """Ученик, если он состоит именно в этом классе."""
    строки = query(
        "SELECT s.* FROM class_members m JOIN students s ON s.id = m.student_id "
        "WHERE m.class_id = ? AND s.id = ?",
        (class_id, student_id),
        db_path=db_path,
    )
    return dict(строки[0]) if строки else None
