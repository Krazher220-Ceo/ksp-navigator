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
