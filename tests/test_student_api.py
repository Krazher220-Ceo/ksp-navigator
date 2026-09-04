"""
tests/test_student_api.py — экран ученика: сверка тетради (блок Ф10).

Главное, что здесь сторожится: ученик не получает ни расшифровки, ни
полного конспекта — ни при каком действии. Это условие допуска в школу
(MASTER.md 0.9 п.3), и держаться оно должно на коде, а не на
формулировке промпта.

Плюс: фото удаляется сразу, оценка не ставится, а если пропущено почти
всё — список не вываливается, а человека отправляют к учителю.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import texts
from core import accounts
from core.config import settings
from core.db import execute, init_db, query
from core.queue import SOURCE_WEB
from tests.test_current_user import BOT_TOKEN, init_data
from tests.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app
from web.errors import CODE_NOT_FOUND

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
AUTH_UID = "9c1e7b30-0000-4000-8000-000000000001"
ФОТО = b"\xff\xd8\xff\xe0" + b"\x00" * 200


@pytest.fixture
def база(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    было = (settings.db_path, settings.db_backend, settings.telegram_bot_token,
            settings.supabase_jwt_secret, settings.supabase_url, settings.uploads_dir)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    object.__setattr__(settings, "supabase_jwt_secret", СЕКРЕТ)
    object.__setattr__(settings, "supabase_url", АДРЕС)
    object.__setattr__(settings, "uploads_dir", uploads)
    try:
        yield db_path
    finally:
        for имя, значение in zip(
            ("db_path", "db_backend", "telegram_bot_token", "supabase_jwt_secret",
             "supabase_url", "uploads_dir"), было
        ):
            object.__setattr__(settings, имя, значение)


@pytest.fixture
def клиент():
    return TestClient(app)


def по_почте() -> dict[str, str]:
    return {"Authorization": "Bearer " + собрать_токен()}


def мир(db_path, с_конспектом=True) -> dict:
    """Педагог, класс, ученик-с-почтой в этом классе и один урок."""
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", 501), db_path=db_path)
    teacher_id = query("SELECT id FROM teachers", db_path=db_path)[0]["id"]
    класс = accounts.create_class(teacher_id, "10 «А»", "физика", db_path=db_path)
    execute("INSERT INTO students (auth_user_id, name) VALUES (?, ?)", (AUTH_UID, "Алина"), db_path=db_path)
    student_id = query("SELECT id FROM students", db_path=db_path)[0]["id"]
    accounts.join_class(класс["id"], student_id, db_path=db_path)
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=db_path)
    execute("INSERT INTO transcripts (id, teacher_id, source, mode, text, duration_seconds, language) "
            "VALUES (?, ?, 'audio', 'student', ?, ?, 'ru')",
            ("t-1", teacher_id, "СЕКРЕТНАЯ РАСШИФРОВКА УРОКА ЦЕЛИКОМ", 2700), db_path=db_path)
    if с_конспектом:
        execute("INSERT INTO konspekty (id, teacher_id, transcript_id, mode, tema, content_json) "
                "VALUES (?, ?, 't-1', 'student', ?, ?)",
                ("k-1", teacher_id, "Импульс тела",
                 json.dumps({"opornye_repliki": ["СЕКРЕТНАЯ ОПОРНАЯ РЕПЛИКА"],
                             "konspekt_uchenika": {"tema": "Импульс тела",
                                                   "glavnoe": ["СЕКРЕТНЫЙ ТЕЗИС"],
                                                   "domashnee_zadanie": "§ 24, задачи 4–6"}},
                            ensure_ascii=False)), db_path=db_path)
    return {"teacher_id": teacher_id, "class_id": класс["id"], "student_id": student_id}


def прислать_фото(клиент, данные=ФОТО, transcript_id="t-1", имя="tetrad.jpg", заголовки=None):
    шапка = {"Content-Type": "image/jpeg", "X-Transcript-Id": transcript_id,
             **(по_почте() if заголовки is None else заголовки)}
    if имя is not None:
        шапка["X-Filename"] = имя
    return клиент.post("/api/v1/student/sverka", content=данные, headers=шапка)


# --- ученик не получает урок целиком ---

def test_в_списке_уроков_нет_расшифровки(база, клиент):
    """
    Полной расшифровки ученик не получает ни при каком действии. Отдаётся
    только то, что нужно для выбора урока.
    """
    состояние = мир(база)
    ответ = клиент.get("/api/v1/student/lessons", params={"class_id": состояние["class_id"]},
                       headers=по_почте())

    assert ответ.status_code == 200
    assert "СЕКРЕТНАЯ РАСШИФРОВКА" not in ответ.text
    урок = ответ.json()["lessons"][0]
    assert set(урок) == {"transcript_id", "created_at", "topic", "homework"}


def test_в_списке_уроков_нет_конспекта(база, клиент):
    """Полный конспект уходит только рукой учителя, адресно."""
    состояние = мир(база)
    ответ = клиент.get("/api/v1/student/lessons", params={"class_id": состояние["class_id"]},
                       headers=по_почте())
    assert "СЕКРЕТНЫЙ ТЕЗИС" not in ответ.text
    assert "СЕКРЕТНАЯ ОПОРНАЯ РЕПЛИКА" not in ответ.text


def test_домашнее_задание_отдаётся_и_это_одна_строка(база, клиент):
    """Домашка названа в макете отдельным блоком — это строка, не конспект."""
    состояние = мир(база)
    урок = клиент.get("/api/v1/student/lessons", params={"class_id": состояние["class_id"]},
                      headers=по_почте()).json()["lessons"][0]
    assert урок["homework"] == "§ 24, задачи 4–6"


def test_без_конспекта_домашнего_задания_просто_нет(база, клиент):
    """Ничего не додумываем: не было конспекта — нет и домашки."""
    состояние = мир(база, с_конспектом=False)
    урок = клиент.get("/api/v1/student/lessons", params={"class_id": состояние["class_id"]},
                      headers=по_почте()).json()["lessons"][0]
    assert урок["homework"] is None


def test_чужой_класс_уроков_не_показывает(база, клиент):
    мир(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    чужой_класс = accounts.create_class(чужой, "Чужой", db_path=база)

    ответ = клиент.get("/api/v1/student/lessons", params={"class_id": чужой_класс["id"]}, headers=по_почте())
    assert ответ.status_code == 404
    assert ответ.json()["error"]["message"] == texts.SVERKA_LESSON_NOT_FOUND


# --- сверка ---

def test_фото_уходит_в_очередь_тем_же_путём(база, клиент):
    состояние = мир(база)
    ответ = прислать_фото(клиент)

    assert ответ.status_code == 200
    задачи = query("SELECT * FROM tasks", db_path=база)
    assert len(задачи) == 1
    задача = dict(задачи[0])
    assert задача["type"] == "sverka_tetradi"
    payload = json.loads(задача["payload"])
    assert payload["student_id"] == состояние["student_id"]
    assert payload["transcript_id"] == "t-1"
    assert payload["source"] == SOURCE_WEB
    assert Path(payload["photo_path"]).parent == settings.uploads_dir


def test_чужой_урок_сверить_нельзя(база, клиент):
    мир(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    # Идентификатор только из ASCII: он едет заголовком, а HTTP другого
    # и не разрешает.
    execute("INSERT INTO transcripts (id, teacher_id, source, mode, text, language) "
            "VALUES ('t-chuzhoy', ?, 'audio', 'student', 'чужое', 'ru')", (чужой,), db_path=база)

    ответ = прислать_фото(клиент, transcript_id="t-chuzhoy")
    assert ответ.status_code == 404
    assert query("SELECT * FROM tasks", db_path=база) == []
    assert list(settings.uploads_dir.iterdir()) == [], "фото не должно остаться на диске"


def test_не_ученику_сверка_недоступна(база, клиент):
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Педагог", "физика", AUTH_UID), db_path=база)
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=база)
    ответ = прислать_фото(клиент)
    assert ответ.status_code == 403
    assert ответ.json()["error"]["message"] == texts.SVERKA_NOT_A_STUDENT


@pytest.mark.parametrize("имя", ["tetrad.exe", "urok.mp3", "spisok.docx"])
def test_не_фото_отклоняется_до_записи_на_диск(база, клиент, имя):
    мир(база)
    ответ = прислать_фото(клиент, имя=имя)
    assert ответ.status_code == 415
    assert ответ.json()["error"]["message"] == texts.API_PHOTO_UNSUPPORTED
    assert list(settings.uploads_dir.iterdir()) == []


def test_без_согласия_сверка_не_ставится(база, клиент):
    мир(база)
    execute("DELETE FROM consents_web", db_path=база)
    ответ = прислать_фото(клиент)
    assert ответ.status_code == 403
    assert query("SELECT * FROM tasks", db_path=база) == []


# --- результат ---

def test_ученик_следит_за_своей_задачей(база, клиент):
    мир(база)
    task_id = прислать_фото(клиент).json()["task_id"]
    ответ = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте())
    assert ответ.status_code == 200
    assert ответ.json()["status"] == "queued"


def test_чужую_сверку_ученик_не_увидит(база, клиент):
    мир(база)
    from core.queue import enqueue

    чужая = enqueue("sverka_tetradi", {"student_id": 9999, "transcript_id": "t-1"}, db_path=база)
    assert клиент.get(f"/api/v1/task/{чужая}", headers=по_почте()).status_code == 404


def test_результат_сверки_это_только_разница(база, клиент):
    """В ответе — чего не хватает, и ничего больше."""
    мир(база)
    task_id = прислать_фото(клиент).json()["task_id"]
    execute("UPDATE tasks SET status = 'done', result = ? WHERE id = ?",
            (json.dumps({"missing_items": ["условие замкнутости системы"], "too_much_missing": False},
                        ensure_ascii=False), task_id), db_path=база)

    тело = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()
    assert тело["result"]["missing_items"] == ["условие замкнутости системы"]
    assert "СЕКРЕТНАЯ РАСШИФРОВКА" not in json.dumps(тело, ensure_ascii=False)


def test_оценки_в_результате_нет(база, клиент):
    """
    Рукописная кириллица распознаётся на 30–70%. Этой точности хватает на
    подсказку и не хватает на суждение о человеке — оценки в ответе нет
    ни в каком виде.
    """
    мир(база)
    task_id = прислать_фото(клиент).json()["task_id"]
    execute("UPDATE tasks SET status = 'done', result = ? WHERE id = ?",
            (json.dumps({"missing_items": [], "too_much_missing": False}), task_id), db_path=база)
    тело = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()
    assert set(тело["result"]) == {"missing_items", "too_much_missing"}
    for запрещённое in ("score", "grade", "ball", "оценка", "процент", "percent"):
        assert запрещённое not in json.dumps(тело, ensure_ascii=False).lower()


def test_потолок_вывода_вычищает_список_а_не_только_сообщение():
    """
    Гарантия «ученик не получает полную расшифровку» обязана держаться и
    на РЕЗУЛЬТАТЕ задачи: кабинет читает его через /api/v1/task/{id}, и
    оставить там весь урок значило бы отдать ученику то же самое другим
    каналом.
    """
    исходник = (PROJECT_ROOT / "bot" / "handlers.py").read_text(encoding="utf-8")
    блок = исходник[исходник.index("too_much = exceeds_output_ceiling("):]
    блок = блок[: блок.index("return {")]
    assert "missing_items = []" in блок, "при превышении потолка список обязан очищаться"


# --- ученик не видит педагогических пунктов ---

def test_меню_ученика_не_содержит_пунктов_педагога():
    """
    Требование блока У3 дословно: педагогические функции для ученика «не
    существуют — не „нет доступа“, а не показываются». Отключённый пункт
    всё равно рассказывает ребёнку, что такая команда есть.
    """
    исходник = (PROJECT_ROOT / "frontend" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    меню_ученика = исходник[исходник.index("export const NAV_УЧЕНИКА"): исходник.index("export const NAV:")]

    for запрещённое in ("Собрать КСП", "Собрать КТП", "Шаблоны", "Тариф и оплата", "Покрытие программы"):
        assert запрещённое not in меню_ученика, f"ученику показан пункт педагога: {запрещённое}"
    assert "Сверка тетради" in меню_ученика


def test_до_ответа_сервера_рисуется_самое_узкое_меню():
    """Мигнуть учительским меню перед ребёнком нельзя."""
    оболочка = (PROJECT_ROOT / "frontend" / "app" / "(cabinet)" / "layout.tsx").read_text(encoding="utf-8")
    assert "загружено ? (я?.role ?? 'teacher') : 'student'" in оболочка


# --- лимит ученика: место вызова заведено при выключенном флаге ---

def test_веб_сверка_спрашивает_лимит_ученика_тем_же_способом_что_бот():
    """
    Находка 8 AUDIT.md была именно про это: проверка лимита ученика
    существовала, но её никто не вызывал, и включение
    STUDENT_TARIFFS_ENABLED не изменило бы ничего. В боте место вызова
    завели, а в вебе — нет, и та же дыра оказалась открыта второй раз.

    Проверяется наличие вызова, а не срабатывание отказа: пилот
    безлимитный (MASTER.md 0.10), флаг выключен, и отказывать живому
    ребёнку тест не должен — это отдельная проверка ниже.
    """
    исходник = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    блок = исходник[исходник.index("async def student_sverka("):]
    блок = блок[: блок.index("# ====")]
    assert "check_student_sverka_limit(" in блок, (
        "веб-сверка не спрашивает лимит ученика — включение тарифов её не затронет"
    )


def test_при_выключенных_тарифах_ученик_шлёт_сколько_угодно_фото(база, клиент):
    """
    Пилот безлимитный (MASTER.md 0.10 п.1), и заглушка не должна тихо
    включиться: ученик делает подряд больше сверок, чем стоит в самом
    щедром тарифе, и не получает отказа. Требование КГ плана, пункт 15.
    """
    from core.limits import STUDENT_DAILY_SVERKA_LIMITS, STUDENT_TARIFFS_ENABLED

    assert STUDENT_TARIFFS_ENABLED is False, "на пилоте тарифы обязаны быть выключены"

    мир(база)
    сколько = max(STUDENT_DAILY_SVERKA_LIMITS.values()) + 2
    for _ in range(сколько):
        ответ = прислать_фото(клиент)
        assert ответ.status_code == 200, ответ.json()

    assert len(query("SELECT id FROM tasks WHERE type = 'sverka_tetradi'", db_path=база)) == сколько
