"""
tests/web/test_history_api.py — история документов и скачивание (блок Ф9).

Сторожит три вещи: чужой документ отдаёт 404 при любом запросе; PDF есть
только у конспекта; повторить можно только то, что действительно можно
повторить — у расшифровки и сверки исходник уже удалён.

Сети нет, LibreOffice не вызывается: конвертация подменяется.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import texts
from core.config import settings
from core.db import execute, init_db, query
from core.queue import SOURCE_WEB, enqueue
from tests.web.test_current_user import BOT_TOKEN
from tests.web.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app
from web.errors import CODE_NOT_FOUND

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


def завести_конспект(db_path, teacher_id, doc_id="k-1", tema="Импульс тела") -> Path:
    файл = settings.generated_dir / f"{doc_id}.docx"
    файл.write_bytes(b"docx")
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json, docx_path) "
            "VALUES (?, ?, 'student', ?, '{}', ?)",
            (doc_id, teacher_id, tema, str(файл)), db_path=db_path)
    return файл


def завести_ксп(db_path, teacher_id, doc_id="ksp-1") -> Path:
    файл = settings.generated_dir / f"{doc_id}.docx"
    файл.write_bytes(b"docx")
    execute("INSERT INTO generated_ksp (id, teacher_id, docx_path) VALUES (?, ?, ?)",
            (doc_id, teacher_id, str(файл)), db_path=db_path)
    return файл


# --- история ---

def test_собранное_и_несобранное_в_одном_списке(база, клиент):
    """
    Провалившаяся задача — такая же часть истории, как готовый документ.
    Молча теряться она не должна.
    """
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    завести_ксп(база, teacher_id)
    task_id = enqueue("generate_ksp", {"teacher_id": teacher_id, "topic": "Импульс"}, db_path=база)
    execute("UPDATE tasks SET status = 'failed', error = ? WHERE id = ?",
            ("провайдер не ответил", task_id), db_path=база)

    тело = клиент.get("/api/v1/history", headers=по_почте()).json()
    виды = sorted(з["kind"] for з in тело["items"])
    assert виды == ["failed", "konspekt", "ksp"]
    assert тело["counts"] == {"konspekt": 1, "ksp": 1, "failed": 1}


def test_ксп_всегда_помечен_черновиком(база, клиент):
    """Слово «черновик» обязательно везде, где речь о сгенерированном."""
    teacher_id = завести_педагога(база)
    завести_ксп(база, teacher_id)
    запись = клиент.get("/api/v1/history", headers=по_почте()).json()["items"][0]
    assert запись["status"] == "draft"


def test_чужие_документы_в_историю_не_попадают(база, клиент):
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    завести_конспект(база, чужой, doc_id="k-чужой", tema="Чужая тема")
    task_id = enqueue("generate_ksp", {"teacher_id": чужой, "topic": "Чужая"}, db_path=база)
    execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task_id,), db_path=база)

    тело = клиент.get("/api/v1/history", headers=по_почте()).json()
    assert [з["title"] for з in тело["items"]] == ["Импульс тела"]


def test_фильтр_по_виду_документа(база, клиент):
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    завести_ксп(база, teacher_id)

    только_ксп = клиент.get("/api/v1/history", params={"kind": "ksp"}, headers=по_почте()).json()
    assert [з["kind"] for з in только_ксп["items"]] == ["ksp"]


def test_фильтр_по_периоду(база, клиент):
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id, doc_id="k-старый")
    execute("UPDATE konspekty SET created_at = '2026-01-15 10:00:00' WHERE id = 'k-старый'", db_path=база)
    завести_конспект(база, teacher_id, doc_id="k-новый")

    тело = клиент.get("/api/v1/history", params={"since": "2026-06-01"}, headers=по_почте()).json()
    assert [з["id"] for з in тело["items"]] == ["k-новый"]


def test_история_без_профиля_пустая_а_не_чужая(база, клиент):
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers", db_path=база)[0]["id"]
    завести_конспект(база, чужой)
    тело = клиент.get("/api/v1/history", headers=по_почте()).json()
    assert тело["items"] == []


# --- скачивание ---

def test_docx_отдаётся_тот_же_что_собрал_обработчик(база, клиент):
    teacher_id = завести_педагога(база)
    файл = завести_конспект(база, teacher_id)
    ответ = клиент.get("/api/v1/download/konspekt/k-1", headers=по_почте())
    assert ответ.status_code == 200
    assert ответ.content == файл.read_bytes()


def test_чужой_документ_отдаёт_404(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    завести_конспект(база, чужой, doc_id="k-чужой")

    ответ = клиент.get("/api/v1/download/konspekt/k-чужой", headers=по_почте())
    assert ответ.status_code == 404
    assert ответ.json()["error"]["code"] == CODE_NOT_FOUND


def test_несуществующий_и_чужой_документ_неразличимы(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    завести_конспект(база, чужой, doc_id="k-чужой")

    нет = клиент.get("/api/v1/download/konspekt/net-takogo", headers=по_почте())
    чужое = клиент.get("/api/v1/download/konspekt/k-чужой", headers=по_почте())
    assert нет.status_code == чужое.status_code == 404
    assert нет.json() == чужое.json()


def test_pdf_есть_только_у_конспекта(база, клиент, monkeypatch):
    """
    У КСП PDF нет и не возвращается: его правят перед утверждением и
    печатают из .docx (решение блока П1).
    """
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    завести_ксп(база, teacher_id)

    from web import api_v1

    def сделать_pdf(путь, output_dir=None):
        pdf = Path(путь).with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-1.4")
        return pdf

    monkeypatch.setattr(api_v1, "convert_docx_to_pdf", сделать_pdf)

    конспект = клиент.get("/api/v1/download/konspekt/k-1", params={"format": "pdf"}, headers=по_почте())
    assert конспект.status_code == 200
    assert конспект.content.startswith(b"%PDF")

    ксп = клиент.get("/api/v1/download/ksp/ksp-1", params={"format": "pdf"}, headers=по_почте())
    assert ксп.status_code == 404


def test_в_истории_у_ксп_pdf_не_обещан(база, клиент):
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    завести_ксп(база, teacher_id)
    записи = {з["kind"]: з for з in клиент.get("/api/v1/history", headers=по_почте()).json()["items"]}
    assert записи["konspekt"]["has_pdf"] is True
    assert записи["ksp"]["has_pdf"] is False


def test_неизвестный_формат_отклоняется(база, клиент):
    teacher_id = завести_педагога(база)
    завести_конспект(база, teacher_id)
    ответ = клиент.get("/api/v1/download/konspekt/k-1", params={"format": "djvu"}, headers=по_почте())
    assert ответ.status_code == 422


def test_пропавший_с_диска_файл_отдаёт_404_а_не_пятисотый(база, клиент):
    teacher_id = завести_педагога(база)
    файл = завести_конспект(база, teacher_id)
    файл.unlink()
    assert клиент.get("/api/v1/download/konspekt/k-1", headers=по_почте()).status_code == 404


# --- повтор ---

def test_провалившаяся_генерация_повторяется(база, клиент):
    teacher_id = завести_педагога(база)
    task_id = enqueue("generate_ksp", {"teacher_id": teacher_id, "topic": "Импульс", "source": SOURCE_WEB},
                      db_path=база)
    execute("UPDATE tasks SET status = 'failed', retries = 3, error = 'сбой' WHERE id = ?",
            (task_id,), db_path=база)

    ответ = клиент.post(f"/api/v1/task/{task_id}/retry", headers=по_почте())

    assert ответ.status_code == 200
    задача = dict(query("SELECT * FROM tasks WHERE id = ?", (task_id,), db_path=база)[0])
    assert задача["status"] == "pending"
    assert задача["retries"] == 0, "это новая попытка человека, а не продолжение серии"
    assert задача["error"] is None


@pytest.mark.parametrize("тип,текст", [
    ("transcribe", texts.KONSPEKT_TRANSCRIBE_RETRY_DISABLED),
    ("sverka_tetradi", texts.SVERKA_RETRY_DISABLED),
])
def test_повтор_невозможен_там_где_исходник_удалён(база, клиент, тип, текст):
    """
    Аудио и фото удаляются сразу после обработки — и при провале тоже.
    Повторять там нечего, и человеку надо сказать об этом словами бота, а
    не притвориться, что попытка пошла.
    """
    teacher_id = завести_педагога(база)
    task_id = enqueue(тип, {"teacher_id": teacher_id}, db_path=база)
    execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task_id,), db_path=база)

    ответ = клиент.post(f"/api/v1/task/{task_id}/retry", headers=по_почте())
    assert ответ.status_code == 422
    assert ответ.json()["error"]["message"] == текст
    assert query("SELECT status FROM tasks WHERE id = ?", (task_id,), db_path=база)[0]["status"] == "failed"


def test_в_истории_видно_что_повторить_нельзя(база, клиент):
    teacher_id = завести_педагога(база)
    task_id = enqueue("transcribe", {"teacher_id": teacher_id}, db_path=база)
    execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task_id,), db_path=база)
    запись = клиент.get("/api/v1/history", headers=по_почте()).json()["items"][0]
    assert запись["can_retry"] is False


def test_чужую_задачу_не_повторить(база, клиент):
    завести_педагога(база)
    task_id = enqueue("generate_ksp", {"teacher_id": 9999}, db_path=база)
    execute("UPDATE tasks SET status = 'failed' WHERE id = ?", (task_id,), db_path=база)
    assert клиент.post(f"/api/v1/task/{task_id}/retry", headers=по_почте()).status_code == 404


def test_успешную_задачу_повторять_нечего(база, клиент):
    teacher_id = завести_педагога(база)
    task_id = enqueue("generate_ksp", {"teacher_id": teacher_id}, db_path=база)
    execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,), db_path=база)
    assert клиент.post(f"/api/v1/task/{task_id}/retry", headers=по_почте()).status_code == 422
