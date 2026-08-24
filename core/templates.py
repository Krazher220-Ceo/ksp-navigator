"""
core/templates.py — библиотека шаблонов КСП: встроенные + загруженные учителями.

Зачем модуль: шаблон описывает ФОРМУ документа (какие блоки, какие
колонки в «Ходе урока»), а не манеру письма конкретного учителя — это
отдельная сущность от style_profiles (core/ksp_parser.py, блок Б3),
и здесь она не пересекается с профилем стиля ни в одну сторону
(MASTER.md, п.1.1.1 и п.11).

Формат structure_json (единый для всех шаблонов, встроенных и
загруженных) — список блоков документа:

    {
      "blocks": [
        {"key": "shapka", "fields": [...]},        // поля шапки документа
        {"key": "tema", "fields": [...]},           // тема урока
        {"key": "celi", "fields": [...]},           // цели обучения/урока
        {"key": "hod_uroka", "columns": [...]},     // колонки таблицы "Ход урока"
        {"key": "primechanie", "fields": [...]}     // текст примечания
      ]
    }

Роли колонок hod_uroka — из того же набора, что распознаёт
core.ksp_parser._match_cell_role: etap_vremya, deystviya_pedagoga,
deystviya_uchenika, resursy, ocenivanie (плюс свои у отдельных
шаблонов — например domashnee_zadanie, dop_literatura).

Что осознанно не делает: не рендерит .docx (это core/docx_builder.py,
блок Б5) и не восстанавливает построчно шапку/тему/цели загруженного
пользователем файла — core.ksp_parser не делает сопоставления
label:value (это не входит в задачи Б3), поэтому save_user_template
берёт официальный набор полей шапки/темы/целей как разумный дефолт;
реально по содержимому файла определяются только колонки "Ход урока".

На что опирается: core.db (запись/чтение SQLite), core.ksp_parser
(разбор загруженного .docx учителя), core.config.settings (путь к
storage/builtin_templates/).
"""

import json
from pathlib import Path

from core.config import settings
from core.db import execute, query
from core.ksp_parser import parse_ksp

BUILTIN_TEMPLATES_DIR = settings.builtin_templates_dir

# Дефолтные поля блоков официальной формы — используются как разумное
# приближение для шапки/темы/целей загруженного пользователем шаблона
# (см. докстринг модуля: построчно это не восстанавливается).
_OFFICIAL_BLOCK_DEFAULTS = {
    "shapka": ["organizaciya", "razdel", "fio_pedagoga", "data", "klass", "prisutstvuet", "otsutstvuet"],
    "tema": ["tema_uroka"],
    "celi": ["celi_obucheniya", "celi_uroka"],
    "primechanie": ["adaptaciya_oop"],
}

_CANONICAL_ROLE_ORDER = ["etap_vremya", "deystviya_pedagoga", "deystviya_uchenika", "resursy", "ocenivanie"]


def _row_to_template_dict(row) -> dict:
    """sqlite3.Row -> обычный dict, structure_json распарсен в объект
    (в базе это TEXT, но по контракту всегда JSON — вызывающему коду
    удобнее сразу получать объект, а не парсить строку самому)."""
    result = dict(row)
    if result.get("structure_json"):
        result["structure_json"] = json.loads(result["structure_json"])
    return result


# --- Б4.2: загрузка встроенных шаблонов ---


