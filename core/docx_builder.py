"""
core/docx_builder.py — сборка .docx по форме приказа №130 из JSON.

Зачем модуль: единственное место, где content (заполненные данные —
из core/ksp_generator.py, блок Б6) и template (structure_json из
core/templates.py, блок Б4) превращаются в файл, который учитель может
открыть и сдать. Структура таблицы — точная копия официальной формы из
MASTER.md, раздел 4 (приказ МОН РК №130): её нельзя менять "как
красивее", это утверждённый приказом документ, а не дизайн на вкус.

Что осознанно не делает: не проверяет содержательную корректность
content (это задача core/ksp_generator.py при валидации ответа LLM,
блок Б6) — если поле пустое, в документе будет пустая ячейка, а не
выдуманное значение. Не поддерживает шаблоны с блоками, которых нет в
формате Б4.1 (shapka/tema/celi/hod_uroka/primechanie) — неизвестный
блок просто игнорируется, а не роняет сборку.

На что опирается: python-docx для генерации .docx. Ничего из core/templates.py
или core/ksp_generator.py не импортирует — обе стороны (шаблон и
контент) приходят уже готовыми словарями, модуль их не добывает сам.
"""

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table, _Cell

# --- F7: обязательная пометка "черновик". Не выносится в параметр —
# у build_docx нет способа её отключить, это осознанное ограничение. ---
DRAFT_NOTICE_TEXT = "ЧЕРНОВИК. Требует проверки и утверждения педагогом. Сформировано автоматически."

TITLE_TEXT = "Краткосрочный (поурочный) план"

ADAPTACIYA_OOP_TEXT = (
    "Примечание: при наличии обучающихся с особыми образовательными потребностями "
    "предусматриваются действия по адаптации и реализации индивидуальных программ, "
    "одобренных методическими объединениями."
)

PAGE_WIDTH_CM = 21.0
PAGE_HEIGHT_CM = 29.7
MARGIN_CM = 2.0
TABLE_WIDTH_CM = PAGE_WIDTH_CM - 2 * MARGIN_CM  # 17 см содержательной ширины

_FIELD_LABELS = {
    "razdel": "Раздел:",
    "fio_pedagoga": "ФИО педагога:",
    "data": "Дата:",
    "klass": "Класс:",
    "prisutstvuet": "Кол-во присутствующих:",
    "otsutstvuet": "Кол-во отсутствующих:",
    "tema_uroka": "Тема урока:",
    "celi_obucheniya": "Цели обучения в соответствии с учебной программой:",
    "celi_uroka": "Цели урока:",
    "chasy": "Часы:",
}

_COLUMN_LABELS = {
    "etap_vremya": "Этап урока / время",
    "deystviya_pedagoga": "Действия педагога",
    "deystviya_uchenika": "Действия ученика",
    "resursy": "Ресурсы",
    "ocenivanie": "Оценивание",
    "domashnee_zadanie": "Домашнее задание",
    "dop_literatura": "Доп. литература",
}

# Относительный вес ширины колонки таблицы "Ход урока" — "действия" вдвое
# многословнее "этапа"/"ресурсов", поэтому им и колонка шире. Незнакомая
# колонка (новый шаблон, ещё не описанный здесь) получает вес по умолчанию.
_COLUMN_WEIGHTS = {
    "etap_vremya": 1.0,
    "deystviya_pedagoga": 1.6,
    "deystviya_uchenika": 1.6,
    "resursy": 1.0,
    "ocenivanie": 1.0,
    "domashnee_zadanie": 1.2,
    "dop_literatura": 1.0,
}
_DEFAULT_COLUMN_WEIGHT = 1.0

_CANONICAL_HOD_UROKA_COLUMNS = [
    "etap_vremya",
    "deystviya_pedagoga",
    "deystviya_uchenika",
    "resursy",
    "ocenivanie",
]

# Порядок полей шапки, который задаёт официальная форма (MASTER.md, п.4).
# klass/prisutstvuet/otsutstvuet — не отдельные строки, а один общий
# трёхколоночный ряд ("Класс: | Кол-во присутств. | Кол-во отсутств.").
_SHAPKA_STANDALONE_ORDER = ["razdel", "fio_pedagoga", "data"]
_SHAPKA_KLASS_GROUP = ["klass", "prisutstvuet", "otsutstvuet"]


# --- оформление страницы (Б5.1) ---


def _apply_page_setup(document: Document) -> None:
    """A4, поля 2 см, Times New Roman 12 — требование Б5.1."""
    section = document.sections[0]
    section.page_width = Cm(PAGE_WIDTH_CM)
    section.page_height = Cm(PAGE_HEIGHT_CM)
    section.left_margin = Cm(MARGIN_CM)
    section.right_margin = Cm(MARGIN_CM)
    section.top_margin = Cm(MARGIN_CM)
    section.bottom_margin = Cm(MARGIN_CM)

    normal_style = document.styles["Normal"]
    normal_style.font.name = "Times New Roman"
    normal_style.font.size = Pt(12)


def _add_draft_notice(document: Document) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(DRAFT_NOTICE_TEXT)
    run.bold = True
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)


