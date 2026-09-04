"""
tests/web/test_analytics_api.py — закрытая аналитика продукта (блок Ф14).

Сторожит три вещи, каждая из которых закрывает продукту дорогу в школы,
если сломается: экран открыт по умолчанию; в аналитике появились имена
или содержание документов; появился рейтинг педагогов.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import analytics
from core.config import settings
from core.db import execute, init_db, query
from tests.web.test_current_user import BOT_TOKEN, init_data
from tests.web.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
AUTH_UID = "9c1e7b30-0000-4000-8000-000000000001"
АДМИН_TG = 777


@pytest.fixture
def база(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    было = (settings.db_path, settings.db_backend, settings.telegram_bot_token,
            settings.supabase_jwt_secret, settings.supabase_url, settings.analytics_enabled)
    object.__setattr__(settings, "db_path", db_path)
    object.__setattr__(settings, "db_backend", "sqlite")
    object.__setattr__(settings, "telegram_bot_token", BOT_TOKEN)
    object.__setattr__(settings, "supabase_jwt_secret", СЕКРЕТ)
    object.__setattr__(settings, "supabase_url", АДРЕС)
    try:
        yield db_path
    finally:
        for имя, значение in zip(
            ("db_path", "db_backend", "telegram_bot_token", "supabase_jwt_secret",
             "supabase_url", "analytics_enabled"), было
        ):
            object.__setattr__(settings, имя, значение)


@pytest.fixture
def клиент():
    return TestClient(app)


def включить_флаг():
    object.__setattr__(settings, "analytics_enabled", True)


def сделать_админом(db_path, telegram_user_id=АДМИН_TG):
    from datetime import datetime, timedelta

    from core.limits import KOSTANAY_TZ

    execute("INSERT INTO admin_access (telegram_user_id, expires_at) VALUES (?, ?)",
            (telegram_user_id, (datetime.now(KOSTANAY_TZ) + timedelta(days=1)).isoformat()),
            db_path=db_path)


def мир(db_path):
    execute("INSERT INTO teachers (name, subject, telegram_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", АДМИН_TG), db_path=db_path)
    teacher_id = query("SELECT id FROM teachers", db_path=db_path)[0]["id"]
    execute("INSERT INTO consents (telegram_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (АДМИН_TG,), db_path=db_path)
    execute("INSERT INTO generated_ksp (id, teacher_id, docx_path) VALUES ('g-1', ?, '/tmp/x.docx')",
            (teacher_id,), db_path=db_path)
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json) "
            "VALUES ('k-1', ?, 'student', 'СЕКРЕТНАЯ ТЕМА УРОКА', '{}')", (teacher_id,), db_path=db_path)
    execute("INSERT INTO tasks (id, type, status, payload, telegram_chat_id) "
            "VALUES ('t-1', 'generate_ksp', 'done', '{}', ?)", (АДМИН_TG,), db_path=db_path)
    execute("INSERT INTO tasks (id, type, status, payload, telegram_chat_id) "
            "VALUES ('t-2', 'transcribe', 'failed', '{}', ?)", (АДМИН_TG,), db_path=db_path)
    return teacher_id


# --- два замка ---

def test_по_умолчанию_экрана_как_будто_нет(база, клиент):
    """
    Флаг выключен — 404, а не 403: отказ «у вас нет прав» рассказывает о
    существовании закрытого экрана тому, кому знать о нём незачем.

    Флаг выставляется здесь ЯВНО, а не берётся «по умолчанию» из
    окружения. Раньше тест читал настоящий `.env` рабочей машины и был
    зелёным ровно потому, что там ANALYTICS_ENABLED не стоял. Стоило
    автору включить аналитику у себя — 04.09.2026 — и тест покраснел, не
    поймав ни одной ошибки в коде. Тот же класс дефекта, что у теста
    аптайма с зашитой датой: результат зависел от машины, а не от того,
    что он проверяет.
    """
    мир(база)
    сделать_админом(база)
    object.__setattr__(settings, "analytics_enabled", False)
    ответ = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)})
    assert ответ.status_code == 404


def test_с_флагом_но_без_admin_access_тоже_404(база, клиент):
    мир(база)
    включить_флаг()
    ответ = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)})
    assert ответ.status_code == 404


def test_оба_замка_сняты_экран_открывается(база, клиент):
    мир(база)
    включить_флаг()
    сделать_админом(база)
    ответ = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)})
    assert ответ.status_code == 200
    assert ответ.json()["active_teachers"] >= 1


def test_вошедшему_по_почте_аналитика_недоступна(база, клиент):
    """admin_access ключуется telegram_user_id; выдумывать второй путь
    к закрытому экрану мы не будем."""
    мир(база)
    включить_флаг()
    сделать_админом(база)
    execute("UPDATE teachers SET auth_user_id = ? WHERE telegram_user_id = ?",
            (AUTH_UID, АДМИН_TG), db_path=база)
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=база)

    ответ = клиент.get("/api/v1/analytics", headers={"Authorization": "Bearer " + собрать_токен()})
    assert ответ.status_code == 404


def test_me_говорит_показывать_ли_пункт_меню(база, клиент):
    мир(база)
    шапка = {"X-Telegram-Init-Data": init_data(АДМИН_TG)}
    assert клиент.get("/api/v1/me", headers=шапка).json()["analytics_available"] is False

    включить_флаг()
    сделать_админом(база)
    assert клиент.get("/api/v1/me", headers=шапка).json()["analytics_available"] is True


# --- что именно отдаётся ---

def test_в_аналитике_нет_ни_имён_ни_тем(база, клиент):
    """
    Содержание уроков и конспектов в аналитику не попадает. Мы считаем
    события — «конспект собран», — а не то, что в нём написано.
    """
    мир(база)
    включить_флаг()
    сделать_админом(база)
    текст = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)}).text

    assert "СЕКРЕТНАЯ ТЕМА УРОКА" not in текст
    assert "Тлеубаева" not in текст


def test_в_ответе_только_числа_и_названия_действий(база, клиент):
    мир(база)
    включить_флаг()
    сделать_админом(база)
    тело = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)}).json()

    def числа_ли(значение) -> bool:
        if isinstance(значение, bool):
            return False
        if isinstance(значение, (int, float)):
            return True
        if isinstance(значение, list):
            return all(числа_ли(э) for э in значение)
        if isinstance(значение, dict):
            return all(числа_ли(э) for э in значение.values())
        return False

    assert числа_ли(тело), f"в аналитике появилось что-то кроме чисел: {тело}"


def test_идентификаторов_людей_в_ответе_нет(база, клиент):
    мир(база)
    включить_флаг()
    сделать_админом(база)
    текст = клиент.get("/api/v1/analytics", headers={"X-Telegram-Init-Data": init_data(АДМИН_TG)}).text
    assert str(АДМИН_TG) not in текст, "telegram_user_id не должен просочиться в аналитику"


def test_рейтинга_педагогов_нет_в_коде():
    """
    Как только аналитика учебного процесса превращается в оценку
    человека, доступ в школы закрывается — и правильно делает.
    """
    исходник = (PROJECT_ROOT / "core" / "analytics.py").read_text(encoding="utf-8")
    без_комментариев = re.sub(r'"""[\s\S]*?"""', "", исходник)
    без_комментариев = "\n".join(
        с for с in без_комментариев.splitlines() if not с.strip().startswith("#")
    )
    for запрещённое in ["ORDER BY", "rating", "рейтинг", "top", "лучш", "худш"]:
        assert запрещённое.lower() not in без_комментариев.lower(), (
            f"в аналитике появилось ранжирование: {запрещённое}"
        )


def test_расчёт_не_читает_содержимое_документов():
    исходник = (PROJECT_ROOT / "core" / "analytics.py").read_text(encoding="utf-8")
    for колонка in ["content_json", "t.text", "SELECT text", "tema,", "topic,"]:
        assert колонка not in исходник, f"аналитика читает содержимое: {колонка}"


def test_активность_считается_по_документам_а_не_по_регистрациям(база):
    """
    Зарегистрироваться легко, вернуться трудно. Педагог попадает в число
    активных, только если собрал документ.
    """
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)",
            ("Зашёл и ушёл", 999), db_path=база)
    итог = analytics.collect(db_path=база)
    assert итог["active_teachers"] == 0


def test_пункт_меню_дорисовывается_а_не_отключается():
    """
    Экрана, к которому у человека нет доступа, для него не существует.
    Отключённый пункт рассказывает о закрытом экране всем подряд.
    """
    боковое = (PROJECT_ROOT / "frontend" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert "аналитика\n    ? [...базовое" in боковое or "аналитика ? [...базовое" in боковое
    assert "disabled" not in боковое.lower()


def test_страница_аналитики_не_показывает_имён():
    """В вёрстке нет ни одного поля, которого сервер и не отдаёт."""
    страница = (PROJECT_ROOT / "frontend" / "app" / "(cabinet)" / "app" / "analitika"
                / "page.tsx").read_text(encoding="utf-8")
    for запрещённое in ["teacher_name", "profile?.name", "students", "tema", "topic"]:
        assert запрещённое not in страница, f"на экране аналитики появилось {запрещённое}"