def load_builtin_templates(db_path=None) -> int:
    """Читает все storage/builtin_templates/*.json и вставляет их в
    таблицу templates (is_builtin=1). Идемпотентно: шаблон с уже
    существующим именем среди встроенных повторно не вставляется — в
    schema.sql нет UNIQUE-ограничения на имя шаблона (менять уже
    принятую в Б1 схему не входит в задачи этого блока), поэтому
    уникальность здесь держит сама функция. Возвращает число реально
    вставленных (новых) шаблонов — 0 при повторном вызове."""
    inserted = 0
    for path in sorted(BUILTIN_TEMPLATES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data["name"]

        existing = query(
            "SELECT id FROM templates WHERE is_builtin = 1 AND name = ?",
            (name,),
            db_path=db_path,
        )
        if existing:
            continue

        structure_json = json.dumps({"blocks": data["blocks"]}, ensure_ascii=False)
        execute(
            "INSERT INTO templates "
            "(name, description, source, is_official, is_builtin, uploaded_by, structure_json) "
            "VALUES (?, ?, ?, ?, 1, NULL, ?)",
            (
                name,
                data.get("description"),
                data["source"],
                int(data["is_official"]),
                structure_json,
            ),
            db_path=db_path,
        )
        inserted += 1
    return inserted


# --- Б4.3: чтение шаблонов ---


def list_templates(teacher_id: int, db_path=None) -> list[dict]:
    """Встроенные шаблоны + свои шаблоны этого учителя. Чужие
    пользовательские шаблоны сюда не попадают — фильтр по
    is_builtin=1 OR uploaded_by=teacher_id."""
    rows = query(
        "SELECT * FROM templates WHERE is_builtin = 1 OR uploaded_by = ? "
        "ORDER BY is_official DESC, is_builtin DESC, id",
        (teacher_id,),
        db_path=db_path,
    )
    return [_row_to_template_dict(row) for row in rows]


def get_template(template_id: int, db_path=None) -> dict | None:
    rows = query("SELECT * FROM templates WHERE id = ?", (template_id,), db_path=db_path)
    if not rows:
        return None
    return _row_to_template_dict(rows[0])


# --- Б4.3: загрузка своего шаблона ---


def _structure_from_parsed_ksp(parsed: dict) -> dict:
    """Строит structure_json для шаблона, загруженного пользователем.
    Единственное, что реально определяется по содержимому файла, —
    колонки таблицы "Ход урока" (если она была найдена parse_ksp);
    шапка/тема/цели/примечание берутся из официального набора полей
    как разумный дефолт (см. докстринг модуля)."""
    lesson_table = parsed.get("lesson_plan_table")
    if lesson_table and lesson_table.get("columns"):
        found_roles = set(lesson_table["columns"].keys())
        hod_uroka_columns = [role for role in _CANONICAL_ROLE_ORDER if role in found_roles]
    else:
        hod_uroka_columns = list(_CANONICAL_ROLE_ORDER)

    return {
        "blocks": [
            {"key": "shapka", "fields": _OFFICIAL_BLOCK_DEFAULTS["shapka"]},
            {"key": "tema", "fields": _OFFICIAL_BLOCK_DEFAULTS["tema"]},
            {"key": "celi", "fields": _OFFICIAL_BLOCK_DEFAULTS["celi"]},
            {"key": "hod_uroka", "columns": hod_uroka_columns},
            {"key": "primechanie", "fields": _OFFICIAL_BLOCK_DEFAULTS["primechanie"]},
        ]
    }


def save_user_template(
    teacher_id: int,
    docx_path: Path | str,
    name: str | None = None,
    db_path=None,
) -> dict:
    """Разбирает загруженный .docx через core.ksp_parser.parse_ksp и
    сохраняет как шаблон учителя: is_builtin=0, is_official=0,
    source="загружен пользователем", uploaded_by=teacher_id — виден
    только этому учителю (list_templates фильтрует по uploaded_by)."""
    parsed = parse_ksp(docx_path)
    structure = _structure_from_parsed_ksp(parsed)
    template_name = name or f"Свой шаблон ({Path(docx_path).stem})"

    new_id = execute(
        "INSERT INTO templates "
        "(name, description, source, is_official, is_builtin, uploaded_by, structure_json) "
        "VALUES (?, ?, ?, 0, 0, ?, ?)",
        (
            template_name,
            f"Загружен учителем из файла {Path(docx_path).name}",
            "загружен пользователем",
            teacher_id,
            json.dumps(structure, ensure_ascii=False),
        ),
        db_path=db_path,
    )
    return get_template(new_id, db_path=db_path)
