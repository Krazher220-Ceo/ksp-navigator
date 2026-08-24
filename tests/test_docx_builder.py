"""
tests/test_docx_builder.py — тесты core/docx_builder.py.

Использует реальные встроенные шаблоны из core/templates.py (блок Б4) —
не придуманные структуры, а те самые три, что попадут в бота.
"""

import re
from datetime import date
from pathlib import Path

import pytest
from docx import Document

from core.db import execute, init_db
from core.docx_builder import (
    DRAFT_NOTICE_TEXT,
    build_docx,
    build_filename,
)
from core.templates import get_template, load_builtin_templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


SAMPLE_CONTENT = {
    "organizaciya": "КГУ «Гимназия №27», Костанай",
    "razdel": "Механика",
    "fio_pedagoga": "Нургалиев М.К.",
    "data": "12.11.2025",
    "chasy": "1",
    "klass": "10А",
    "prisutstvuet": "24",
    "otsutstvuet": "2",
    "tema_uroka": 'Закон сохранения импульса: теория и практика "на пальцах"',
    "celi_obucheniya": "10.1.4.1 применять законы сохранения при решении задач",
    "celi_uroka": [
        "Все учащиеся смогут сформулировать закон сохранения импульса",
        "Большинство учащихся смогут решить задачу на неупругий удар",
    ],
    "hod_uroka": [
        {
            "etap_vremya": "Начало урока, 0-5 мин",
            "deystviya_pedagoga": "Приветствует, проверяет присутствующих",
            "deystviya_uchenika": "Готовятся к уроку",
            "resursy": "Классный журнал",
            "ocenivanie": "Устная похвала",
            "domashnee_zadanie": "§24, вопросы 1-3",
            "dop_literatura": "Перышкин, §18",
        },
        {
            "etap_vremya": "Середина урока, 5-30 мин",
            "deystviya_pedagoga": "Объясняет закон сохранения импульса",
            "deystviya_uchenika": "Слушают, решают задачи",
            "resursy": "Учебник, доска",
            "ocenivanie": "Взаимооценивание",
            "domashnee_zadanie": "",
            "dop_literatura": "",
        },
    ],
}


@pytest.fixture
def db_with_builtins(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=db_path)
    load_builtin_templates(db_path=db_path)
    return db_path


def _template_ids(db_path) -> list[int]:
    from core.templates import list_templates

    return [t["id"] for t in list_templates(teacher_id=1, db_path=db_path)]


# --- Б5.1: build_docx на всех трёх встроенных шаблонах ---


@pytest.mark.parametrize("template_index", [0, 1, 2])
def test_build_docx_produces_reopenable_file_for_every_builtin_template(
    db_with_builtins, tmp_path, template_index
):
    template_id = _template_ids(db_with_builtins)[template_index]
    template = get_template(template_id, db_path=db_with_builtins)

    out_path = tmp_path / f"output_{template_index}.docx"
    result = build_docx(SAMPLE_CONTENT, template, out_path)

    assert result == out_path
    assert out_path.exists()

    # файл действительно валидный .docx, открывается обратно без исключений
    reopened = Document(str(out_path))
    assert len(reopened.tables) == 1
    assert len(reopened.paragraphs) > 0


def test_build_docx_accepts_structure_json_as_raw_string(db_with_builtins, tmp_path):
    """template["structure_json"] может прийти уже распарсенным (из
    core.templates) или сырой JSON-строкой (прямо из БД) — оба случая
    должны работать одинаково."""
    template_id = _template_ids(db_with_builtins)[0]
    template = get_template(template_id, db_path=db_with_builtins)

    import json

    raw_string_template = dict(template)
    raw_string_template["structure_json"] = json.dumps(template["structure_json"])

    out_path = tmp_path / "raw_string.docx"
    build_docx(SAMPLE_CONTENT, raw_string_template, out_path)
    assert out_path.exists()


# --- Б5.2: пометка "черновик" — F7, обязательна в каждом файле ---


@pytest.mark.parametrize("template_index", [0, 1, 2])
def test_draft_notice_present_as_first_paragraph(db_with_builtins, tmp_path, template_index):
    template_id = _template_ids(db_with_builtins)[template_index]
    template = get_template(template_id, db_path=db_with_builtins)

    out_path = tmp_path / f"draft_check_{template_index}.docx"
    build_docx(SAMPLE_CONTENT, template, out_path)

    document = Document(str(out_path))
    assert document.paragraphs[0].text == DRAFT_NOTICE_TEXT


