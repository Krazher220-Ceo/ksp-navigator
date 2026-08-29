"""
tests/test_templates.py — тесты core/templates.py.

Использует ту же реальную storage/schema.sql, что и tests/test_db.py, и
одну из фикстур .docx из tests/test_ksp_parser.py (блок Б3) — для
проверки save_user_template на настоящем разборе файла, а не на
придуманных данных.
"""

import json
from pathlib import Path

import pytest

from core.db import execute, init_db, query
from core.templates import (
    BUILTIN_TEMPLATES_DIR,
    get_template,
    list_templates,
    load_builtin_templates,
    save_user_template,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
UPLOAD_FIXTURE = FIXTURES_DIR / "ksp_sample_3_typos_case.docx"


@pytest.fixture
def db_with_two_teachers(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Учитель Один', 'физика')", db_path=db_path)
    execute("INSERT INTO teachers (id, name, subject) VALUES (2, 'Учитель Два', 'физика')", db_path=db_path)
    return db_path


# --- Б4.1: формат structure_json одинаков во всех трёх файлах ---


def test_builtin_template_files_share_the_same_block_format():
    files = sorted(BUILTIN_TEMPLATES_DIR.glob("*.json"))
    assert len(files) == 3

    expected_block_keys = {"shapka", "tema", "celi", "hod_uroka", "primechanie"}

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "name" in data
        assert "source" in data, f"{path.name}: нет обязательного поля source"
        assert "is_official" in data, f"{path.name}: нет обязательного поля is_official"
        assert "blocks" in data

        block_keys = {block["key"] for block in data["blocks"]}
        assert block_keys == expected_block_keys, f"{path.name}: {block_keys}"

        hod_uroka = next(b for b in data["blocks"] if b["key"] == "hod_uroka")
        # у всех троих должен быть хотя бы базовый набор из 5 ролей
        assert {"etap_vremya", "deystviya_pedagoga", "deystviya_uchenika", "resursy", "ocenivanie"} <= set(
            hod_uroka["columns"]
        )


# --- Б4.2: ровно 3 встроенных шаблона, идемпотентность ---


def test_load_builtin_templates_inserts_exactly_three(db_with_two_teachers):
    inserted = load_builtin_templates(db_path=db_with_two_teachers)

    assert inserted == 3
    rows = query("SELECT * FROM templates WHERE is_builtin = 1", db_path=db_with_two_teachers)
    assert len(rows) == 3


def test_load_builtin_templates_is_idempotent(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    second_call_inserted = load_builtin_templates(db_path=db_with_two_teachers)

    assert second_call_inserted == 0
    rows = query("SELECT * FROM templates WHERE is_builtin = 1", db_path=db_with_two_teachers)
    assert len(rows) == 3, "повторный вызов load_builtin_templates не должен плодить дубли"


def test_load_builtin_templates_survives_three_calls(db_with_two_teachers):
    for _ in range(3):
        load_builtin_templates(db_path=db_with_two_teachers)
    rows = query("SELECT * FROM templates WHERE is_builtin = 1", db_path=db_with_two_teachers)
    assert len(rows) == 3


def test_every_builtin_template_has_source_and_is_official(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    rows = query("SELECT * FROM templates WHERE is_builtin = 1", db_path=db_with_two_teachers)

    for row in rows:
        assert row["source"], f"у шаблона {row['name']!r} пустой source"
        assert row["is_official"] in (0, 1)


def test_exactly_one_builtin_template_is_official(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    rows = query(
        "SELECT * FROM templates WHERE is_builtin = 1 AND is_official = 1", db_path=db_with_two_teachers
    )
    assert len(rows) == 1
    assert "130" in rows[0]["source"]


def test_builtin_templates_receive_categories(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    rows = query("SELECT category FROM templates WHERE is_builtin = 1", db_path=db_with_two_teachers)
    assert {row["category"] for row in rows} == {"official", "sample"}


# --- Б4.3: list_templates / get_template ---


def test_list_templates_shows_builtins_to_any_teacher(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)

    templates = list_templates(teacher_id=1, db_path=db_with_two_teachers)
    assert len(templates) == 3
    assert all(t["structure_json"]["blocks"] for t in templates)  # уже распарсенный JSON, не строка


def test_get_template_returns_none_for_missing_id(db_with_two_teachers):
    assert get_template(999999, db_path=db_with_two_teachers) is None


def test_get_template_returns_parsed_structure(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    official = next(
        t for t in list_templates(teacher_id=1, db_path=db_with_two_teachers) if t["is_official"] == 1
    )

    fetched = get_template(official["id"], db_path=db_with_two_teachers)
    assert fetched["id"] == official["id"]
    assert isinstance(fetched["structure_json"], dict)


# --- Б4.3: save_user_template — виден только своему учителю ---


def test_save_user_template_uses_real_uploaded_docx(db_with_two_teachers):
    result = save_user_template(teacher_id=1, docx_path=UPLOAD_FIXTURE, db_path=db_with_two_teachers)

    assert result["is_builtin"] == 0
    assert result["is_official"] == 0
    assert result["source"] == "загружен пользователем"
    assert result["uploaded_by"] == 1

    # колонки "Ход урока" реально определены по загруженному файлу
    hod_uroka = next(b for b in result["structure_json"]["blocks"] if b["key"] == "hod_uroka")
    assert set(hod_uroka["columns"]) == {
        "etap_vremya",
        "deystviya_pedagoga",
        "deystviya_uchenika",
        "resursy",
        "ocenivanie",
    }


def test_save_user_template_visible_only_to_own_teacher(db_with_two_teachers):
    load_builtin_templates(db_path=db_with_two_teachers)
    save_user_template(teacher_id=1, docx_path=UPLOAD_FIXTURE, db_path=db_with_two_teachers)

    owner_templates = list_templates(teacher_id=1, db_path=db_with_two_teachers)
    other_teacher_templates = list_templates(teacher_id=2, db_path=db_with_two_teachers)

    assert len(owner_templates) == 4  # 3 встроенных + свой
    assert len(other_teacher_templates) == 3  # только встроенные, чужой не виден

    other_names = {t["name"] for t in other_teacher_templates}
    owner_only_names = {t["name"] for t in owner_templates} - other_names
    assert len(owner_only_names) == 1


def test_save_user_template_default_name_uses_filename(db_with_two_teachers):
    result = save_user_template(teacher_id=1, docx_path=UPLOAD_FIXTURE, db_path=db_with_two_teachers)
    assert UPLOAD_FIXTURE.stem in result["name"]


def test_save_user_template_custom_name(db_with_two_teachers):
    result = save_user_template(
        teacher_id=1, docx_path=UPLOAD_FIXTURE, name="Мой любимый шаблон", db_path=db_with_two_teachers
    )
    assert result["name"] == "Мой любимый шаблон"
