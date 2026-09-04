"""
tests/test_pdf_export.py — тесты core/pdf_export.py (блок Р10).

Реальная конвертация через LibreOffice (soffice уже используется
core.ksp_parser.ensure_docx и стоит в этом окружении) — не мокаем.
"""

from pathlib import Path

import pytest

from core.pdf_export import PdfExportError, convert_docx_to_pdf

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_convert_docx_to_pdf_produces_readable_pdf(tmp_path):
    docx_path = FIXTURES_DIR / "ksp_sample_1_single_table.docx"

    pdf_path = convert_docx_to_pdf(docx_path, output_dir=tmp_path)

    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.parent == tmp_path
    assert pdf_path.read_bytes().startswith(b"%PDF-")


def test_convert_docx_to_pdf_missing_soffice_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("core.pdf_export.shutil.which", lambda name: None)
    docx_path = FIXTURES_DIR / "ksp_sample_1_single_table.docx"

    with pytest.raises(PdfExportError, match="LibreOffice"):
        convert_docx_to_pdf(docx_path, output_dir=tmp_path)


def test_convert_docx_to_pdf_uses_tempdir_when_no_output_dir_given():
    docx_path = FIXTURES_DIR / "ksp_sample_1_single_table.docx"

    pdf_path = convert_docx_to_pdf(docx_path)

    assert pdf_path.exists()
    assert pdf_path.suffix == ".pdf"


# =====================================================================
# Копии документов не остаются на диске навсегда.
#
# convert_docx_to_pdf без output_dir заводит tempfile.mkdtemp, и убрать
# её обязан вызывающий код. Пока оба места вызова этого не делали, на
# диске копились вечные копии конспектов уроков: /delete_my_data о них
# не знал (в базе лежит только docx_path), и обещание удалить данные
# выполнялось не до конца.
# =====================================================================


def test_вызовы_конвертации_передают_куда_класть_результат():
    """Оба места вызова обязаны назвать папку явно — иначе временная
    папка с копией документа останется на диске навсегда."""
    from pathlib import Path

    корень = Path(__file__).resolve().parent.parent
    for файл in ("bot/handlers.py", "web/api_v1.py"):
        исходник = (корень / файл).read_text(encoding="utf-8")
        вызовы = [
            строка.strip() for строка in исходник.splitlines()
            if "to_thread(convert_docx_to_pdf" in строка
        ]
        assert вызовы, f"{файл}: вызов конвертации в PDF не найден — тест смотрит не туда"
        for строка in вызовы:
            аргументы = строка[строка.index("to_thread(") :]
            # to_thread(convert_docx_to_pdf, путь)       — папки нет
            # to_thread(convert_docx_to_pdf, путь, куда) — папка названа
            assert аргументы.count(",") >= 2, (
                f"{файл}: convert_docx_to_pdf зовётся без папки назначения — результат "
                f"уйдёт во временную папку, которую никто не удалит: {строка}"
            )
