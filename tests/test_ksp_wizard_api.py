"""
tests/test_ksp_wizard_api.py — мастер сборки КСП (блок Ф7).

Главное, что здесь сторожится: порядок колонок «Хода урока». Он закреплён
приложением 4 приказа МОН РК №130 в редакции от 30.04.2025 № 98 —
оценивание идёт ПЕРЕД ресурсами, — и один раз в проекте его уже путали
(блок Р1.1). Кабинет получает порядок с сервера, из того же модуля, что
собирает .docx, и второй копии приказа в проекте нет.

Ещё две вещи: настройки урока приходят из core/, а не сочиняются во
фронтенде, и генерация уходит в очередь, а не выполняется в обработчике
запроса.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import texts
from core.adal_azamat import PROJECTS
from core.config import settings
from core.docx_builder import CANONICAL_HOD_UROKA_COLUMNS
from core.db import execute, init_db, query
from core.ksp_generator import MAX_VIDY_DEYATELNOSTI, TIP_UROKA_OPTIONS
from core.queue import SOURCE_WEB
from core.values import VALUES
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


def завести_педагога(db_path) -> int:
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", AUTH_UID), db_path=db_path)
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=db_path)
    return query("SELECT id FROM teachers ORDER BY id DESC", db_path=db_path)[0]["id"]


def завести_цель(db_path, code="10.1.4.1") -> str:
    """objective_code в ktp_entries — внешний ключ на справочник целей."""
    execute("INSERT OR IGNORE INTO curriculum_objectives (code, grade, section, description) "
            "VALUES (?, ?, ?, ?)",
            (code, 10, "Законы сохранения", "применять закон сохранения импульса"), db_path=db_path)
    return code


def завести_шаблон(db_path) -> int:
    execute("INSERT INTO templates (name, description, source, is_official, is_builtin, category, structure_json) "
            "VALUES (?, ?, ?, 1, 1, 'official', ?)",
            ("Форма приказа МОН РК №130", "Приложение 4", "приказ №130", "{}"), db_path=db_path)
    return query("SELECT id FROM templates ORDER BY id DESC", db_path=db_path)[0]["id"]


# --- порядок колонок ---

def test_порядок_колонок_приходит_с_сервера_и_совпадает_с_приказом(база, клиент):
    """
    Оценивание ПЕРЕД ресурсами. Это не вкус, а приложение 4 приказа №130
    в редакции от 30.04.2025 № 98, и в проекте этот порядок уже путали.
    """
    завести_педагога(база)
    колонки = клиент.get("/api/v1/ksp/options", headers=по_почте()).json()["hod_uroka_columns"]
    ключи = [колонка["key"] for колонка in колонки]

    assert ключи == ["etap_vremya", "deystviya_pedagoga", "deystviya_uchenika", "ocenivanie", "resursy"]
    assert ключи.index("ocenivanie") < ключи.index("resursy")


def test_порядок_колонок_берётся_из_сборщика_docx_а_не_из_копии(база, клиент):
    """Второй копии приказа в проекте нет: список ровно тот же объект."""
    завести_педагога(база)
    колонки = клиент.get("/api/v1/ksp/options", headers=по_почте()).json()["hod_uroka_columns"]
    assert [к["key"] for к in колонки] == list(CANONICAL_HOD_UROKA_COLUMNS)


def test_у_каждой_колонки_есть_подпись_по_русски(база, клиент):
    завести_педагога(база)
    колонки = клиент.get("/api/v1/ksp/options", headers=по_почте()).json()["hod_uroka_columns"]
    подписи = [к["label"] for к in колонки]
    # Подпись первой колонки — дословно из приказа, с пробелом после «/».
    assert подписи == ["Этап урока/ Время", "Действия педагога", "Действия ученика", "Оценивание", "Ресурсы"]


# --- справочники настроек ---

def test_настройки_приходят_из_core_а_не_сочиняются(база, клиент):
    """
    Тип урока, ценности «Адал азамат» и виды деятельности заданы
    приказом и методичками. Набрать их руками в вёрстке — значит завести
    вторую редакцию, которая разойдётся при первой же правке.
    """
    завести_педагога(база)
    тело = клиент.get("/api/v1/ksp/options", headers=по_почте()).json()

    assert тело["tip_uroka"] == TIP_UROKA_OPTIONS
    assert [ц["key"] for ц in тело["cennosti"]] == list(VALUES)
    assert [п["key"] for п in тело["adal_azamat_projects"]] == list(PROJECTS)
    assert тело["max_vidy_deyatelnosti"] == MAX_VIDY_DEYATELNOSTI


def test_встроенные_шаблоны_видны_и_без_профиля(база, клиент):
    """Контракт «teacher_id=-1 — профиля нет» держит и Mini App."""
    завести_шаблон(база)
    тело = клиент.get("/api/v1/ksp/options", headers=по_почте()).json()
    assert [ш["name"] for ш in тело["templates"]] == ["Форма приказа МОН РК №130"]


def test_настройки_без_входа_не_отдаются(база, клиент):
    assert клиент.get("/api/v1/ksp/options").status_code == 401


# --- быстрый путь из КТП ---

def test_тема_из_ктп_подставляет_код_цели(база, клиент):
    teacher_id = завести_педагога(база)
    завести_цель(база)
    execute("INSERT INTO ktp_entries (teacher_id, topic, section, objective_code) VALUES (?, ?, ?, ?)",
            (teacher_id, "Импульс тела", "10.1В Законы сохранения", "10.1.4.1"), db_path=база)

    тело = клиент.get("/api/v1/ktp/objective", params={"topic": "импульс тела"}, headers=по_почте()).json()
    assert тело["objective_code"] == "10.1.4.1"


def test_чужой_ктп_кода_не_даёт(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    завести_цель(база)
    execute("INSERT INTO ktp_entries (teacher_id, topic, objective_code) VALUES (?, ?, ?)",
            (чужой, "Импульс тела", "10.1.4.1"), db_path=база)

    тело = клиент.get("/api/v1/ktp/objective", params={"topic": "Импульс тела"}, headers=по_почте()).json()
    assert тело["objective_code"] is None


def test_неизвестная_тема_не_придумывает_код(база, клиент):
    """Ничего не додумываем: нет в КТП — значит нет."""
    завести_педагога(база)
    тело = клиент.get("/api/v1/ktp/objective", params={"topic": "Квантовая хромодинамика"}, headers=по_почте()).json()
    assert тело["objective_code"] is None


def test_список_тем_ктп_только_свой(база, клиент):
    teacher_id = завести_педагога(база)
    execute("INSERT INTO ktp_entries (teacher_id, topic, section) VALUES (?, ?, ?)",
            (teacher_id, "Импульс тела", "Законы сохранения"), db_path=база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    execute("INSERT INTO ktp_entries (teacher_id, topic) VALUES (?, ?)", (чужой, "Чужая тема"), db_path=база)

    темы = клиент.get("/api/v1/ktp/entries", headers=по_почте()).json()["entries"]
    assert [т["topic"] for т in темы] == ["Импульс тела"]


# --- постановка генерации в очередь ---

def тело_генерации(template_id: int, **правки) -> dict:
    основа = {
        "topic": "Импульс тела",
        "razdel": "10.1В Законы сохранения",
        "subject": "физика",
        "klass": "10",
        "duration_minutes": 40,
        "template_id": template_id,
        "objective_code": "10.1.4.1",
    }
    основа.update(правки)
    return основа


def test_генерация_уходит_в_очередь_а_не_выполняется_в_запросе(база, клиент):
    """Сборка КСП — от 25 до 34 секунд по замерам, плюс ретраи."""
    teacher_id = завести_педагога(база)
    template_id = завести_шаблон(база)

    ответ = клиент.post("/api/v1/ksp/generate", json=тело_генерации(template_id), headers=по_почте())

    assert ответ.status_code == 200
    задачи = query("SELECT * FROM tasks", db_path=база)
    assert len(задачи) == 1
    задача = dict(задачи[0])
    assert задача["type"] == "generate_ksp"
    assert задача["status"] == "pending"
    payload = json.loads(задача["payload"])
    assert payload["teacher_id"] == teacher_id
    assert payload["topic"] == "Импульс тела"
    assert payload["source"] == SOURCE_WEB
    assert ответ.json()["task_id"] == задача["id"]


def test_без_согласия_генерация_не_ставится(база, клиент):
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", AUTH_UID), db_path=база)
    template_id = завести_шаблон(база)
    ответ = клиент.post("/api/v1/ksp/generate", json=тело_генерации(template_id), headers=по_почте())
    assert ответ.status_code == 403
    assert ответ.json()["error"]["code"] == CODE_CONSENT_REQUIRED
    assert query("SELECT * FROM tasks", db_path=база) == []


def test_настройки_урока_доезжают_до_задачи(база, клиент):
    завести_педагога(база)
    template_id = завести_шаблон(база)
    клиент.post("/api/v1/ksp/generate", headers=по_почте(), json=тело_генерации(
        template_id,
        options={"tip_uroka": "Комбинированный урок", "cennost_key": "trudolyubiye_professionalizm",
                 "ima_oop": True, "page_orientation": "album"},
    ))
    options = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])["options"]
    assert options["tip_uroka"] == "Комбинированный урок"
    assert options["cennost_key"] == "trudolyubiye_professionalizm"
    assert options["ima_oop"] is True
    assert options["page_orientation"] == "album"


def test_видов_деятельности_не_больше_трёх(база, клиент):
    """
    «До трёх» — ограничение, а не пожелание, и режет его сам
    LessonOptions. Второй копии правила во фронтенде нет.
    """
    завести_педагога(база)
    template_id = завести_шаблон(база)
    клиент.post("/api/v1/ksp/generate", headers=по_почте(), json=тело_генерации(
        template_id,
        options={"vidy_deyatelnosti": ["Парная работа", "Групповая работа", "Проектная деятельность", "Лишняя"]},
    ))
    options = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])["options"]
    assert len(options["vidy_deyatelnosti"]) == MAX_VIDY_DEYATELNOSTI


def test_неизвестная_настройка_отклоняется_а_не_молча_теряется(база, клиент):
    завести_педагога(база)
    template_id = завести_шаблон(база)
    ответ = клиент.post("/api/v1/ksp/generate", headers=по_почте(),
                        json=тело_генерации(template_id, options={"vydumannoe_pole": True}))
    assert ответ.status_code == 422
    assert query("SELECT * FROM tasks", db_path=база) == []


def test_пустые_поля_остаются_пустыми(база, клиент):
    """Недостающее не дописывается заглушками: пусто — значит пусто."""
    завести_педагога(база)
    template_id = завести_шаблон(база)
    клиент.post("/api/v1/ksp/generate", headers=по_почте(),
                json=тело_генерации(template_id, objective_code=None))
    payload = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])
    assert payload["objective_code"] is None
    assert payload["options"]["cennost_key"] is None


def test_чужой_конспект_к_генерации_не_прицепить(база, клиент):
    завести_педагога(база)
    template_id = завести_шаблон(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json) VALUES (?, ?, 'student', ?, ?)",
            ("k-чужой", чужой, "Чужая тема", "{}"), db_path=база)

    ответ = клиент.post("/api/v1/ksp/generate", headers=по_почте(),
                        json=тело_генерации(template_id, konspekt_id="k-чужой"))
    assert ответ.status_code == 404
    assert ответ.json()["error"]["code"] == CODE_NOT_FOUND
    assert query("SELECT * FROM tasks", db_path=база) == []


def test_свой_конспект_доезжает_текстом(база, клиент):
    teacher_id = завести_педагога(база)
    template_id = завести_шаблон(база)
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json) VALUES (?, ?, 'teacher', ?, ?)",
            ("k-свой", teacher_id, "Импульс тела",
             json.dumps({"transcript_text": "…импульс — векторная величина…"}, ensure_ascii=False)),
            db_path=база)

    клиент.post("/api/v1/ksp/generate", headers=по_почте(),
                json=тело_генерации(template_id, konspekt_id="k-свой"))
    payload = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])
    assert "векторная величина" in payload["konspekt_text"]
