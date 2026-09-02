"""
tests/test_classes_api.py — классы и ученики (блок Ф8).

Три вещи, которые нельзя сломать незаметно: удаление класса не удаляет
учеников; массовой рассылки конспекта классу нет; об ученике не
появляется ничего сверх имени и идентификатора.

Сети нет: TestClient in-process, отправка в Telegram подменяется.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import texts
from core import accounts
from core.config import settings
from core.db import execute, init_db, query
from tests.test_current_user import BOT_TOKEN
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
            settings.supabase_jwt_secret, settings.supabase_url, settings.generated_dir)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    object.__setattr__(settings, "supabase_jwt_secret", СЕКРЕТ)
    object.__setattr__(settings, "supabase_url", АДРЕС)
    object.__setattr__(settings, "generated_dir", tmp_path)
    try:
        yield db_path
    finally:
        for имя, значение in zip(
            ("db_path", "db_backend", "telegram_bot_token", "supabase_jwt_secret",
             "supabase_url", "generated_dir"), было
        ):
            object.__setattr__(settings, имя, значение)


@pytest.fixture
def клиент():
    return TestClient(app)


def по_почте() -> dict[str, str]:
    return {"Authorization": "Bearer " + собрать_токен()}


def завести_педагога(db_path) -> int:
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", AUTH_UID), db_path=db_path)
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=db_path)
    return query("SELECT id FROM teachers ORDER BY id DESC", db_path=db_path)[0]["id"]


def завести_ученика(db_path, имя, telegram_id=None, auth_user_id=None) -> int:
    execute("INSERT INTO students (telegram_id, auth_user_id, name) VALUES (?, ?, ?)",
            (telegram_id, auth_user_id, имя), db_path=db_path)
    return query("SELECT id FROM students ORDER BY id DESC", db_path=db_path)[0]["id"]


# --- список и создание ---

def test_класс_создаётся_с_кодом_приглашения(база, клиент):
    завести_педагога(база)
    ответ = клиент.post("/api/v1/classes", headers=по_почте(), json={"name": "10 «А»", "subject": "физика"})

    assert ответ.status_code == 200
    класс = ответ.json()["class"]
    assert класс["name"] == "10 «А»"
    assert len(класс["invite_code"]) == accounts.INVITE_CODE_LENGTH
    assert класс["students_count"] == 0


def test_код_читается_вслух_без_похожих_символов(база, клиент):
    """0/O и 1/l/I в коде не встречаются: его диктуют на уроке."""
    завести_педагога(база)
    for _ in range(15):
        код = клиент.post("/api/v1/classes", headers=по_почте(), json={"name": "10 «А»"}).json()["class"]["invite_code"]
        assert set(код) <= set(accounts.INVITE_CODE_ALPHABET)
        assert not (set(код) & set("01OIL"))


def test_класс_без_названия_не_создаётся(база, клиент):
    завести_педагога(база)
    ответ = клиент.post("/api/v1/classes", headers=по_почте(), json={"name": "   "})
    assert ответ.status_code == 422
    assert query("SELECT * FROM classes", db_path=база) == []


def test_без_согласия_класс_не_создаётся(база, клиент):
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", AUTH_UID), db_path=база)
    ответ = клиент.post("/api/v1/classes", headers=по_почте(), json={"name": "10 «А»"})
    assert ответ.status_code == 403
    assert ответ.json()["error"]["code"] == CODE_CONSENT_REQUIRED


def test_видны_только_свои_классы(база, клиент):
    teacher_id = завести_педагога(база)
    accounts.create_class(teacher_id, "10 «А»", db_path=база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    accounts.create_class(чужой, "Чужой класс", db_path=база)

    классы = клиент.get("/api/v1/classes", headers=по_почте()).json()["classes"]
    assert [к["name"] for к in классы] == ["10 «А»"]


# --- перевыпуск кода ---

def test_перевыпуск_кода_отменяет_старый(база, клиент):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    старый = класс["invite_code"]

    новый = клиент.post(f"/api/v1/classes/{класс['id']}/code", headers=по_почте()).json()["invite_code"]

    assert новый != старый
    assert accounts.find_class_by_invite_code(старый, db_path=база) is None
    assert accounts.find_class_by_invite_code(новый, db_path=база)["name"] == "10 «А»"


def test_чужой_класс_не_перевыпустить(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    класс = accounts.create_class(чужой, "Чужой класс", db_path=база)

    ответ = клиент.post(f"/api/v1/classes/{класс['id']}/code", headers=по_почте())
    assert ответ.status_code == 404
    assert ответ.json()["error"]["code"] == CODE_NOT_FOUND


# --- удаление ---

def test_удаление_класса_не_удаляет_учеников(база, клиент):
    """
    Тот же ребёнок может состоять у другого педагога. Стереть его вместе
    с классом значило бы выкинуть чужие данные.
    """
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Дана Аманжолова", telegram_id=777)
    accounts.join_class(класс["id"], ученик, db_path=база)

    ответ = клиент.delete(f"/api/v1/classes/{класс['id']}", headers=по_почте())

    assert ответ.status_code == 200
    assert query("SELECT * FROM classes", db_path=база) == []
    assert query("SELECT * FROM class_members", db_path=база) == []
    оставшиеся = query("SELECT name FROM students", db_path=база)
    assert [с["name"] for с in оставшиеся] == ["Дана Аманжолова"], "ученик обязан остаться"


def test_чужой_класс_не_удалить(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    класс = accounts.create_class(чужой, "Чужой класс", db_path=база)

    assert клиент.delete(f"/api/v1/classes/{класс['id']}", headers=по_почте()).status_code == 404
    assert len(query("SELECT * FROM classes", db_path=база)) == 1


# --- ученики ---

def test_об_ученике_отдаётся_только_имя_и_счётчики(база, клиент):
    """Ни ИИН, ни фамилии в документах, ни даты рождения, ни оценок."""
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Дана Аманжолова", telegram_id=777)
    accounts.join_class(класс["id"], ученик, db_path=база)

    ученики = клиент.get(f"/api/v1/classes/{класс['id']}/students", headers=по_почте()).json()["students"]

    assert len(ученики) == 1
    assert set(ученики[0]) == {"id", "name", "joined_at", "sverki", "last_activity", "can_receive"}
    assert ученики[0]["name"] == "Дана Аманжолова"
    # Идентификатор в мессенджере наружу не отдаётся — только признак,
    # можно ли отправить конспект.
    assert "telegram_id" not in ученики[0]
    assert ученики[0]["can_receive"] is True


def test_число_сверок_считается_а_не_выдумывается(база, клиент):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Дана", telegram_id=777)
    accounts.join_class(класс["id"], ученик, db_path=база)
    execute("INSERT INTO usage_daily (telegram_user_id, day, operation, count) VALUES (?, ?, ?, ?)",
            (777, "2026-09-01", "tetrad_sverka", 4), db_path=база)
    execute("INSERT INTO usage_daily (telegram_user_id, day, operation, count) VALUES (?, ?, ?, ?)",
            (777, "2026-09-02", "tetrad_sverka", 2), db_path=база)

    ученики = клиент.get(f"/api/v1/classes/{класс['id']}/students", headers=по_почте()).json()["students"]
    assert ученики[0]["sverki"] == 6
    assert ученики[0]["last_activity"] == "2026-09-02"


def test_у_веб_ученика_сверок_нет_и_это_не_ноль_наугад(база, клиент):
    """Сверки ключуются Telegram; у пришедшего с сайта их просто нет."""
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Алина", auth_user_id="uuid-web")
    accounts.join_class(класс["id"], ученик, db_path=база)

    ученики = клиент.get(f"/api/v1/classes/{класс['id']}/students", headers=по_почте()).json()["students"]
    assert ученики[0]["sverki"] == 0
    assert ученики[0]["last_activity"] is None
    assert ученики[0]["can_receive"] is False


def test_учеников_чужого_класса_не_посмотреть(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    класс = accounts.create_class(чужой, "Чужой класс", db_path=база)

    assert клиент.get(f"/api/v1/classes/{класс['id']}/students", headers=по_почте()).status_code == 404


# --- отправка конспекта ---

@pytest.fixture
def отправленное(monkeypatch):
    """Перехватывает отправку в Telegram: в тестах сети нет."""
    отправки = []

    async def подмена(chat_id, путь, подпись):
        отправки.append((chat_id, Path(путь).name, подпись))

    from web import api_v1

    monkeypatch.setattr(api_v1, "_отправить_документ_в_telegram", подмена)
    return отправки


def подготовить_конспект(база, teacher_id, tmp_path) -> str:
    файл = settings.generated_dir / "konspekt.docx"
    файл.write_bytes(b"docx")
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json, docx_path) "
            "VALUES (?, ?, 'student', ?, ?, ?)",
            ("k-1", teacher_id, "Импульс тела", "{}", str(файл)), db_path=база)
    return "k-1"


def test_конспект_уходит_одному_ученику(база, клиент, отправленное, tmp_path):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Тимур Мұратов", telegram_id=777)
    accounts.join_class(класс["id"], ученик, db_path=база)
    konspekt_id = подготовить_конспект(база, teacher_id, tmp_path)

    ответ = клиент.post(f"/api/v1/classes/{класс['id']}/send-konspekt", headers=по_почте(),
                        json={"student_id": ученик, "konspekt_id": konspekt_id})

    assert ответ.status_code == 200
    assert ответ.json()["message"] == texts.SEND_KONSPEKT_SENT.format(student_name="Тимур Мұратов")
    assert len(отправленное) == 1
    chat_id, имя_файла, подпись = отправленное[0]
    assert chat_id == 777
    assert имя_файла == "konspekt.docx"
    assert "Импульс тела" in подпись


def test_массовой_рассылки_классу_нет():
    """
    Прямое решение автора: продукт, раздающий детям полные конспекты,
    отвечает на вопрос «зачем тогда ходить в школу» неправильным
    образом. Отправка всегда адресная — ни одного эндпоинта, который
    принимал бы класс без ученика.
    """
    from web import api_v1

    пути = [маршрут.path for маршрут in api_v1.router.routes]
    рассылки = [п for п in пути if "broadcast" in п or "send-all" in п or "рассыл" in п]
    assert рассылки == []

    исходник = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    отправка = исходник[исходник.index("async def send_konspekt("):]
    assert "student_id: int = Body(...)" in отправка, "адресат обязателен, а не необязателен"


def test_чужому_ученику_конспект_не_отправить(база, клиент, отправленное, tmp_path):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    чужой_ученик = завести_ученика(база, "Не мой", telegram_id=888)
    konspekt_id = подготовить_конспект(база, teacher_id, tmp_path)

    ответ = клиент.post(f"/api/v1/classes/{класс['id']}/send-konspekt", headers=по_почте(),
                        json={"student_id": чужой_ученик, "konspekt_id": konspekt_id})
    assert ответ.status_code == 404
    assert отправленное == []


def test_чужой_конспект_не_отправить(база, клиент, отправленное, tmp_path):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Тимур", telegram_id=777)
    accounts.join_class(класс["id"], ученик, db_path=база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json, docx_path) "
            "VALUES (?, ?, 'student', ?, ?, ?)",
            ("k-чужой", чужой, "Чужая тема", "{}", "/tmp/x.docx"), db_path=база)

    ответ = клиент.post(f"/api/v1/classes/{класс['id']}/send-konspekt", headers=по_почте(),
                        json={"student_id": ученик, "konspekt_id": "k-чужой"})
    assert ответ.status_code == 404
    assert отправленное == []


def test_ученику_без_telegram_отправить_нечем_и_это_сказано(база, клиент, отправленное, tmp_path):
    teacher_id = завести_педагога(база)
    класс = accounts.create_class(teacher_id, "10 «А»", db_path=база)
    ученик = завести_ученика(база, "Алина", auth_user_id="uuid-web")
    accounts.join_class(класс["id"], ученик, db_path=база)
    konspekt_id = подготовить_конспект(база, teacher_id, tmp_path)

    ответ = клиент.post(f"/api/v1/classes/{класс['id']}/send-konspekt", headers=по_почте(),
                        json={"student_id": ученик, "konspekt_id": konspekt_id})
    assert ответ.status_code == 422
    assert ответ.json()["error"]["message"] == texts.SEND_KONSPEKT_STUDENT_NO_TELEGRAM
    assert отправленное == []