def _add_title(document: Document) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(TITLE_TEXT)
    run.bold = True
    run.font.size = Pt(14)


def _add_underline_field(document: Document, value: str, caption: str) -> None:
    """Строка-заполнение над таблицей (наименование организации, тема
    урока) — в официальной форме это подчёркнутая линия с подписью
    под ней. При наличии значения печатаем его подчёркнутым; если
    значения нет — просто линия-заглушка, как в бланке."""
    line = document.add_paragraph()
    line.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = line.add_run(value if value else "_" * 45)
    run.underline = True

    caption_p = document.add_paragraph()
    caption_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption_run = caption_p.add_run(caption)
    caption_run.italic = True
    caption_run.font.size = Pt(10)


# --- таблица ---


def _column_widths(columns: list[str], total_width_cm: float) -> list[Cm]:
    weights = [_COLUMN_WEIGHTS.get(c, _DEFAULT_COLUMN_WEIGHT) for c in columns]
    weight_sum = sum(weights) or 1.0
    return [Cm(total_width_cm * w / weight_sum) for w in weights]


def _build_table(document: Document, columns: list[str]) -> tuple[Table, list[Cm]]:
    table = document.add_table(rows=0, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = False
    widths = _column_widths(columns, TABLE_WIDTH_CM)
    for i, width in enumerate(widths):
        table.columns[i].width = width
    return table, widths


def _merge_row(row) -> _Cell:
    merged = row.cells[0]
    for i in range(1, len(row.cells)):
        merged = merged.merge(row.cells[i])
    return merged


def _set_cell_label_value(cell: _Cell, label: str, value) -> None:
    # Ячейка только что создана (add_row()/merge()) — уже содержит один
    # пустой абзац без runs. cell.text = "" здесь не нужен и вреден: он
    # сам добавляет пустой run перед тем, что мы допишем через add_run(),
    # и тогда runs[0] оказывается пустым, а не жирным лейблом.
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(f"{label} ")
    run.bold = True

    if isinstance(value, list):
        items = [str(v) for v in value if v]
        if items:
            paragraph.add_run(items[0])
        for extra in items[1:]:
            extra_paragraph = cell.add_paragraph()
            extra_paragraph.add_run(f"— {extra}")
    else:
        paragraph.add_run(str(value) if value else "")


def _add_label_value_row(table: Table, label: str, value) -> None:
    row = table.add_row()
    merged = _merge_row(row)
    _set_cell_label_value(merged, label, value)


def _add_klass_row(table: Table, content: dict, fields_present: list[str]) -> None:
    """Класс / Кол-во присутствующих / Кол-во отсутствующих — один ряд,
    ровно три видимые колонки (MASTER.md, п.4), сколько бы колонок ни
    было у таблицы "Ход урока" дальше."""
    row = table.add_row()
    ncols = len(row.cells)

    if "klass" in fields_present:
        _set_cell_label_value(row.cells[0], _FIELD_LABELS["klass"], content.get("klass", ""))

    if "prisutstvuet" in fields_present and ncols > 1:
        _set_cell_label_value(
            row.cells[1], _FIELD_LABELS["prisutstvuet"], content.get("prisutstvuet", "")
        )

    if "otsutstvuet" in fields_present:
        start = min(2, ncols - 1)
        merged = row.cells[start]
        for i in range(start + 1, ncols):
            merged = merged.merge(row.cells[i])
        _set_cell_label_value(merged, _FIELD_LABELS["otsutstvuet"], content.get("otsutstvuet", ""))


def _add_hod_uroka_section(
    table: Table, columns: list[str], widths: list[Cm], rows_data: list[dict]
) -> None:
    title_row = table.add_row()
    merged = _merge_row(title_row)
    run = merged.paragraphs[0].add_run("Ход урока")
    run.bold = True

    header_row = table.add_row()
    for i, col in enumerate(columns):
        cell = header_row.cells[i]
        cell.width = widths[i]
        run = cell.paragraphs[0].add_run(_COLUMN_LABELS.get(col, col))
        run.bold = True

    data_rows = rows_data or [{}]  # пустая таблица выглядит как заготовка, а не как баг
    for entry in data_rows:
        row = table.add_row()
        for i, col in enumerate(columns):
            row.cells[i].width = widths[i]
            value = entry.get(col, "")
            if col == "etap_vremya" and not value:
                # запасной вариант на случай, если Б6 когда-нибудь пришлёт
                # раздельные etap/vremya вместо объединённого etap_vremya
                value = f"{entry.get('etap', '')} / {entry.get('vremya', '')}".strip(" /")
            row.cells[i].text = str(value) if value else ""


# --- Б5.1: главная функция сборки ---


def build_docx(content: dict, template: dict, out_path: Path | str) -> Path:
    """Строит .docx по structure_json шаблона и заполненным данным content.

    template — словарь с ключом "structure_json" (как из core.templates:
    get_template()/list_templates(), уже распарсенный, или сырая JSON-строка
    из БД — оба варианта поддерживаются).
    content — заполненные значения полей, ключи те же, что в fields/columns
    шаблона (например "tema_uroka", "razdel", "hod_uroka": [...]).
    """
    structure = template.get("structure_json")
    if isinstance(structure, str):
        structure = json.loads(structure)
    blocks = {block["key"]: block for block in structure.get("blocks", [])}

    document = Document()
    _apply_page_setup(document)
    _add_draft_notice(document)  # F7 — первая строка документа, всегда

    shapka_fields = blocks.get("shapka", {}).get("fields", [])
    tema_fields = blocks.get("tema", {}).get("fields", [])
    celi_fields = blocks.get("celi", {}).get("fields", [])
    hod_uroka_columns = blocks.get("hod_uroka", {}).get("columns") or list(_CANONICAL_HOD_UROKA_COLUMNS)
    primechanie_fields = blocks.get("primechanie", {}).get("fields", [])

    if "organizaciya" in shapka_fields:
        _add_underline_field(
            document, content.get("organizaciya", ""), "(наименование организации образования)"
        )

    _add_title(document)

    if "tema_uroka" in tema_fields:
        _add_underline_field(document, content.get("tema_uroka", ""), "(тема урока)")

    table, widths = _build_table(document, hod_uroka_columns)

    for field in _SHAPKA_STANDALONE_ORDER:
        if field in shapka_fields:
            _add_label_value_row(table, _FIELD_LABELS[field], content.get(field, ""))

    klass_group_present = [f for f in _SHAPKA_KLASS_GROUP if f in shapka_fields]
    if klass_group_present:
        _add_klass_row(table, content, klass_group_present)

    known_shapka_fields = {"organizaciya", *_SHAPKA_STANDALONE_ORDER, *_SHAPKA_KLASS_GROUP}
    for field in shapka_fields:
        if field not in known_shapka_fields:
            label = _FIELD_LABELS.get(field, f"{field.capitalize()}:")
            _add_label_value_row(table, label, content.get(field, ""))

    if "tema_uroka" in tema_fields:
        _add_label_value_row(table, _FIELD_LABELS["tema_uroka"], content.get("tema_uroka", ""))

    for field in ("celi_obucheniya", "celi_uroka"):
        if field in celi_fields:
            _add_label_value_row(table, _FIELD_LABELS[field], content.get(field, ""))

    _add_hod_uroka_section(table, hod_uroka_columns, widths, content.get("hod_uroka", []))

    if "adaptaciya_oop" in primechanie_fields:
        document.add_paragraph(ADAPTACIYA_OOP_TEXT)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(out_path)
    return out_path


# --- Б5.3: имя файла ---

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ў": "u", "қ": "q", "ғ": "g", "ң": "n", "ә": "a",
    "ө": "o", "ұ": "u", "һ": "h",
}

