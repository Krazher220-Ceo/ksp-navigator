"""
tests/test_registration_api.py — вход, согласие, регистрация (блок Ф4).

Проверяет три вещи, которые нельзя сломать незаметно: согласие
спрашивается ДО действия, а не после; код приглашения прощает то, что
человек дописал с голоса; об ученике не сохраняется ничего, кроме имени.

Сети нет: TestClient in-process, база — SQLite во временной папке.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import accounts
from core.config import settings
from core.db import execute, init_db, query
from tests.test_current_user import BOT_TOKEN, init_data
from tests.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app
from web.errors import CODE_CONSENT_REQUIRED, CODE_NOT_FOUND

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
AUTH_UID = "9c1e7b30-0000-4000-8000-000000000001"


@pytest.fixture
def база(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    было = (settings.db_path, settings.db_backend, settings.telegram_bot_token,
            settings.supabase_jwt_secret, settings.supabase_url)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    object.__setattr__(settings, "supabase_jwt_secret", СЕКРЕТ)
    object.__setattr__(settings, "supabase_url", АДРЕС)
    try:
        yield db_path
    finally:
        for имя, значение in zip(
            ("db_path", "db_backend", "telegram_bot_token", "supabase_jwt_secret", "supabase_url"), было
        ):
            object.__setattr__(settings, имя, значение)


@pytest.fixture
def клиент():
    return TestClient(app)


def по_почте() -> dict[str, str]:
    return {"Authorization": "Bearer " + собрать_токен()}


def класс_с_кодом(db_path, code: str = "KZ4H7M", teacher: str = "Тлеубаева А.") -> int:
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            (teacher, "физика", 501), db_path=db_path)
    teacher_id = query("SELECT id FROM teachers", db_path=db_path)[0]["id"]
    execute("INSERT INTO classes (teacher_id, name, subject, invite_code) VALUES (?, ?, ?, ?)",
            (teacher_id, "10 «А»", "физика", code), db_path=db_path)
    return query("SELECT id FROM classes", db_path=db_path)[0]["id"]


# --- /me ---

def test_me_показывает_что_человек_ещё_ничего_не_принял(база, клиент):
    тело = клиент.get("/api/v1/me", headers=по_почте()).json()
    assert тело["role"] is None
    assert тело["consent_given"] is False
    assert тело["profile"] is None
    assert тело["user_id"] == f"auth:{AUTH_UID}"


def test_me_без_входа_отвечает_401(база, клиент):
    assert клиент.get("/api/v1/me").status_code == 401


# --- согласие ДО действия ---

def test_регистрация_без_согласия_не_создаёт_профиль(база, клиент):
    ответ = клиент.post("/api/v1/teacher", headers=по_почте(),
                        json={"name": "Тлеубаева А.", "subject": "физика"})
    assert ответ.status_code == 403
    assert ответ.json()["error"]["code"] == CODE_CONSENT_REQUIRED
    assert query("SELECT * FROM teachers", db_path=база) == [], \
        "человек, не принявший условия, не должен оставить о себе строку"


def test_вступление_в_класс_без_согласия_не_создаёт_ученика(база, клиент):
    класс_с_кодом(база)
    ответ = клиент.post("/api/v1/class/join", headers=по_почте(), json={"code": "KZ4H7M"})
    assert ответ.status_code == 403
    assert query("SELECT * FROM students", db_path=база) == []


def test_после_согласия_регистрация_проходит(база, клиент):
    assert клиент.post("/api/v1/consent", headers=по_почте()).json() == {"consent_given": True}
    ответ = клиент.post("/api/v1/teacher", headers=по_почте(), json={
        "name": "Тлеубаева А.", "subject": "физика", "school": "Школа №12", "city": "Костанай",
    })
    assert ответ.status_code == 200
    assert ответ.json()["profile"] == {
        "name": "Тлеубаева А.", "subject": "физика", "school": "Школа №12", "city": "Костанай",
    }
    строки = query("SELECT * FROM teachers", db_path=база)
    assert len(строки) == 1 and строки[0]["auth_user_id"] == AUTH_UID
    assert строки[0]["telegram_user_id"] is None


def test_согласие_из_telegram_и_из_веба_живут_отдельно(база, клиент):
    """У двух дверей разные ключи, и запись одной не должна засчитаться другой."""
    клиент.post("/api/v1/consent", headers=по_почте())
    assert accounts.has_given_consent(auth_user_id=AUTH_UID, db_path=база)
    assert not accounts.has_given_consent(telegram_user_id=777, db_path=база)


def test_повторная_регистрация_профиль_не_задваивает(база, клиент):
    клиент.post("/api/v1/consent", headers=по_почте())
    тело = {"name": "Тлеубаева А.", "subject": "физика"}
    клиент.post("/api/v1/teacher", headers=по_почте(), json=тело)
    клиент.post("/api/v1/teacher", headers=по_почте(), json=тело)
    assert len(query("SELECT * FROM teachers", db_path=база)) == 1


def test_после_регистрации_роль_становится_teacher(база, клиент):
    клиент.post("/api/v1/consent", headers=по_почте())
    клиент.post("/api/v1/teacher", headers=по_почте(), json={"name": "Т", "subject": "физика"})
    тело = клиент.get("/api/v1/me", headers=по_почте()).json()
    assert тело["role"] == "teacher"
    assert тело["profile"]["subject"] == "физика"


# --- код приглашения ---

@pytest.mark.parametrize("введено", [
    "KZ4H7M", "kz4h7m", "KZ-4H7M", "kz 4h7m", "  KZ4H7M  ", "KZ—4H7M", "Kz-4h 7M",
])
def test_код_читается_вслух_и_прощает_запись(база, клиент, введено):
    """
    Регистр не важен, пробелы и дефисы отбрасываются: человек услышал
    код на уроке и записал его так, как ему удобно.
    """
    класс_с_кодом(база)
    ответ = клиент.post("/api/v1/class/preview", headers=по_почте(), json={"code": введено})
    assert ответ.status_code == 200, введено
    assert ответ.json() == {"class_name": "10 «А»", "teacher_name": "Тлеубаева А."}


@pytest.mark.parametrize("введено", ["", "   ", "-", "НЕТТАКОГО", "KZ4H7X"])
def test_негодный_код_отвечает_кодом_не_найден(база, клиент, введено):
    """Текст — из bot/texts.py: у бота и у веба одна формулировка."""
    from bot import texts

    класс_с_кодом(база)
    ответ = клиент.post("/api/v1/class/preview", headers=по_почте(), json={"code": введено})
    assert ответ.status_code == 404
    assert ответ.json() == {
        "error": {"code": CODE_NOT_FOUND, "message": texts.STUDENT_JOIN_CODE_NOT_FOUND}
    }


def test_предпросмотр_класса_не_вступает_в_него(база, клиент):
    """Ребёнок сначала видит, куда вступает, и только потом решает."""
    класс_с_кодом(база)
    клиент.post("/api/v1/consent", headers=по_почте())
    клиент.post("/api/v1/class/preview", headers=по_почте(), json={"code": "KZ4H7M"})
    assert query("SELECT * FROM class_members", db_path=база) == []


# --- вступление ---

def test_вступление_в_класс(база, клиент):
    from bot import texts

    class_id = класс_с_кодом(база)
    клиент.post("/api/v1/consent", headers=по_почте())
    ответ = клиент.post("/api/v1/class/join", headers=по_почте(),
                        json={"code": "kz-4h7m", "name": "Алина"})
    тело = ответ.json()
    assert тело["joined"] is True
    assert тело["role"] == "student"
    assert тело["message"] == texts.STUDENT_JOIN_SUCCESS.format(
        class_name="10 «А»", teacher_name="Тлеубаева А."
    )
    участники = query("SELECT * FROM class_members WHERE class_id = ?", (class_id,), db_path=база)
    assert len(участники) == 1


def test_повторное_вступление_говорит_что_уже_состоит(база, клиент):
    from bot import texts

    класс_с_кодом(база)
    клиент.post("/api/v1/consent", headers=по_почте())
    клиент.post("/api/v1/class/join", headers=по_почте(), json={"code": "KZ4H7M", "name": "Алина"})
    ответ = клиент.post("/api/v1/class/join", headers=по_почте(), json={"code": "KZ4H7M"})
    assert ответ.json()["joined"] is False
    assert ответ.json()["message"] == texts.STUDENT_JOIN_ALREADY_MEMBER.format(class_name="10 «А»")
    assert len(query("SELECT * FROM class_members", db_path=база)) == 1


def test_об_ученике_хранится_только_имя(база, клиент):
    """Ни ИИН, ни фамилии в документах, ни даты рождения, ни оценок."""
    класс_с_кодом(база)
    клиент.post("/api/v1/consent", headers=по_почте())
    клиент.post("/api/v1/class/join", headers=по_почте(), json={"code": "KZ4H7M", "name": "Алина"})
    ученик = dict(query("SELECT * FROM students", db_path=база)[0])
    assert set(ученик) == {"id", "telegram_id", "auth_user_id", "name", "joined_at"}
    assert ученик["name"] == "Алина"


def test_ученик_из_telegram_и_из_веба_это_разные_строки(база, клиент):
    """Связывание двух дверей подтверждается кодом из бота (блок Ф4),
    а не совпадением имени — до связывания это два разных человека."""
    класс_с_кодом(база)
    клиент.post("/api/v1/consent", headers=по_почте())
    клиент.post("/api/v1/class/join", headers=по_почте(), json={"code": "KZ4H7M", "name": "Алина"})

    из_telegram = {"X-Telegram-Init-Data": init_data(4242)}
    клиент.post("/api/v1/consent", headers=из_telegram)
    клиент.post("/api/v1/class/join", headers=из_telegram, json={"code": "KZ4H7M", "name": "Алина"})

    ученики = query("SELECT telegram_id, auth_user_id FROM students", db_path=база)
    assert len(ученики) == 2
    assert {(р["telegram_id"], р["auth_user_id"]) for р in ученики} == {(None, AUTH_UID), (4242, None)}


# --- нормализация кода как функция ---

@pytest.mark.parametrize("введено,ожидается", [
    ("kz-4h7m", "KZ4H7M"), (" KZ 4H7M ", "KZ4H7M"), ("KZ–4H7M", "KZ4H7M"),
    ("", ""), (None, ""), ("KZ_4H7M", "KZ4H7M"),
])
def test_нормализация_кода(введено, ожидается):
    assert accounts.normalize_invite_code(введено) == ожидается
