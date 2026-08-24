"""
core/ktp_parser.py — разбор загруженного файла КТП (.docx/.xlsx) в ktp_entries.

Зачем модуль: команда /upload_ktp (блок Б8) нужна, чтобы у учителя была
СВОЯ таблица ktp_entries, а не только seed-данные физики 10 класса —
без неё guess_objective_code (блок Б6, F8) не сможет подставлять код
цели по теме урока ни для кого, кроме учителя-заглушки id=1.

Этот модуль не был отдельным пунктом в PLAN_STAGE1.md — там Б8.2 просто
требует "приём .docx/.xlsx КТП, разбор в ktp_entries" как часть команды
бота. Разбор файла — не логика бота, поэтому он здесь, в core/, а не в
bot/handlers.py, тем же принципом, что и core/ksp_parser.py.

Что осознанно не делает: не проверяет соответствие objective_code
программе — если код, который встретился в файле, не найден в
curriculum_objectives (внешний ключ ktp_entries.objective_code), запись
всё равно сохраняется, просто с NULL в этом поле (реальный КТП может
ссылаться на коды, которых нет в seed для другого предмета/класса — это
не повод терять всю строку). Не пытается угадать вёрстку идеально при
неоднозначном заголовке — тогда честная ошибка, а не мусор в базе.

На что опирается: python-docx (.docx, все таблицы документа), openpyxl
(.xlsx, первый лист). Определение колонок — по ключевым словам в
заголовке (без внешних библиотек нечёткого поиска: КТП размечен куда
единообразнее, чем «Ход урока» в реальных КСП, см. core/ksp_parser.py).
"""

from pathlib import Path

from docx import Document
from openpyxl import load_workbook

from core.db import execute, query


class KTPParseError(Exception):
    """Не удалось разобрать файл КТП."""


_ROLE_KEYWORDS: dict[str, list[str]] = {
    "lesson_number": ["номер", "№"],
    "section": ["раздел"],
    "topic": ["тема"],
    "objective_code": ["цели обучения", "код цел", "цель обучения"],
    "hours": ["час"],
    "planned_date": ["дата"],
    "quarter": ["четверт"],
}

_MAX_HEADER_SCAN_ROWS = 10


def _normalize(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _match_role(header_cell) -> str | None:
    normalized = _normalize(header_cell)
    if not normalized:
        return None
    for role, keywords in _ROLE_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return role
    return None


def _detect_columns(header_row: list) -> dict[str, int]:
    roles: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        role = _match_role(cell)
        if role and role not in roles:
            roles[role] = idx
    return roles


def _rows_from_docx(path: Path) -> list[list]:
    try:
        document = Document(str(path))
        rows: list[list] = []
        for table in document.tables:
            for row in table.rows:
                rows.append([cell.text for cell in row.cells])
        return rows
    except KTPParseError:
        raise
    except Exception as exc:  # python-docx кидает разные типы на битых файлах
        raise KTPParseError(f"не удалось открыть {path.name} как .docx: {exc}") from exc


def _rows_from_xlsx(path: Path) -> list[list]:
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl кидает zipfile.BadZipFile и т.п. на битых файлах
        raise KTPParseError(f"не удалось открыть {path.name} как .xlsx: {exc}") from exc
    try:
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _parse_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def parse_ktp_file(path: Path | str) -> list[dict]:
    """Возвращает список словарей {lesson_number, section, topic,
    objective_code, hours, planned_date, quarter} — по одной записи на
    распознанную строку данных. Пустые строки-разделители пропускаются
    молча; полное отсутствие узнаваемого заголовка — KTPParseError."""
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".docx":
        rows = _rows_from_docx(path)
    elif suffix == ".xlsx":
        rows = _rows_from_xlsx(path)
    else:
        raise KTPParseError(
            f"неподдерживаемый формат файла: {path.suffix!r}. Ожидается .docx или .xlsx."
        )

    if not rows:
        raise KTPParseError("файл прочитан, но в нём не нашлось ни одной строки")

    columns: dict[str, int] | None = None
    header_row_index = -1
    for i in range(min(_MAX_HEADER_SCAN_ROWS, len(rows))):
        candidate = _detect_columns(rows[i])
        if "topic" in candidate or "section" in candidate:
            columns = candidate
            header_row_index = i
            break

    if columns is None:
        raise KTPParseError(
            "не удалось найти строку-заголовок с колонками КТП "
            "(должны быть узнаваемые «раздел» и/или «тема»)"
        )

    entries: list[dict] = []
    for row in rows[header_row_index + 1 :]:

        def get(role: str):
            idx = columns.get(role)
            if idx is None or idx >= len(row):
                return None
            value = row[idx]
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        topic = get("topic")
        section = get("section")
        if not topic and not section:
            continue

        entries.append(
            {
                "lesson_number": _parse_int(get("lesson_number")),
                "section": section,
                "topic": topic,
                "objective_code": get("objective_code"),
                "hours": _parse_int(get("hours")),
                "planned_date": get("planned_date"),
                "quarter": _parse_int(get("quarter")),
            }
        )

    if not entries:
        raise KTPParseError("заголовок найден, но ни одной строки с данными не распознано")

    return entries


def save_ktp_entries(teacher_id: int, entries: list[dict], db_path=None) -> dict:
    """Вставляет разобранные записи в ktp_entries этого учителя.

    objective_code, которого нет в curriculum_objectives, обнуляется
    перед вставкой — иначе внешний ключ ktp_entries.objective_code
    уронит INSERT (реальный загруженный КТП почти наверняка ссылается
    на коды, которых нет в seed-данных физики 10 класса).

    Возвращает {"inserted": N, "codes_not_found": M} — M нужен, чтобы
    честно сказать учителю в чате, что часть кодов не распозналась,
    а не молча их потерять."""
    known_codes = {row["code"] for row in query("SELECT code FROM curriculum_objectives", db_path=db_path)}

    codes_not_found = 0
    for entry in entries:
        code = entry.get("objective_code")
        if code and code not in known_codes:
            code = None
            codes_not_found += 1

        execute(
            "INSERT INTO ktp_entries "
            "(teacher_id, lesson_number, section, topic, objective_code, hours, planned_date, quarter) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                teacher_id,
                entry.get("lesson_number"),
                entry.get("section"),
                entry.get("topic"),
                code,
                entry.get("hours"),
                entry.get("planned_date"),
                entry.get("quarter"),
            ),
            db_path=db_path,
        )

    return {"inserted": len(entries), "codes_not_found": codes_not_found}
