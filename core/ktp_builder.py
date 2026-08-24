"""
core/ktp_builder.py — сборка .docx среднесрочного (календарно-тематического)
плана по форме приложения 4 приказа МОН РК №130 (редакция от 30.04.2025 № 98).

Зачем модуль: КТП — вторая обязательная бумага педагога (наряду с КСП,
core/docx_builder.py), которую приказ требует разработать один раз до
начала учебного года. Это отдельная форма с своей структурой (7 колонок,
разбивка по четвертям), не вариант формы КСП — поэтому отдельный модуль,
а не расширение core/docx_builder.py.

Что осознанно не делает: не проверяет содержательную корректность content
(это задача core/ktp_generator.py при валидации ответа LLM, блок Р4.2) —
если поле пустое, в документе будет пустая ячейка. Не работает с шаблонами
(core/templates.py) — форма КТП в приказе одна, вариативности вроде трёх
шаблонов КСП для неё не предусмотрено.

На что опирается: python-docx. Делит переданный список уроков на четыре
четверти по полю "chetvert" каждого урока (1-4) — само распределение по
четвертям делает core/ktp_generator.py, здесь только отрисовка.
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table

from core.docx_builder import (
    DRAFT_NOTICE_TEXT,
    MARGIN_CM,
    PAGE_HEIGHT_CM,
    PAGE_WIDTH_CM,
    _strip_unsafe_filename_chars,
)

TITLE_TEXT = "Среднесрочный (календарно-тематический) план по предметам"

# Дословно из приложения 4 приказа №130 (редакция от 30.04.2025 № 98) —
# извлечено программно из текста приказа, не перепечатано вручную: во всех
# четырёх строках "І" — кириллическая (U+0406), не латинская "I", а в
# четвёртой она вдобавок соседствует с латинской "V" ("ІV", не "IV" и не
# "ІІІІ") — это реальная особенность документа, легко ошибиться на глаз.
QUARTER_LABELS = ["І четверть", "ІІ четверть", "ІІІ четверть", "ІV четверть"]

TABLE_COLUMNS = [
    "nomer_pp",
    "razdel",
    "tema_uroka",
    "celi_obucheniya",
    "chasov",
    "sroki",
    "primechanie",
]
_COLUMN_LABELS = {
    "nomer_pp": "№ п/п",
    "razdel": "Раздел/ Сквозные темы",
    "tema_uroka": "Тема урока",
    "celi_obucheniya": "Цели обучения",
    "chasov": "Количество часов",
    "sroki": "Сроки",
    "primechanie": "Примечание",
}
_COLUMN_WEIGHTS = {
    "nomer_pp": 0.5,
    "razdel": 1.3,
    "tema_uroka": 1.6,
    "celi_obucheniya": 1.0,
    "chasov": 0.6,
    "sroki": 0.8,
    "primechanie": 1.0,
}

TABLE_WIDTH_CM = PAGE_WIDTH_CM - 2 * MARGIN_CM


def _apply_page_setup(document: Document) -> None:
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
    run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)


def _column_widths() -> list[Cm]:
    weights = [_COLUMN_WEIGHTS[c] for c in TABLE_COLUMNS]
    total = sum(weights)
    return [Cm(TABLE_WIDTH_CM * w / total) for w in weights]


def _build_table(document: Document) -> tuple[Table, list[Cm]]:
    table = document.add_table(rows=0, cols=len(TABLE_COLUMNS))
    table.style = "Table Grid"
    table.autofit = False
    widths = _column_widths()
    for i, width in enumerate(widths):
        table.columns[i].width = width

    header_row = table.add_row()
    for i, col in enumerate(TABLE_COLUMNS):
        cell = header_row.cells[i]
        cell.width = widths[i]
        run = cell.paragraphs[0].add_run(_COLUMN_LABELS[col])
        run.bold = True

    return table, widths


def _add_quarter_section(table: Table, widths: list[Cm], label: str, uroki: list[dict]) -> None:
    title_row = table.add_row()
    merged = title_row.cells[0]
    for i in range(1, len(TABLE_COLUMNS)):
        merged = merged.merge(title_row.cells[i])
    run = merged.paragraphs[0].add_run(label)
    run.bold = True

    for urok in uroki:
        row = table.add_row()
        for i, col in enumerate(TABLE_COLUMNS):
            row.cells[i].width = widths[i]
            value = urok.get(col, "")
            row.cells[i].text = str(value) if value else ""


def build_ktp_docx(content: dict, out_path: Path | str) -> Path:
    """Строит .docx среднесрочного плана.

    content — словарь:
      predmet: str, klass: str,
      chasov_v_nedelu: int, chasov_v_god: int,
      chetverti: [{"label": "I четверть", "uroki": [...]}, ...] — ровно 4
      элемента, "uroki" — список словарей с ключами TABLE_COLUMNS (без
      "nomer_pp" — он проставляется здесь сквозной нумерацией по всему
      документу, а не отдельно для каждой четверти, как в реальных КТП).
    """
    document = Document()
    _apply_page_setup(document)
    _add_draft_notice(document)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(TITLE_TEXT)
    run.bold = True
    run.font.size = Pt(14)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run(
        f"{content.get('predmet', '')} дисциплина, {content.get('klass', '')} класс"
    )

    totals = document.add_paragraph()
    totals.alignment = WD_ALIGN_PARAGRAPH.CENTER
    totals.add_run(
        f"Итого: {content.get('chasov_v_god', '')} часов, "
        f"в неделю: {content.get('chasov_v_nedelu', '')} часов"
    )

    table, widths = _build_table(document)

    lesson_number = 0
    for quarter in content.get("chetverti", []):
        uroki_with_numbers = []
        for urok in quarter.get("uroki", []):
            lesson_number += 1
            uroki_with_numbers.append({**urok, "nomer_pp": lesson_number})
        _add_quarter_section(table, widths, quarter.get("label", ""), uroki_with_numbers)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(out_path)
    return out_path


def build_ktp_filename(predmet: str, klass: str, generated_at) -> str:
    """КТП_{предмет}_{класс}_{ГГГГ-ММ-ДД}.docx — тот же принцип, что у
    build_filename в core/docx_builder.py, но без темы урока (у КТП её нет)."""
    if hasattr(generated_at, "strftime"):
        date_str = generated_at.strftime("%Y-%m-%d")
    else:
        date_str = str(generated_at)

    predmet_slug = _strip_unsafe_filename_chars(predmet or "predmet")
    klass_slug = _strip_unsafe_filename_chars(klass or "klass")
    return f"КТП_{predmet_slug}_{klass_slug}_{date_str}.docx"
