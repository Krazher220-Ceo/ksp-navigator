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

Блок Ф2 (FRONTEND_PLAN.md) добавил сюда три вещи и ни одной не убрал:
роутер /api/v1/* для собственного фронтенда (web/api_v1.py), единый
формат ошибки для него (web/errors.py) и CORS на список адресов из
core/config.py. Старые /api/* остались как были — на них работает
Mini App в проде.

На что опирается: core.db, core.templates. Владение generated_ksp
проверяется здесь же: чужой ksp_id -> 404, не 403 — не подтверждаем
даже факт существования чужой записи (Б9.2).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.dashboard import collect as collect_dashboard
from core.db import execute, query
from core.templates import list_templates
from web.api_v1 import router as api_v1_router
from web.auth import AuthenticatedUser, verify_init_data
from web.errors import install_error_handlers

STATIC_DIR = Path(__file__).resolve().parent / "static"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = FastAPI(title="Mazmun — API")

# Ф2: браузеру кабинета разрешены только перечисленные адреса. Звёздочки
# здесь нет и быть не может — сервер публично доступен через Cloudflare
# Tunnel, и «разрешить всем» означало бы разрешить любому сайту дёргать
# API из браузера вошедшего человека. Куки не используются: кабинет
# ходит с заголовком, поэтому allow_credentials выключен.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    # Каждый заголовок, который кабинет отправляет сам, обязан быть здесь:
    # предполётный запрос браузера сверяется именно с этим списком, и
    # забытое имя выглядит как «сервер недоступен», а не как отказ. На
    # X-Filename я на этом уже попался (блок Ф6).
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Telegram-Init-Data",
        "X-Filename",
        "X-Konspekt-Mode",
        "X-Transcript-Id",
    ],
)

# Ф2: единый формат ошибки для /api/v1/*. Старые /api/* обработчики
# пропускают дальше, к обработчику FastAPI по умолчанию.
install_error_handlers(app)


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


@app.post("/api/template-selection")
async def api_save_template_selection(
    template_id: int = Body(embed=True),
    auth: AuthenticatedUser = Depends(verify_init_data),
) -> dict:
    """И1: сохраняет выбор для запуска Mini App из меню чата.

    sendData в этом контексте не доставляет сообщение боту, поэтому
    следующая /generate забирает запись из общей SQLite. Клиентскому id
    не доверяем: доступны только встроенные и собственные шаблоны учителя.
    """
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    available_ids = {template["id"] for template in list_templates(teacher_id)}
    if template_id not in available_ids:
        raise HTTPException(status_code=404, detail="шаблон не найден")

    execute(
        "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(telegram_user_id) DO UPDATE SET "
        "template_id = excluded.template_id, selected_at = excluded.selected_at",
        (auth.telegram_user_id, template_id, datetime.now(timezone.utc).isoformat()),
    )
    return {"template_id": template_id, "saved": True}


@app.get("/api/preview/{ksp_id}")
async def api_preview(ksp_id: str, auth: AuthenticatedUser = Depends(verify_init_data)) -> dict:
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    row = _get_owned_generated_ksp(ksp_id, teacher_id)
    return json.loads(row["content_json"]) if row["content_json"] else {}


@app.get("/api/dashboard")
async def api_dashboard(auth: AuthenticatedUser = Depends(verify_init_data)) -> dict:
    """М5.3: третий экран Mini App. Отдаёт РОВНО то, что вернул
    core.dashboard.collect() — без переформатирования, чтобы бот и
    Mini App не могли разойтись в числах (М5.1: один расчёт на оба
    представления). teacher_id=-1 (профиля нет, см. _resolve_teacher_id)
    превращается в None — collect() сам знает, что это не ошибка."""
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    return collect_dashboard(teacher_id if teacher_id != -1 else None)


@app.get("/api/download/{ksp_id}")
async def api_download(ksp_id: str, auth: AuthenticatedUser = Depends(verify_init_data)) -> FileResponse:
    teacher_id = _resolve_teacher_id(auth.telegram_user_id)
    row = _get_owned_generated_ksp(ksp_id, teacher_id)

    docx_path = Path(row["docx_path"])
    if not docx_path.exists():
        raise HTTPException(status_code=404, detail="файл не найден на диске")

    return FileResponse(docx_path, filename=docx_path.name, media_type=DOCX_MEDIA_TYPE)


# Ф2: версионированный API для собственного фронтенда. Включается ДО
# монтирования статики — Starlette проверяет маршруты в порядке
# регистрации, и роутер после mount никогда бы не сработал.
app.include_router(api_v1_router)


# Статика Mini App (блок Б10) — регистрируется ПОСЛЕДНЕЙ: раньше
# зарегистрированные /api/* маршруты имеют приоритет при совпадении
# путей (Starlette проверяет маршруты в порядке регистрации).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