def test_draft_notice_is_bold_and_red(db_with_builtins, tmp_path):
    template = get_template(_template_ids(db_with_builtins)[0], db_path=db_with_builtins)
    out_path = tmp_path / "draft_style.docx"
    build_docx(SAMPLE_CONTENT, template, out_path)

    document = Document(str(out_path))
    run = document.paragraphs[0].runs[0]
    assert run.bold is True
    assert run.font.color.rgb is not None


# --- Б5.1: оформление страницы ---


def test_page_setup_a4_margins_and_font(db_with_builtins, tmp_path):
    template = get_template(_template_ids(db_with_builtins)[0], db_path=db_with_builtins)
    out_path = tmp_path / "page_setup.docx"
    build_docx(SAMPLE_CONTENT, template, out_path)

    document = Document(str(out_path))
    section = document.sections[0]

    # A4 (21.0 x 29.7 см), допуск на округление EMU
    assert abs(section.page_width.cm - 21.0) < 0.05
    assert abs(section.page_height.cm - 29.7) < 0.05
    for margin in (section.left_margin, section.right_margin, section.top_margin, section.bottom_margin):
        assert abs(margin.cm - 2.0) < 0.05

    normal_font = document.styles["Normal"].font
    assert normal_font.name == "Times New Roman"
    assert normal_font.size.pt == 12


def test_table_has_visible_borders_style(db_with_builtins, tmp_path):
    template = get_template(_template_ids(db_with_builtins)[0], db_path=db_with_builtins)
    out_path = tmp_path / "borders.docx"
    build_docx(SAMPLE_CONTENT, template, out_path)

    document = Document(str(out_path))
    assert document.tables[0].style.name == "Table Grid"


def test_column_headers_are_bold(db_with_builtins, tmp_path):
    template = get_template(_template_ids(db_with_builtins)[0], db_path=db_with_builtins)
    out_path = tmp_path / "headers_bold.docx"
    build_docx(SAMPLE_CONTENT, template, out_path)

    document = Document(str(out_path))
    table = document.tables[0]
    # официальный шаблон: строка 8 - заголовки колонок (после 7 строк шапки+целей + "Ход урока")
    header_row = next(r for r in table.rows if r.cells[0].text.strip() == "Этап урока / время")
    for cell in header_row.cells:
        assert cell.paragraphs[0].runs[0].bold is True


# --- официальная форма: все обязательные поля приказа №130 присутствуют ---


def test_official_form_contains_all_required_fields(db_with_builtins, tmp_path):
    official = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if t["is_official"] == 1
    )
    out_path = tmp_path / "official_full.docx"
    build_docx(SAMPLE_CONTENT, official, out_path)

    document = Document(str(out_path))
    full_text = "\n".join(p.text for p in document.paragraphs)
    full_text += "\n" + "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)

    required_labels = [
        "Раздел:",
        "ФИО педагога:",
        "Дата:",
        "Класс:",
        "Кол-во присутствующих:",
        "Кол-во отсутствующих:",
        "Тема урока:",
        "Цели обучения в соответствии с учебной программой:",
        "Цели урока:",
        "Ход урока",
        "Этап урока / время",
        "Действия педагога",
        "Действия ученика",
        "Ресурсы",
        "Оценивание",
    ]
    for label in required_labels:
        assert label in full_text, f"в официальной форме отсутствует обязательное поле: {label!r}"


def test_merged_rows_use_real_gridspan_not_duplicated_cells(db_with_builtins, tmp_path):
    """Полноширинные строки (Раздел, ФИО и т.п.) должны быть настоящим
    слиянием ячеек (w:gridSpan), а не пятью отдельными ячейками с
    одинаковым текстом — иначе в Word будут лишние границы посреди строки."""
    official = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if t["is_official"] == 1
    )
    out_path = tmp_path / "gridspan_check.docx"
    build_docx(SAMPLE_CONTENT, official, out_path)

    document = Document(str(out_path))
    table = document.tables[0]

    razdel_row = next(r for r in table.rows if r.cells[0].text.startswith("Раздел:"))
    tcs = razdel_row._tr.findall(f".//{W_NS}tc")
    assert len(tcs) == 1, "строка 'Раздел:' должна быть одной физически слитой ячейкой"

    klass_row = next(r for r in table.rows if r.cells[0].text.startswith("Класс:"))
    tcs = klass_row._tr.findall(f".//{W_NS}tc")
    assert len(tcs) == 3, "строка Класс/Присутств./Отсутств. должна давать ровно 3 физические ячейки"


