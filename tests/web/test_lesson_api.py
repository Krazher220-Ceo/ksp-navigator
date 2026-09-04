"""
tests/web/test_lesson_api.py — запись урока через веб (блок Ф6).

Главный сценарий продукта, и в нём три вещи, которые нельзя сломать
незаметно: аудио уходит в ту же очередь, что у бота, а не обрабатывается
в обработчике запроса; файл ложится на тот же путь, где его удалит тот же
пайплайн; чужая задача отдаёт 404, а не чужие данные.

Сети нет: TestClient in-process, база — SQLite во временной папке.
Настоящей расшифровки тоже нет — проверяется постановка в очередь.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import texts
from core.config import settings
from core.db import execute, init_db, query
from core.limits import WEB_AUDIO_MAX_BYTES
from core.queue import SOURCE_WEB
from tests.web.test_current_user import BOT_TOKEN, init_data
from tests.web.test_jwt import АДРЕС, СЕКРЕТ, собрать_токен
from web.api import app
from web.errors import CODE_BAD_REQUEST, CODE_CONSENT_REQUIRED, CODE_NOT_FOUND

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
AUTH_UID = "9c1e7b30-0000-4000-8000-000000000001"
АУДИО = b"\x00\x01\x02" * 100


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


def завести_педагога(db_path, telegram_user_id=None) -> int:
    execute(
        "INSERT INTO teachers (name, subject, auth_user_id, telegram_user_id) VALUES (?, ?, ?, ?)",
        ("Тлеубаева А.", "физика", AUTH_UID, telegram_user_id), db_path=db_path,
    )
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=db_path)
    return query("SELECT id FROM teachers ORDER BY id DESC", db_path=db_path)[0]["id"]


# Отдельная метка «заголовков не передавали»: пустой словарь — это тоже
# осознанный выбор (запрос без входа), и подменять его умолчанием нельзя.
БЕЗ_ЗАГОЛОВКОВ = object()


def загрузить(клиент, данные=АУДИО, имя="urok.m4a", заголовки=БЕЗ_ЗАГОЛОВКОВ, режим=None):
    if заголовки is БЕЗ_ЗАГОЛОВКОВ:
        заголовки = по_почте()
    шапка = {"Content-Type": "application/octet-stream", **заголовки}
    if имя is not None:
        шапка["X-Filename"] = имя
    if режим is not None:
        шапка["X-Konspekt-Mode"] = режим
    return клиент.post("/api/v1/lesson/upload", content=данные, headers=шапка)


# --- постановка в очередь ---

def test_запись_уходит_в_ту_же_очередь_что_у_бота(база, клиент):
    """
    Обработка не запускается в обработчике запроса: расшифровка занимает
    десятки секунд, и запрос на столько не живёт. Задача обязана лечь в
    tasks со статусом pending и типом transcribe — тем же, что у бота.
    """
    teacher_id = завести_педагога(база)
    ответ = загрузить(клиент)

    assert ответ.status_code == 200
    задачи = query("SELECT * FROM tasks", db_path=база)
    assert len(задачи) == 1
    задача = dict(задачи[0])
    assert задача["type"] == "transcribe"
    assert задача["status"] == "pending"
    payload = json.loads(задача["payload"])
    assert payload["teacher_id"] == teacher_id
    assert payload["mode"] == "student"
    assert payload["source"] == SOURCE_WEB
    assert ответ.json()["task_id"] == задача["id"]


def test_файл_ложится_туда_же_откуда_его_удалит_пайплайн(база, клиент):
    """
    Второго пути, который сохраняет запись «на всякий случай», в проекте
    нет: обработчик transcribe удаляет ровно то, что лежит в audio_paths,
    и при успехе, и при провале. Значит путь обязан быть внутри
    storage/uploads и попасть в payload.
    """
    завести_педагога(база)
    загрузить(клиент)

    payload = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])
    путь = Path(payload["audio_paths"][0])
    assert путь.parent == settings.uploads_dir
    assert путь.exists() and путь.read_bytes() == АУДИО


def test_у_педагога_с_telegram_задача_несёт_чат(база, клиент):
    """Привязан Telegram — бот напишет о ходе работы туда же, что и всегда."""
    завести_педагога(база, telegram_user_id=50101)
    загрузить(клиент)
    assert query("SELECT telegram_chat_id FROM tasks", db_path=база)[0]["telegram_chat_id"] == 50101


def test_без_telegram_задача_идёт_без_чата(база, клиент):
    """Педагог зарегистрировался по почте: слать некуда, и это не ошибка."""
    завести_педагога(база)
    загрузить(клиент)
    assert query("SELECT telegram_chat_id FROM tasks", db_path=база)[0]["telegram_chat_id"] is None


# --- отказы ---

def test_без_согласия_запись_не_принимается(база, клиент):
    execute("INSERT INTO teachers (name, subject, auth_user_id) VALUES (?, ?, ?)",
            ("Тлеубаева А.", "физика", AUTH_UID), db_path=база)
    ответ = загрузить(клиент)
    assert ответ.status_code == 403
    assert ответ.json()["error"]["code"] == CODE_CONSENT_REQUIRED
    assert query("SELECT * FROM tasks", db_path=база) == []
    assert list(settings.uploads_dir.iterdir()) == [], "файл не должен остаться на диске"


def test_без_профиля_педагога_запись_не_принимается(база, клиент):
    execute("INSERT INTO consents_web (auth_user_id, given_at) VALUES (?, CURRENT_TIMESTAMP)",
            (AUTH_UID,), db_path=база)
    ответ = загрузить(клиент)
    assert ответ.status_code == 403
    assert ответ.json()["error"]["message"] == texts.API_PROFILE_REQUIRED


def test_без_входа_запись_не_принимается(база, клиент):
    ответ = загрузить(клиент, заголовки={})
    assert ответ.status_code == 401
    assert list(settings.uploads_dir.iterdir()) == []


@pytest.mark.parametrize("имя", ["zapis.exe", "urok.txt", "spisok.docx"])
def test_не_аудио_отклоняется_до_записи_на_диск(база, клиент, имя):
    завести_педагога(база)
    ответ = загрузить(клиент, имя=имя)
    assert ответ.status_code == 415
    assert ответ.json()["error"]["message"] == texts.API_AUDIO_UNSUPPORTED
    assert list(settings.uploads_dir.iterdir()) == []


def test_пустой_файл_отклоняется(база, клиент):
    завести_педагога(база)
    ответ = загрузить(клиент, данные=b"")
    assert ответ.status_code == 400
    assert ответ.json()["error"]["message"] == texts.API_AUDIO_EMPTY
    assert list(settings.uploads_dir.iterdir()) == []


def test_слишком_большая_запись_отклоняется_и_не_остаётся_на_диске(база, клиент, monkeypatch):
    """
    Предел проверяется на сервере, а не только в браузере, и файл при
    обрыве не остаётся: недописанный кусок гигабайта на диске — это тот
    же мусор, только незаметный.
    """
    from core import limits
    from web import api_v1

    monkeypatch.setattr(api_v1, "WEB_AUDIO_MAX_BYTES", 1024)
    завести_педагога(база)
    ответ = загрузить(клиент, данные=b"x" * 5000)

    assert ответ.status_code == 413
    assert "МБ" in ответ.json()["error"]["message"]
    assert list(settings.uploads_dir.iterdir()) == []
    assert query("SELECT * FROM tasks", db_path=база) == []
    # Настоящий предел остался прежним — подменяли только на время теста.
    assert limits.WEB_AUDIO_MAX_BYTES == 100 * 1024 * 1024


def test_предел_назван_в_тексте_ошибки_числом(база, клиент):
    """Человек должен узнать число, а не «файл слишком большой»."""
    сообщение = texts.API_AUDIO_TOO_LARGE.format(limit_mb=WEB_AUDIO_MAX_BYTES // (1024 * 1024))
    assert "100 МБ" in сообщение
    assert "моно" in сообщение and "частями" in сообщение


@pytest.mark.parametrize("режим", ["nikakoy", "", "STUDENT"])
def test_неизвестный_режим_обработки_отклоняется(база, клиент, режим):
    завести_педагога(база)
    ответ = загрузить(клиент, режим=режим)
    assert ответ.status_code == 422


def test_режим_только_расшифровка_доезжает_до_задачи(база, клиент):
    завести_педагога(база)
    загрузить(клиент, режим="teacher")
    payload = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])
    assert payload["mode"] == "teacher"


# --- статус задачи ---

def test_статусы_очереди_переводятся_на_язык_кабинета(база, клиент):
    завести_педагога(база)
    task_id = загрузить(клиент).json()["task_id"]

    assert клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()["status"] == "queued"

    execute("UPDATE tasks SET status = 'processing' WHERE id = ?", (task_id,), db_path=база)
    assert клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()["status"] == "running"


def test_провал_доходит_до_кабинета_вместе_с_причиной(база, клиент):
    """
    Это второй канал гарантии уведомления, а не обход её: у задачи из
    кабинета телеграм-чата может не быть вовсе.
    """
    завести_педагога(база)
    task_id = загрузить(клиент).json()["task_id"]
    execute("UPDATE tasks SET status = 'failed', error = ? WHERE id = ?",
            ("провайдер не ответил", task_id), db_path=база)

    тело = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()
    assert тело["status"] == "failed"
    assert тело["error"] == "провайдер не ответил"


def test_готовая_задача_отдаёт_результат(база, клиент):
    завести_педагога(база)
    task_id = загрузить(клиент).json()["task_id"]
    execute("UPDATE tasks SET status = 'done', result = ? WHERE id = ?",
            (json.dumps({"transcript_id": "t-1"}), task_id), db_path=база)

    тело = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте()).json()
    assert тело["status"] == "done"
    assert тело["result"] == {"transcript_id": "t-1"}


def test_чужая_задача_отдаёт_404_а_не_403(база, клиент):
    """Не подтверждаем даже факт существования чужой записи (Б9.2)."""
    завести_педагога(база)
    task_id = загрузить(клиент).json()["task_id"]
    # Задача становится чужой: подменяем teacher_id в payload.
    payload = json.loads(query("SELECT payload FROM tasks", db_path=база)[0]["payload"])
    payload["teacher_id"] = 9999
    execute("UPDATE tasks SET payload = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), task_id), db_path=база)

    ответ = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте())
    assert ответ.status_code == 404
    assert ответ.json()["error"]["code"] == CODE_NOT_FOUND


def test_несуществующая_и_чужая_задача_неразличимы(база, клиент):
    завести_педагога(база)
    несуществующая = клиент.get("/api/v1/task/net-takoy", headers=по_почте())
    task_id = загрузить(клиент).json()["task_id"]
    execute("UPDATE tasks SET payload = ? WHERE id = ?",
            (json.dumps({"teacher_id": 9999}), task_id), db_path=база)
    чужая = клиент.get(f"/api/v1/task/{task_id}", headers=по_почте())

    assert несуществующая.status_code == чужая.status_code == 404
    assert несуществующая.json() == чужая.json()


# --- конспект ---

def test_конспект_отдаётся_вместе_с_расшифровкой(база, клиент):
    teacher_id = завести_педагога(база)
    execute("INSERT INTO transcripts (id, teacher_id, source, mode, text, duration_seconds, language) "
            "VALUES (?, ?, 'audio', 'student', ?, ?, 'ru')",
            ("t-1", teacher_id, "…импульс — векторная величина…", 2700), db_path=база)
    execute("INSERT INTO konspekty (id, teacher_id, transcript_id, mode, tema, content_json, docx_path) "
            "VALUES (?, ?, ?, 'student', ?, ?, ?)",
            ("k-1", teacher_id, "t-1", "Импульс тела",
             json.dumps({"konspekt_uchenika": {"tema": "Импульс тела"}}, ensure_ascii=False),
             "/tmp/k.docx"), db_path=база)

    тело = клиент.get("/api/v1/konspekt/k-1", headers=по_почте()).json()
    assert тело["tema"] == "Импульс тела"
    assert тело["transcript"]["duration_seconds"] == 2700
    assert "импульс" in тело["transcript"]["text"]
    assert тело["has_docx"] is True


def test_чужой_конспект_отдаёт_404(база, клиент):
    завести_педагога(база)
    execute("INSERT INTO teachers (name, telegram_user_id) VALUES (?, ?)", ("Другой", 4242), db_path=база)
    чужой = query("SELECT id FROM teachers ORDER BY id DESC", db_path=база)[0]["id"]
    execute("INSERT INTO konspekty (id, teacher_id, mode, tema, content_json) VALUES (?, ?, 'student', ?, ?)",
            ("k-чужой", чужой, "Чужая тема", "{}"), db_path=база)

    assert клиент.get("/api/v1/konspekt/k-чужой", headers=по_почте()).status_code == 404


# --- обработчик очереди и задача без телеграм-чата ---

class ФальшивыйБот:
    """Ровно то, что нужно обработчику: запоминает отправленное."""

    def __init__(self) -> None:
        self.сообщения: list[tuple] = []
        self.документы: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id is None:
            raise TypeError("chat_id обязателен — так падает настоящий aiogram")
        self.сообщения.append((chat_id, text))

    async def send_document(self, chat_id, *args, **kwargs):
        if chat_id is None:
            raise TypeError("chat_id обязателен")
        self.документы.append((chat_id, args))


@pytest.fixture
def обработчик_расшифровки(база, monkeypatch, tmp_path):
    """Обработчик transcribe с подменённой расшифровкой: настоящая ходит
    в xAI, а проверяем мы здесь не её, а поведение вокруг."""
    from bot import handlers

    async def подменённая_расшифровка(путь):
        return {"text": "…импульс — векторная величина…", "duration_seconds": 2700}

    async def подменённая_длительность(путь):
        return 2700

    monkeypatch.setattr(handlers, "transcribe", подменённая_расшифровка)
    monkeypatch.setattr(handlers, "_safe_probe", подменённая_длительность)
    бот = ФальшивыйБот()
    return handlers.make_transcribe_handler(бот), бот


async def _прогнать(обработчик, задача):
    return await обработчик(задача)


def _задача_расшифровки(база, chat_id, tmp_path, mode="teacher"):
    teacher_id = завести_педагога(база) if not query("SELECT id FROM teachers", db_path=база) else \
        query("SELECT id FROM teachers", db_path=база)[0]["id"]
    аудио = settings.uploads_dir / "zapis.m4a"
    аудио.write_bytes(АУДИО)
    return {
        "id": "task-1",
        "telegram_chat_id": chat_id,
        "retries": 0,
        "payload": {
            "teacher_id": teacher_id,
            "audio_paths": [str(аудио)],
            "mode": mode,
            "source": SOURCE_WEB,
        },
    }


def test_задача_из_кабинета_без_чата_не_падает(база, обработчик_расшифровки, tmp_path):
    """
    Пайплайн один на бота и на кабинет — второго быть не должно, иначе
    в одном из них аудио перестанут удалять. У задачи из кабинета чата
    может не быть вовсе, и обработчик обязан это пережить.
    """
    import asyncio

    обработчик, бот = обработчик_расшифровки
    задача = _задача_расшифровки(база, None, tmp_path)

    итог = asyncio.run(_прогнать(обработчик, задача))

    assert итог["mode"] == "teacher"
    assert бот.сообщения == [], "слать некуда — значит не слать"
    assert query("SELECT id FROM transcripts", db_path=база), "расшифровка обязана сохраниться"


def test_задача_из_telegram_по_прежнему_пишет_в_чат(база, обработчик_расшифровки, tmp_path):
    """Поведение для тех, у кого чат есть, не изменилось ни на строку."""
    import asyncio

    обработчик, бот = обработчик_расшифровки
    задача = _задача_расшифровки(база, 50101, tmp_path)

    asyncio.run(_прогнать(обработчик, задача))

    assert [chat for chat, _ in бот.сообщения] == [50101, 50101]


def test_аудио_удаляется_и_у_задачи_из_кабинета(база, обработчик_расшифровки, tmp_path):
    """
    Требование согласия: аудиофайлов на диске после обработки — ноль.
    Веб-загрузка идёт тем же путём, значит и удаление то же.
    """
    import asyncio

    обработчик, _ = обработчик_расшифровки
    задача = _задача_расшифровки(база, None, tmp_path)
    путь = Path(задача["payload"]["audio_paths"][0])
    assert путь.exists()

    asyncio.run(_прогнать(обработчик, задача))

    assert not путь.exists(), "аудио обязано исчезнуть с диска сразу после расшифровки"


def test_провал_задачи_из_кабинета_не_кричит_в_лог_про_kpi(база, caplog):
    """
    У задачи из кабинета телеграм-чата нет по существу, и это не
    нарушение гарантии уведомления: о провале узнают опросом статуса.
    А вот задача из бота без чата — по-прежнему повод для громкой записи.
    """
    import asyncio
    import logging

    from core import queue as очередь

    воркер = очередь.QueueWorker(handlers={}, notify=None, db_path=база)

    async def прогнать(payload):
        task_id = очередь.enqueue("transcribe", payload, chat_id=None, db_path=база)
        задача = query("SELECT * FROM tasks WHERE id = ?", (task_id,), db_path=база)[0]
        execute("UPDATE tasks SET retries = 99 WHERE id = ?", (task_id,), db_path=база)
        задача = dict(query("SELECT * FROM tasks WHERE id = ?", (task_id,), db_path=база)[0])
        await воркер._fail_and_maybe_notify(задача, "провайдер молчит")

    with caplog.at_level(logging.INFO, logger="core.queue"):
        asyncio.run(прогнать({"teacher_id": 1, "source": SOURCE_WEB}))
    уровни_веб = [з.levelno for з in caplog.records if "core.queue" in з.name]
    assert logging.ERROR not in уровни_веб
    assert any(з.levelno == logging.INFO for з in caplog.records if "core.queue" in з.name)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="core.queue"):
        asyncio.run(прогнать({"teacher_id": 1}))
    assert any(з.levelno == logging.ERROR for з in caplog.records if "core.queue" in з.name)
