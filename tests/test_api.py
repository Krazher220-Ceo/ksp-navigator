"""
tests/test_api.py — тесты web/api.py (КГ блока Б9.2).

settings.db_path/telegram_bot_token временно подменяются (frozen
dataclass-синглтон, object.__setattr__ с восстановлением) — тот же
приём, что и в tests/test_bot_handlers.py. Настоящих HTTP-запросов в
сеть нет — FastAPI TestClient работает in-process.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from core.db import execute, init_db
from web.api import app
from web.auth import settings as auth_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
BOT_TOKEN = "123456:AAEtest-bot-token-for-tests-only"


def _build_init_data(user_id: int, bot_token: str = BOT_TOKEN) -> str:
    fields = {
        "query_id": "AAHtest",
        "user": json.dumps({"id": user_id, "first_name": "Т"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def isolated_api(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)

    original_db_path = auth_settings.db_path
    original_db_backend = auth_settings.db_backend
    original_token = auth_settings.telegram_bot_token
    # web/api.py тоже вызывает core.db.query/execute без явного db_path —
    # connect() смотрит на settings.db_backend, подмены одного db_path
    # недостаточно (блок Н0 PLAN.md, та же причина, что в test_bot_handlers.py).
    object.__setattr__(auth_settings, "db_path", db_path)
    object.__setattr__(auth_settings, "db_backend", "sqlite")
    object.__setattr__(auth_settings, "telegram_bot_token", BOT_TOKEN)
    try:
        yield db_path
    finally:
        object.__setattr__(auth_settings, "db_path", original_db_path)
        object.__setattr__(auth_settings, "db_backend", original_db_backend)
        object.__setattr__(auth_settings, "telegram_bot_token", original_token)


@pytest.fixture
def client():
    return TestClient(app)


def _headers(user_id: int) -> dict[str, str]:
    return {"X-Telegram-Init-Data": _build_init_data(user_id)}


# --- КГ Б9.2: запрос без initData -> 401 ---


@pytest.mark.parametrize(
    "path",
    ["/api/templates", "/api/preview/some-id", "/api/download/some-id"],
)
def test_request_without_init_data_returns_401(isolated_api, client, path):
    response = client.get(path)
    assert response.status_code == 401


def test_request_with_garbage_init_data_returns_401(isolated_api, client):
    response = client.get("/api/templates", headers={"X-Telegram-Init-Data": "hash=deadbeef&auth_date=1"})
    assert response.status_code == 401


# --- КГ Б9.2: запрос к чужому КСП -> 404 (не 403) ---


def test_preview_of_nonexistent_ksp_returns_404(isolated_api, client):
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 111)", db_path=isolated_api)
    response = client.get("/api/preview/does-not-exist", headers=_headers(111))
    assert response.status_code == 404


def test_preview_of_someone_elses_ksp_returns_404_not_403(isolated_api, client):
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Владелец', 'физика', 111)", db_path=isolated_api)
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (2, 'Чужой', 'физика', 222)", db_path=isolated_api)
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json, docx_path) "
        "VALUES ('ksp-1', 1, '{\"tema_uroka\": \"Секретная тема\"}', '/tmp/x.docx')",
        db_path=isolated_api,
    )

    # владелец видит свой КСП
    own_response = client.get("/api/preview/ksp-1", headers=_headers(111))
    assert own_response.status_code == 200
    assert own_response.json()["tema_uroka"] == "Секретная тема"

    # чужой человек получает 404, не 403 - не подтверждаем существование
    foreign_response = client.get("/api/preview/ksp-1", headers=_headers(222))
    assert foreign_response.status_code == 404


def test_download_of_someone_elses_ksp_returns_404(isolated_api, client, tmp_path):
    fake_docx = tmp_path / "result.docx"
    fake_docx.write_bytes(b"fake docx content")

    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Владелец', 'физика', 111)", db_path=isolated_api)
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (2, 'Чужой', 'физика', 222)", db_path=isolated_api)
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json, docx_path) VALUES ('ksp-2', 1, '{}', ?)",
        (str(fake_docx),),
        db_path=isolated_api,
    )

    own_response = client.get("/api/download/ksp-2", headers=_headers(111))
    assert own_response.status_code == 200
    assert own_response.content == b"fake docx content"

    foreign_response = client.get("/api/download/ksp-2", headers=_headers(222))
    assert foreign_response.status_code == 404


def test_download_of_ksp_without_file_path_returns_404_not_500(isolated_api, client):
    """docx_path в базе пустой — так выглядит строка, если генерация
    упала между записью в generated_ksp и сборкой файла.

    Path(None) кидает TypeError, то есть Mini App получал 500 и
    техническую ошибку вместо честного «не найдено». В /api/v1/download
    это уже сделано правильно (Path(... or "")) — здесь две копии одного
    правила разошлись."""
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (3, 'Владелец', 'физика', 333)",
        db_path=isolated_api,
    )
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json, docx_path) VALUES ('ksp-3', 3, '{}', NULL)",
        db_path=isolated_api,
    )

    response = client.get("/api/download/ksp-3", headers=_headers(333))
    assert response.status_code == 404


def test_nonexistent_and_foreign_ksp_give_identical_error_shape(isolated_api, client):
    """Дополнительная проверка духа "не подтверждаем существование":
    ответы на "нет такого id" и "это не твой id" должны быть
    неотличимы для клиента."""
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Владелец', 'физика', 111)", db_path=isolated_api)
    execute("INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (2, 'Чужой', 'физика', 222)", db_path=isolated_api)
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json, docx_path) VALUES ('ksp-3', 1, '{}', '/tmp/y.docx')",
        db_path=isolated_api,
    )

    not_found = client.get("/api/preview/truly-does-not-exist", headers=_headers(222))
    foreign = client.get("/api/preview/ksp-3", headers=_headers(222))

    assert not_found.status_code == foreign.status_code == 404
    assert not_found.json() == foreign.json()


# --- /api/templates: встроенные видны всем, свои - только владельцу ---


def test_templates_endpoint_returns_builtins_for_teacher_without_profile(isolated_api, client):
    from core.templates import load_builtin_templates

    load_builtin_templates(db_path=isolated_api)
    response = client.get("/api/templates", headers=_headers(999))  # учителя с таким id нет
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert all(template["structure_json"]["blocks"] for template in response.json())


def test_templates_endpoint_requires_auth(isolated_api, client):
    response = client.get("/api/templates")
    assert response.status_code == 401


def test_template_selection_endpoint_requires_auth(isolated_api, client):
    response = client.post("/api/template-selection", json={"template_id": 1})
    assert response.status_code == 401


def test_template_selection_endpoint_saves_latest_owned_template(isolated_api, client):
    from core.db import query
    from core.templates import load_builtin_templates

    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 555)",
        db_path=isolated_api,
    )
    load_builtin_templates(db_path=isolated_api)
    # Встроенный шаблон теперь один — второй, между которым тест
    # переключается, добавлен как свой шаблон учителя (то же самое видит
    # /api/templates, что и настоящий загруженный).
    execute(
        "INSERT INTO templates (name, is_official, is_builtin, category, uploaded_by, structure_json) "
        "VALUES ('Свой шаблон для теста', 0, 0, 'personal', 1, '{\"blocks\": []}')",
        db_path=isolated_api,
    )
    template_ids = [row["id"] for row in query("SELECT id FROM templates ORDER BY id", db_path=isolated_api)]

    first = client.post(
        "/api/template-selection", json={"template_id": template_ids[0]}, headers=_headers(555)
    )
    second = client.post(
        "/api/template-selection", json={"template_id": template_ids[1]}, headers=_headers(555)
    )

    assert first.status_code == second.status_code == 200
    rows = query("SELECT * FROM template_selections WHERE telegram_user_id = 555", db_path=isolated_api)
    assert len(rows) == 1
    assert rows[0]["template_id"] == template_ids[1]
    assert rows[0]["selected_at"]


def test_template_selection_endpoint_rejects_foreign_template(isolated_api, client):
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Владелец', 'физика', 111)",
        db_path=isolated_api,
    )
    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (2, 'Чужой', 'физика', 222)",
        db_path=isolated_api,
    )
    execute(
        "INSERT INTO templates (id, name, is_builtin, uploaded_by, structure_json) "
        "VALUES (99, 'Личный', 0, 1, '{\"blocks\": []}')",
        db_path=isolated_api,
    )

    response = client.post(
        "/api/template-selection", json={"template_id": 99}, headers=_headers(222)
    )

    assert response.status_code == 404


# --- М5.3: /api/dashboard ---


def test_dashboard_without_init_data_returns_401(isolated_api, client):
    response = client.get("/api/dashboard")
    assert response.status_code == 401


def test_dashboard_without_teacher_profile_has_profile_false(isolated_api, client):
    response = client.get("/api/dashboard", headers=_headers(222))
    assert response.status_code == 200
    assert response.json()["has_profile"] is False


def test_dashboard_matches_collect_output_exactly(isolated_api, client):
    """М5.3 КГ (дословно из плана): оба представления на одних и тех же
    данных показывают одинаковые числа — /api/dashboard не пересчитывает
    и не переформатирует, отдаёт ровно то, что вернул collect()."""
    from core.dashboard import collect

    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 333)",
        db_path=isolated_api,
    )
    execute(
        "INSERT INTO generated_ksp (id, teacher_id, content_json) VALUES ('ksp1', 1, '{}')",
        db_path=isolated_api,
    )

    response = client.get("/api/dashboard", headers=_headers(333))
    assert response.status_code == 200

    expected = collect(1, db_path=isolated_api)
    assert response.json() == expected


def test_dashboard_bot_text_and_api_json_agree_on_numbers(isolated_api, client):
    """То же самое, но сверяя с тем, что реально покажет бот
    (format_dashboard_text) — не только с промежуточным collect()."""
    from bot.handlers import format_dashboard_text

    execute(
        "INSERT INTO teachers (id, name, subject, telegram_user_id) VALUES (1, 'Т', 'физика', 444)",
        db_path=isolated_api,
    )
    for i in range(3):
        execute(
            f"INSERT INTO generated_ksp (id, teacher_id, content_json) VALUES ('ksp{i}', 1, '{{}}')",
            db_path=isolated_api,
        )

    response = client.get("/api/dashboard", headers=_headers(444))
    api_data = response.json()
    bot_text = format_dashboard_text(api_data)

    assert f"всего: {api_data['generated_ksp']['total']}" in bot_text
    assert api_data["generated_ksp"]["total"] == 3