def test_celi_uroka_list_renders_as_multiple_lines(db_with_builtins, tmp_path):
    official = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if t["is_official"] == 1
    )
    out_path = tmp_path / "celi_list.docx"
    build_docx(SAMPLE_CONTENT, official, out_path)

    document = Document(str(out_path))
    table = document.tables[0]
    celi_uroka_row = next(r for r in table.rows if r.cells[0].text.startswith("Цели урока:"))
    cell_text = celi_uroka_row.cells[0].text
    for item in SAMPLE_CONTENT["celi_uroka"]:
        assert item in cell_text


def test_empty_hod_uroka_produces_one_placeholder_row_not_crash(db_with_builtins, tmp_path):
    content = dict(SAMPLE_CONTENT)
    content["hod_uroka"] = []
    official = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if t["is_official"] == 1
    )
    out_path = tmp_path / "empty_hod_uroka.docx"
    build_docx(content, official, out_path)  # не должно упасть

    document = Document(str(out_path))
    header_row_idx = next(
        i for i, r in enumerate(document.tables[0].rows) if r.cells[0].text.strip() == "Этап урока / время"
    )
    assert len(document.tables[0].rows) == header_row_idx + 2  # заголовок + 1 пустая строка-заглушка


def test_extended_template_has_seven_columns_in_hod_uroka(db_with_builtins, tmp_path):
    extended = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if "Развёрнутый" in t["name"]
    )
    out_path = tmp_path / "extended.docx"
    build_docx(SAMPLE_CONTENT, extended, out_path)

    document = Document(str(out_path))
    assert len(document.tables[0].columns) == 7


def test_gymnasium_template_omits_fields_it_does_not_declare(db_with_builtins, tmp_path):
    gymnasium = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if "Гимназия" in t["name"]
    )
    out_path = tmp_path / "gymnasium.docx"
    build_docx(SAMPLE_CONTENT, gymnasium, out_path)

    document = Document(str(out_path))
    full_text = "\n".join(cell.text for row in document.tables[0].rows for cell in row.cells)
    assert "ФИО педагога:" not in full_text
    assert "Часы:" in full_text  # известная метка, не "Chasy:"


# --- Б5.3: имя файла ---


def test_build_filename_transliterates_and_replaces_spaces():
    filename = build_filename("физика", "10А", "Закон Ньютона", date(2025, 11, 12))
    assert filename == "КСП_физика_10А_zakon_nyutona_2025-11-12.docx"


def test_build_filename_strips_unsafe_characters_from_topic():
    filename = build_filename("физика", "10А", 'Тема: "закон" / часть 2', date(2025, 1, 5))
    for bad_char in '"/:*?<>|\\':
        assert bad_char not in filename
    assert filename.endswith("_2025-01-05.docx")


def test_build_filename_length_never_exceeds_100_chars():
    huge_topic = "Очень длинная тема урока " * 10 + 'с кавычками "и слэшами/двоеточиями:тоже"'
    filename = build_filename("физика", "10А", huge_topic, date(2025, 6, 1))
    assert len(filename) <= 100


def test_build_filename_accepts_string_date():
    filename = build_filename("физика", "10А", "Тема", "2025-03-14")
    assert filename == "КСП_физика_10А_tema_2025-03-14.docx"


def test_topic_with_quotes_slashes_colons_does_not_break_actual_save(
    db_with_builtins, tmp_path
):
    """Главная проверка Б5.3: настоящее сохранение на диск с именем,
    построенным из темы с кавычками/слэшами/двоеточиями, не падает."""
    official = next(
        t for t in [get_template(i, db_path=db_with_builtins) for i in _template_ids(db_with_builtins)]
        if t["is_official"] == 1
    )
    tricky_topic = 'Закон "Ома": сила тока / напряжение * сопротивление?'
    content = dict(SAMPLE_CONTENT)
    content["tema_uroka"] = tricky_topic

    filename = build_filename("физика", "10А", tricky_topic, date(2025, 9, 1))
    out_path = tmp_path / filename

    result = build_docx(content, official, out_path)

    assert result.exists()
    # исходная (не транслитерированная) тема сохранилась в тексте документа —
    # ломается только имя файла, не содержимое
    document = Document(str(result))
    full_text = "\n".join(p.text for p in document.paragraphs)
    assert tricky_topic in full_text
