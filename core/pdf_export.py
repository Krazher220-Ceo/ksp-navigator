"""
core/pdf_export.py — конвертация готового .docx в .pdf (блок Р10).

Зачем модуль: приказ №130 разрешает сдавать план «в формате word (ворд)
или pdf (пдф)» — некоторым учителям удобнее второе. Сам документ уже
собран (core.docx_builder/core.ktp_builder), здесь только конвертация
готового файла через уже установленный LibreOffice.

Что осознанно не делает: не строит PDF напрямую из содержимого — это
дублировало бы всю логику core.docx_builder/core.ktp_builder. Не
проверяет содержимое результата (что таблица не разъехалась, что
пометка «ЧЕРНОВИК» на месте) — это разово проверено вручную при сдаче
блока, автоматической проверки читаемости PDF в проекте нет и не
планируется.

На что опирается: LibreOffice (soffice) — тот же бинарник, что уже
использует core.ksp_parser.ensure_docx для .doc -> .docx.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path


class PdfExportError(Exception):
    """Не удалось сконвертировать .docx в .pdf."""


def convert_docx_to_pdf(docx_path: Path | str, output_dir: Path | str | None = None) -> Path:
    """Конвертирует .docx в .pdf через LibreOffice headless.

    Синхронная и блокирующая функция — вызывающий код (bot/handlers.py)
    обязан звать её через asyncio.to_thread, иначе она заморозит бот на
    время конвертации (та же ловушка, что уже была с .doc -> .docx,
    core.ksp_parser.ensure_docx).
    """
    docx_path = Path(docx_path)

    if shutil.which("soffice") is None:
        raise PdfExportError(
            "LibreOffice (soffice) не найден в PATH — без него PDF не собрать. "
            "Установите: brew install --cask libreoffice, или запустите scripts/setup_mac.sh."
        )

    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="ksp_navigator_pdf_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfExportError(
            f"конвертация {docx_path.name} в .pdf не уложилась в 60 секунд"
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise PdfExportError(
            f"LibreOffice не смог сконвертировать {docx_path.name} (код {result.returncode}): "
            f"{stderr or 'без сообщения об ошибке'}"
        )

    converted = out_dir / f"{docx_path.stem}.pdf"
    if not converted.exists():
        raise PdfExportError(
            f"LibreOffice отработал без ошибки, но файл {converted} не появился"
        )
    return converted