_UNSAFE_FILENAME_CHARS = set('<>:"/\\|?*')


def _transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in text.lower())


def _strip_unsafe_filename_chars(text: str) -> str:
    """Заменяет пробелы и символы, недопустимые в имени файла на любой
    ОС (кавычки, слэши, двоеточия, звёздочки и т.п.), на '_'. Кириллицу
    не трогает — транслитерация нужна только для темы, см. build_filename."""
    chars = []
    for ch in text:
        if ch.isspace() or ch in _UNSAFE_FILENAME_CHARS:
            chars.append("_")
        elif ord(ch) < 0x20:
            continue
        else:
            chars.append(ch)
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _slugify_topic(topic: str) -> str:
    """Тема идёт в имя файла транслитерированной и очищенной от
    небезопасных символов (Б5.3: кавычки/слэши/двоеточия не должны
    ломать сохранение)."""
    return _strip_unsafe_filename_chars(_transliterate(topic))


def build_filename(subject: str, klass: str, topic: str, generated_at) -> str:
    """КСП_{предмет}_{класс}_{тема_транслитом}_{ГГГГ-ММ-ДД}.docx,
    длина ≤ 100 символов. generated_at — date/datetime или строка вида
    "2025-11-12" (для тестов с фиксированной датой)."""
    if hasattr(generated_at, "strftime"):
        date_str = generated_at.strftime("%Y-%m-%d")
    else:
        date_str = str(generated_at)

    subject_slug = _strip_unsafe_filename_chars(subject or "predmet")
    klass_slug = _strip_unsafe_filename_chars(klass or "klass")
    topic_slug = _slugify_topic(topic or "tema")

    prefix = f"КСП_{subject_slug}_{klass_slug}_"
    suffix = f"_{date_str}.docx"

    max_topic_len = 100 - len(prefix) - len(suffix)
    if max_topic_len < 1:
        # предмет/класс сами по себе огромные — обрежем и их, честно
        # деградируем, а не роняем сборку из-за длины имени файла
        overflow = 1 - max_topic_len
        subject_slug = subject_slug[: max(1, len(subject_slug) - overflow)]
        prefix = f"КСП_{subject_slug}_{klass_slug}_"
        max_topic_len = max(1, 100 - len(prefix) - len(suffix))

    topic_slug = topic_slug[:max_topic_len]
    filename = f"{prefix}{topic_slug}{suffix}"
    return filename
