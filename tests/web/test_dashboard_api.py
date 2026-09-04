"""
tests/web/test_dashboard_api.py — дэшборд кабинета (блок Ф5).

Главное, что здесь сторожится: кабинет показывает РОВНО то, что посчитал
core.dashboard.collect(). Как только в эндпоинте появится хоть одно
сложение, бот и кабинет разойдутся в числах — и заметить это можно будет
только глазами, на живом уроке.

Сети нет, база — SQLite во временной папке.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from core.dashboard import collect
from core.db import execute, init_db, query
from tests.web.test_current_user import BOT_TOKEN, init_data
from tests.web.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
AUTH_UID = "9c1e7b30-0000-4000-8000-000000000001"

# Поля, добавленные эндпоинтом сверх расчёта. Всё остальное обязано
# совпадать с collect() побайтово.
ДОБАВЛЕНО = {"generated_at", "generated_at_label"}


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


def завести_педагога(db_path, telegram_user_id=None) -> int:
    execute(
        "INSERT INTO teachers (name, subject, auth_user_id, telegram_user_id) VALUES (?, ?, ?, ?)",
        ("Тлеубаева А.", "физика", None if telegram_user_id else AUTH_UID, telegram_user_id),
        db_path=db_path,
    )
    return query("SELECT id FROM teachers ORDER BY id DESC", db_path=db_path)[0]["id"]


# --- контракт ---

def test_дэшборд_без_входа_отвечает_401(база, клиент):
    assert клиент.get("/api/v1/dashboard").status_code == 401


def test_числа_совпадают_с_core_dashboard_до_единого(база, клиент):
    """
    core/dashboard.py — единственное место расчёта. Эндпоинт обязан быть
    тонкой обёрткой, и это проверяется сравнением всего словаря, а не
    отдельных полей: приписка нового ключа или пересчёт старого тут же
    покраснеют.
    """
    teacher_id = завести_педагога(база)
    execute("INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, ?, ?)",
            (teacher_id, "Импульс тела", (date.today() + timedelta(days=2)).isoformat()), db_path=база)

    из_апи = клиент.get("/api/v1/dashboard", headers=по_почте()).json()
    из_расчёта = collect(teacher_id, db_path=база)

    assert set(из_апи) - set(из_расчёта) == ДОБАВЛЕНО
    for ключ, значение in из_расчёта.items():
        assert из_апи[ключ] == значение, f"поле {ключ} переписано эндпоинтом"


def test_бот_и_кабинет_видят_одни_и_те_же_числа(база, клиент):
    """Тот же расчёт, что читает текстовая сводка бота."""
    teacher_id = завести_педагога(база, telegram_user_id=777)
    execute("INSERT INTO generated_ksp (id, teacher_id, docx_path) VALUES (?, ?, ?)",
            ("ksp-1", teacher_id, "/tmp/x.docx"), db_path=база)

    из_апи = клиент.get("/api/v1/dashboard", headers={"X-Telegram-Init-Data": init_data(777)}).json()
    assert из_апи["generated_ksp"] == collect(teacher_id, db_path=база)["generated_ksp"]
    assert из_апи["generated_ksp"]["total"] == 1


# --- профиля нет ---

def test_без_профиля_отдаётся_общее_а_не_пустота(база, клиент):
    """
    Ранний выход «профиля нет — показывать нечего» уже был ошибкой в
    Mini App. Ответ обязан прийти целиком, с флагом has_profile=False.
    """
    ответ = клиент.get("/api/v1/dashboard", headers=по_почте())
    assert ответ.status_code == 200
    тело = ответ.json()
    assert тело["has_profile"] is False
    # Все разделы на месте — фронтенду не приходится гадать, чего нет.
    for раздел in ("queue", "generated_ksp", "ktp_coverage", "usage_today", "uptime"):
        assert раздел in тело
    assert тело["upcoming_lessons_without_ksp"] == []


def test_чужой_профиль_в_ответ_не_попадает(база, клиент):
    """Педагог видит свои числа, а не первые попавшиеся из таблицы."""
    чужой = завести_педагога(база, telegram_user_id=999)
    execute("INSERT INTO generated_ksp (id, teacher_id, docx_path) VALUES (?, ?, ?)",
            ("ksp-чужой", чужой, "/tmp/x.docx"), db_path=база)

    тело = клиент.get("/api/v1/dashboard", headers=по_почте()).json()
    assert тело["has_profile"] is False
    assert тело["generated_ksp"]["total"] == 0


# --- нераспознанные даты ---

def test_нераспознанная_дата_не_превращается_в_сегодня(база, клиент):
    """
    В реальных КТП planned_date выглядит как «01-08.09.23», и часть строк
    не парсится вовсе. Такие уроки честно уходят в «не распознано», а не
    всплывают в списке ближайших сегодняшним числом.
    """
    teacher_id = завести_педагога(база)
    execute("INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, ?, ?)",
            (teacher_id, "Тема без даты", "сентябрь, 2 неделя"), db_path=база)

    тело = клиент.get("/api/v1/dashboard", headers=по_почте()).json()
    assert тело["unparsed_planned_dates"] == 1
    assert тело["upcoming_lessons_without_ksp"] == []


def test_диапазон_дней_разбирается_первым_днём(база, клиент):
    """«01-08.09.26» — это неделя, показываем её началом."""
    teacher_id = завести_педагога(база)
    в_будущем = date.today() + timedelta(days=20)
    execute("INSERT INTO ktp_entries (teacher_id, topic, planned_date) VALUES (?, ?, ?)",
            (teacher_id, "Реактивное движение",
             f"{в_будущем.day:02d}-{min(в_будущем.day + 7, 28):02d}.{в_будущем.month:02d}.{в_будущем.year % 100:02d}"),
            db_path=база)

    тело = клиент.get("/api/v1/dashboard", headers=по_почте()).json()
    assert тело["unparsed_planned_dates"] == 0
    assert тело["upcoming_lessons_without_ksp"][0]["topic"] == "Реактивное движение"


# --- «данные на ЧЧ:ММ» ---

def test_время_сборки_приходит_готовой_подписью(база, клиент):
    """
    Показать надо время тех данных, что отдал сервер, поэтому подпись
    собирает он же. Браузеру считать нечего.
    """
    import re

    тело = клиент.get("/api/v1/dashboard", headers=по_почте()).json()
    assert re.fullmatch(r"\d{2}:\d{2}", тело["generated_at_label"])
    assert тело["generated_at"].count("T") == 1
