"""
web/api.py — FastAPI для Mini App (блок Б9; статику и JS для самого
Mini App строит блок Б10, здесь только API и раздача этой статики).

Зачем модуль: единственная точка входа для web/static/*.js — отдаёт
шаблоны учителя, предпросмотр и .docx конкретной генерации. Cloudflare
Tunnel (блок Б11) делает этот сервер доступным из интернета, поэтому
Depends(verify_init_data) стоит на КАЖДОМ эндпоинте без исключений
(web/auth.py, блок Б9.1).

Что осознанно не делает: не редактирует шаблоны и не сохраняет правки
предпросмотра — Mini App (блок Б10) только смотрит и скачивает
(MASTER.md, п.7.1 и п.11). Не проверяет права на уровне ролей — есть
только "это твой generated_ksp или нет"; учитель без профиля видит
только встроенные шаблоны, это не отдельная ошибка, а естественное
следствие отсутствия personal templates.

На что опирается: core.db, core.templates. Владение generated_ksp
проверяется здесь же: чужой ksp_id -> 404, не 403 — не подтверждаем
даже факт существования чужой записи (Б9.2).
"""

import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.db import query
from core.templates import list_templates
from web.auth import AuthenticatedUser, verify_init_data

STATIC_DIR = Path(__file__).resolve().parent / "static"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = FastAPI(title="Учебный навигатор — API")


def _resolve_teacher_id(telegram_user_id: int, db_path=None) -> int:
    """id учителя по telegram_user_id, или -1 (заведомо несуществующий),
    если профиля ещё нет — так list_templates всё равно вернёт встроенные
    шаблоны, а проверка владения generated_ksp корректно даст 404, без
    отдельной ветки "профиля нет"."""
    rows = query("SELECT id FROM teachers WHERE telegram_user_id = ?", (telegram_user_id,), db_path=db_path)
    return rows[0]["id"] if rows else -1


def _get_owned_generated_ksp(ksp_id: str, teacher_id: int, db_path=None) -> dict:
    rows = query("SELECT * FROM generated_ksp WHERE id = ?", (ksp_id,), db_path=db_path)
    if not rows or rows[0]["teacher_id"] != teacher_id:
        # одна и та же ошибка что для "не найдено", что для "чужое" —
        # так клиент не может отличить два случая (Б9.2)
        raise HTTPException(status_code=404, detail="не найдено")
    return dict(rows[0])


@app.get("/api/templates")
async def api_templates(auth: AuthenticatedUser = Depends(verify_init_data)) -> list[dict]:
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    return list_templates(teacher_id)


@app.get("/api/preview/{ksp_id}")
async def api_preview(ksp_id: str, auth: AuthenticatedUser = Depends(verify_init_data)) -> dict:
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    row = _get_owned_generated_ksp(ksp_id, teacher_id)
    return json.loads(row["content_json"]) if row["content_json"] else {}


@app.get("/api/download/{ksp_id}")
async def api_download(ksp_id: str, auth: AuthenticatedUser = Depends(verify_init_data)) -> FileResponse:
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    row = _get_owned_generated_ksp(ksp_id, teacher_id)

    docx_path = Path(row["docx_path"])
    if not docx_path.exists():
        raise HTTPException(status_code=404, detail="файл не найден на диске")

    return FileResponse(docx_path, filename=docx_path.name, media_type=DOCX_MEDIA_TYPE)


# Статика Mini App (блок Б10) — регистрируется ПОСЛЕДНЕЙ: раньше
# зарегистрированные /api/* маршруты имеют приоритет при совпадении
# путей (Starlette проверяет маршруты в порядке регистрации).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
