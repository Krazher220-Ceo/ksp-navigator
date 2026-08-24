"""
tests/test_ktp_builder.py — тесты core/ktp_builder.py (блок Р4.1).
"""

from datetime import date

from docx import Document

from core.ktp_builder import (
    QUARTER_LABELS,
    TITLE_TEXT,
    build_ktp_docx,
    build_ktp_filename,
)

SAMPLE_CONTENT = {
    "predmet": "физика",
    "klass": "10",
    "chasov_v_nedelu": 2,
    "chasov_v_god": 68,
    "chetverti": [
        {
            "label": QUARTER_LABELS[0],
            "uroki": [
                {
                    "razdel": "Механика",
                    "tema_uroka": "Кинематика точки",
                    "celi_obucheniya": "10.1.1.5",
                    "chasov": 1,
                    "sroki": "02-06.09",
                    "primechanie": "",
                },
            ],
        },
        {"label": QUARTER_LABELS[1], "uroki": []},
        {"label": QUARTER_LABELS[2], "uroki": []},
        {"label": QUARTER_LABELS[3], "uroki": []},
    ],
}


def test_quarter_labels_use_cyrillic_i_not_latin(tmp_path):
    """Р4.1, ловушка: римские цифры в приказе — кириллическая 'І'
    (U+0406), не латинская 'I'. Проверяем кодпоинты явно, а не полагаемся
    на визуальное сходство символов."""
    for label in QUARTER_LABELS:
        assert "І" in label  # кириллическая заглавная І
        assert "I" not in label  # латинская заглавная I отсутствует


def test_build_ktp_docx_produces_reopenable_file(tmp_path):
    out_path = tmp_path / "ktp.docx"
    result = build_ktp_docx(SAMPLE_CONTENT, out_path)

    assert result.exists()
    document = Document(str(result))
    full_text = "\n".join(p.text for p in document.paragraphs)
    assert TITLE_TEXT in full_text
    assert "физика" in full_text
    assert "10" in full_text
    assert "68" in full_text


def test_draft_notice_present(tmp_path):
    out_path = tmp_path / "ktp.docx"
    result = build_ktp_docx(SAMPLE_CONTENT, out_path)
    document = Document(str(result))
    assert document.paragraphs[0].text.startswith("ЧЕРНОВИК")


def test_table_has_seven_official_columns(tmp_path):
    out_path = tmp_path / "ktp.docx"
    result = build_ktp_docx(SAMPLE_CONTENT, out_path)
    document = Document(str(result))
    header_row = document.tables[0].rows[0]
    header_texts = [c.text for c in header_row.cells]
    assert header_texts == [
        "№ п/п",
        "Раздел/ Сквозные темы",
        "Тема урока",
        "Цели обучения",
        "Количество часов",
        "Сроки",
        "Примечание",
    ]


def test_all_four_quarter_labels_present(tmp_path):
    out_path = tmp_path / "ktp.docx"
    result = build_ktp_docx(SAMPLE_CONTENT, out_path)
    document = Document(str(result))
    full_text = "\n".join(c.text for row in document.tables[0].rows for c in row.cells)
    for label in QUARTER_LABELS:
        assert label in full_text


def test_lesson_numbering_is_continuous_across_quarters(tmp_path):
    content = {
        **SAMPLE_CONTENT,
        "chetverti": [
            {"label": QUARTER_LABELS[0], "uroki": [{"tema_uroka": "Тема 1"}, {"tema_uroka": "Тема 2"}]},
            {"label": QUARTER_LABELS[1], "uroki": [{"tema_uroka": "Тема 3"}]},
            {"label": QUARTER_LABELS[2], "uroki": []},
            {"label": QUARTER_LABELS[3], "uroki": []},
        ],
    }
    out_path = tmp_path / "ktp.docx"
    result = build_ktp_docx(content, out_path)
    document = Document(str(result))

    numbers = []
    for row in document.tables[0].rows[1:]:  # пропускаем строку заголовков
        first_cell = row.cells[0].text.strip()
        if first_cell.isdigit():
            numbers.append(int(first_cell))
    assert numbers == [1, 2, 3]  # сквозная нумерация, не с 1 в каждой четверти


def test_build_ktp_filename_uses_expected_pattern():
    # subject/klass не транслитерируются — тот же принцип, что у
    # build_filename в core/docx_builder.py (там тоже транслитерируется
    # только тема урока, а не предмет/класс).
    filename = build_ktp_filename("физика", "10А", date(2026, 9, 1))
    assert filename == "КТП_физика_10А_2026-09-01.docx"
