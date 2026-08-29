"""
core/konspekt_builder.py — сборка .docx конспекта урока (блок К6).

Зачем модуль: конспект (core.konspekt_generator, блок К4) до этого блока
отдавался только текстом в чат — этого достаточно для быстрого чтения, но
не для печати или архива. У конспекта нет утверждённой приказом №130
формы (это не отчётный документ, как КСП/КТП, а рабочий материал
педагога) — вёрстка здесь свободная, не копия официальной таблицы.

Что осознанно не делает: не проверяет содержательную корректность content
(это уже сделал core.konspekt_generator при валидации ответа LLM) — если
поле пустое, в документе просто не будет соответствующего раздела, а не
выдуманное значение. Единственное исключение — раздел «Цели»: он печатается
всегда, потому что пустые цели это законный результат (на записи их не
прозвучало), и молчание здесь неотличимо от потерянных данных — см.
CELI_NOT_STATED_NOTE в core.konspekt_generator. Не пишет свою конвертацию
в PDF — core.pdf_export уже умеет конвертировать любой .docx, второй код
для этого не нужен (bot/handlers.py вызывает его напрямую, тем же
_try_send_pdf, что и КСП/КТП).

На что опирается: python-docx (та же библиотека, что и core.docx_builder);
переиспользует оттуда настройку страницы и транслитерацию имени файла
(_apply_page_setup/_transliterate/_strip_unsafe_filename_chars) — не
копирует их второй раз, тот же приём, каким уже пользуется core.ktp_builder.
core.konspekt_generator — за строкой CELI_NOT_STATED_NOTE (одна строка на
оба представления конспекта, чтобы текст в чате и в .docx не разошёлся).
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from core.docx_builder import _apply_page_setup, _strip_unsafe_filename_chars, _transliterate
from core.konspekt_generator import CELI_NOT_STATED_NOTE

# К6, ловушка из плана: пометка F7 у КСП ("ЧЕРНОВИК. Требует проверки и
# утверждения педагогом.") — про отчётный документ, который кто-то
# "утверждает". Конспект — рабочий материал, никто его не "утверждает";
# механическое копирование текста КСП было бы нечестной формулировкой.
# Здесь — про то, что РЕАЛЬНО может пойти не так: ошибки распознавания
# речи (whisper.cpp слышит не идеально), а не выдуманное "утверждение".
DRAFT_NOTICE_TEXT = (
    "Конспект собран автоматически по расшифровке аудиозаписи урока и может "
    "содержать ошибки распознавания речи. Это рабочий материал для педагога, "
    "не отчётный документ — проверьте перед использованием."
)

TITLE_TEXT = "Конспект урока"


def _add_notice(document: Document) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(DRAFT_NOTICE_TEXT)
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)


def _add_title(document: Document, tema: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(TITLE_TEXT)
    run.bold = True
    run.font.size = Pt(14)

    if tema:
        tema_p = document.add_paragraph()
        tema_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        tema_run = tema_p.add_run(tema)
        tema_run.bold = True
        tema_run.font.size = Pt(12)


def _add_heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True


def _add_bullets(document: Document, items: list[str]) -> None:
    for item in items:
        document.add_paragraph(item, style="List Bullet")


def build_konspekt_docx(content: dict, out_path: Path | str) -> Path:
    """Собирает .docx конспекта по content — тому же словарю, что
    возвращает core.konspekt_generator.generate_konspekt и что уже
    отправляется текстом в чат (bot.handlers.format_konspekt_text,
    структура разделов — та же, порядок тот же). Раздел рендерится,
    только если в content для него реально есть данные."""
    document = Document()
    _apply_page_setup(document)
    _add_notice(document)
    # Старые записи в базе были сохранены до К1 плоским словарём. Они
    # остаются читаемыми, а новые ответы имеют два явных раздела.
    student = content.get("konspekt_uchenika", content)
    _add_title(document, student.get("tema", ""))

    _add_heading(document, "Опорные реплики учителя:")
    _add_bullets(document, content.get("opornye_repliki") or ["опорных реплик в записи не нашлось"])
    _add_heading(document, "Конспект для ученика:")

    # Аудит этапа 2, находка 1: раздел «Цели» печатается всегда — пустые
    # цели это законный результат (на записи их не прозвучало), а не
    # потерянные данные, и читатель документа должен видеть разницу.
    _add_heading(document, "Цели:")
    _add_bullets(document, student.get("celi") or [CELI_NOT_STATED_NOTE])

    if student.get("glavnoe"):
        _add_heading(document, "Главное:")
        _add_bullets(document, student["glavnoe"])

    if student.get("formuly"):
        _add_heading(document, "Формулы:")
        _add_bullets(document, [f"{f['formula']} — {f['znachenie']}" for f in student["formuly"]])

    if student.get("terminy"):
        _add_heading(document, "Термины:")
        _add_bullets(document, [f"{t['termin']}: {t['opredelenie']}" for t in student["terminy"]])

    if student.get("primery"):
        _add_heading(document, "Примеры:")
        _add_bullets(document, student["primery"])

    if student.get("voprosy_dlya_samoproverki"):
        _add_heading(document, "Вопросы для самопроверки:")
        _add_bullets(document, student["voprosy_dlya_samoproverki"])

    if student.get("domashnee_zadanie"):
        _add_heading(document, "Домашнее задание:")
        document.add_paragraph(student["domashnee_zadanie"])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(out_path)
    return out_path


def build_konspekt_filename(tema: str, generated_at) -> str:
    """Конспект_{тема_транслитом}_{ГГГГ-ММ-ДД}.docx, длина ≤ 100 символов
    — тот же принцип, что core.docx_builder.build_filename, но без
    предмета и класса (у конспекта их нет, /konspekt их не спрашивает,
    К2.3). generated_at — date/datetime или строка вида "2025-11-12"
    (для тестов с фиксированной датой)."""
    if hasattr(generated_at, "strftime"):
        date_str = generated_at.strftime("%Y-%m-%d")
    else:
        date_str = str(generated_at)

    topic_slug = _strip_unsafe_filename_chars(_transliterate(tema or "urok"))

    prefix = "Конспект_"
    suffix = f"_{date_str}.docx"
    max_topic_len = max(1, 100 - len(prefix) - len(suffix))
    topic_slug = topic_slug[:max_topic_len]

    return f"{prefix}{topic_slug}{suffix}"
