"""
core/ksp_parser.py — разбор реальных файлов КСП и построение профиля стиля учителя.

Зачем модуль: единственное место, где .doc/.docx конкретного учителя
превращается в структуру, из которой потом LLM извлекает манеру письма
(core/ksp_generator.py, блок Б6, будет опираться на style_profiles).

Что осознанно не делает: не проверяет, что документ вообще является
КСП (это не парсер валидации, а парсер извлечения — мусор на входе даёт
пустую структуру, а не исключение). НИКОГДА не подмешивает в профиль
стиля документы из библиотеки шаблонов (core/templates.py, блок Б4) —
шаблон описывает форму, профиль стиля — манеру письма ОДНОГО учителя;
это разные сущности (MASTER.md, п.1.1.1 и п.11), и ответственность за
то, чтобы не перепутать входные данные, лежит на вызывающем коде
(bot/handlers.py, блок Б8): сюда должны попадать только реальные,
ранее написанные КСП этого учителя.

На что опирается: python-docx для чтения .docx, LibreOffice (soffice)
для конвертации .doc, core.llm_client.LLMClient для построения профиля,
core.db для сохранения в style_profiles.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document

from core.db import execute, query
from core.llm_client import LLMClient


# Префикс временной папки под результат конвертации .doc -> .docx.
# Вынесен в константу, потому что по нему parse_ksp узнаёт СВОЮ папку и
# только её удаляет: rmtree по "родителю разобранного файла" без такой
# проверки однажды снёс бы storage/uploads целиком.
_TEMP_DOCX_PREFIX = "ksp_navigator_docx_"


class KSPConversionError(Exception):
    """Не удалось сконвертировать .doc в .docx."""


class KSPParseError(Exception):
    """Не удалось разобрать содержимое файла как .docx."""


# --- Б3.1: конвертация .doc -> .docx ---


def ensure_docx(path: Path | str) -> Path:
    """Если path уже .docx — возвращает его как есть. Если .doc —
    конвертирует через LibreOffice во временную папку и возвращает путь
    к результату. pandoc здесь не подходит: он не читает бинарный .doc,
    только .docx (см. MASTER.md, уточнение в разделе про Python-стек).

    Временная папка с результатом конвертации не удаляется автоматически —
    вызывающий код (parse_ksp) читает файл сразу же, а уборка временных
    файлов не критична для однопользовательского бота на одном Mac.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".docx":
        return path

    if suffix != ".doc":
        raise KSPConversionError(
            f"неподдерживаемый формат файла: {path.suffix!r}. Ожидается .doc или .docx."
        )

    if shutil.which("soffice") is None:
        raise KSPConversionError(
            "LibreOffice (soffice) не найден в PATH — без него .doc не читается. "
            "Установите: brew install --cask libreoffice, или запустите scripts/setup_mac.sh."
        )

    # Папка убирается на КАЖДОМ выходе с ошибкой: конвертация падает
    # регулярно (сломанный профиль LibreOffice, таймаут, битый .doc), и
    # без этого каждая неудача оставляла пустую папку на диске, а удачная
    # — папку с полной копией КСП педагога. Убирает её parse_ksp, когда
    # дочитает файл; за путь до успеха отвечает этот try.
    out_dir = Path(tempfile.mkdtemp(prefix=_TEMP_DOCX_PREFIX))
    try:
        try:
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(path)],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise KSPConversionError(
                f"конвертация {path.name} в .docx не уложилась в 60 секунд"
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise KSPConversionError(
                f"LibreOffice не смог сконвертировать {path.name} (код {result.returncode}): "
                f"{stderr or 'без сообщения об ошибке'}"
            )

        converted = out_dir / f"{path.stem}.docx"
        if not converted.exists():
            raise KSPConversionError(
                f"LibreOffice отработал без ошибки, но файл {converted} не появился"
            )
        return converted
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


# --- Б3.2: извлечение структуры документа ---

# Роль колонки таблицы "Ход урока" -> варианты ключевых слов (группы "И").
# Ячейка совпадает с ролью, если хотя бы одна группа целиком найдена в её
# тексте (все слова группы присутствуют, каждое — с учётом опечаток).
_ROLE_KEYWORD_GROUPS: dict[str, list[list[str]]] = {
    "etap_vremya": [["этап"]],
    "deystviya_pedagoga": [["действ", "педагог"]],
    "deystviya_uchenika": [["действ", "ученик"], ["действ", "обучающ"]],
    "resursy": [["ресурс"]],
    "ocenivanie": [["оценив"]],
}

_MIN_ROLE_MATCHES_FOR_HEADER = 3


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
        previous = current
    return previous[-1]


def _matches_keyword(cell_text_lower: str, keyword: str) -> bool:
    """Ищет keyword (короткий корень слова) в тексте ячейки: сначала
    точным вхождением (покрывает разный регистр и лишние пробелы —
    cell_text_lower уже нормализован), затем — опечатку в корне слова
    сравнением с окном той же длины, что и keyword. Сравнение с целым
    словом здесь не годится: падежное окончание ("действия" длиннее
    "действ") само по себе накручивает расстояние сильнее любой опечатки."""
    if keyword in cell_text_lower:
        return True
    klen = len(keyword)
    for word in cell_text_lower.split():
        if len(word) < 3:
            continue
        window = word[:klen]
        if _levenshtein(window, keyword) <= 2:
            return True
    return False


def _match_cell_role(cell_text: str) -> str | None:
    lowered = cell_text.lower()
    for role, groups in _ROLE_KEYWORD_GROUPS.items():
        for group in groups:
            if all(_matches_keyword(lowered, kw) for kw in group):
                return role
    return None


def _is_heading_paragraph(paragraph) -> bool:
    style_name = ""
    if paragraph.style is not None and paragraph.style.name:
        style_name = paragraph.style.name.lower()
    if "heading" in style_name or "title" in style_name or "заголов" in style_name:
        return True

    text = _normalize_ws(paragraph.text)
    if not text or len(text) > 100:
        return False
    runs = [r for r in paragraph.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs)


def _extract_tables(document: Document) -> list[list[list[str]]]:
    """Все таблицы документа как список строк-ячеек. Полностью пустые
    строки (строки-разделители, частые в реальных КСП) отбрасываются;
    отдельные пустые ячейки внутри строки остаются на своих местах —
    их позиция важна для сопоставления колонок таблицы "Ход урока"."""
    tables: list[list[list[str]]] = []
    for table in document.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [_normalize_ws(cell.text) for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _extract_lesson_plan_table(tables: list[list[list[str]]]) -> dict | None:
    """Ищет среди всех таблиц документа ту, что похожа на "Ход урока":
    строку, где минимум 3 из 5 ролей узнаются по ключевым словам в
    ячейках, независимо от того, какая это по счёту таблица и какая по
    счёту строка внутри неё (учителя верстают доки по-разному — где-то
    одна таблица на документ, где-то пять).

    Сканирует ВСЕ строки каждой таблицы, а не только первые несколько:
    в документе с единственной таблицей на весь КСП строка-заголовок
    "Ход урока" может оказаться восьмой-девятой по счёту (перед ней —
    раздел, ФИО, дата, тема, цели). Ложных срабатываний на строках с
    данными это на практике не даёт: реальному заголовку нужно совпасть
    сразу по 3-5 ролям в одной строке, а лучший (наибольший) счёт всегда
    побеждает — совпадение "вничью" оставляет более раннюю строку."""
    best: tuple[int, int, int, dict[str, int]] | None = None  # score, t_idx, r_idx, role_to_col

    for t_idx, table in enumerate(tables):
        for r_idx in range(len(table)):
            role_to_col: dict[str, int] = {}
            for c_idx, cell_text in enumerate(table[r_idx]):
                role = _match_cell_role(cell_text)
                if role and role not in role_to_col:
                    role_to_col[role] = c_idx
            score = len(role_to_col)
            if score >= _MIN_ROLE_MATCHES_FOR_HEADER and (best is None or score > best[0]):
                best = (score, t_idx, r_idx, role_to_col)

    if best is None:
        return None

    _, t_idx, header_row_idx, role_to_col = best
    table = tables[t_idx]

    rows = []
    for row in table[header_row_idx + 1 :]:
        entry = {role: (row[col] if col < len(row) else "") for role, col in role_to_col.items()}
        if any(entry.values()):
            rows.append(entry)

    return {
        "table_index": t_idx,
        "header_row_index": header_row_idx,
        "columns": role_to_col,
        "rows": rows,
    }


def parse_ksp(path: Path | str) -> dict:
    """Разбирает один файл КСП (.doc или .docx) в структуру:
    заголовки, абзацы вне таблиц, все таблицы, и (если найдена)
    отдельно распознанная таблица "Ход урока" с ролями колонок.

    Не роняет процесс на "странном" документе — при отсутствии
    подходящей таблицы lesson_plan_table будет None, а не исключение.
    Единственное, что может кинуть исключение — сам файл нечитаем
    как .docx (битый файл, не Word-документ и т.п.)."""
    docx_path = ensure_docx(path)
    # ensure_docx на .doc кладёт результат во временную папку и владельца
    # ей не назначает. Пока её никто не убирал, каждый разобранный .doc
    # оставлял на диске ПОЛНУЮ КОПИЮ КСП педагога — навсегда, и
    # /delete_my_data о ней не знал: он удаляет то, на что ссылается
    # база, а этот путь нигде не хранится. Убираем сразу после чтения:
    # дальше нужен только разобранный текст.
    временная_папка = (
        docx_path.parent
        if docx_path != Path(path) and docx_path.parent.name.startswith(_TEMP_DOCX_PREFIX)
        else None
    )

    try:
        document = Document(str(docx_path))
    except Exception as exc:  # python-docx кидает разные типы на битых файлах
        raise KSPParseError(f"не удалось открыть {path} как .docx: {exc}") from exc
    finally:
        if временная_папка is not None:
            shutil.rmtree(временная_папка, ignore_errors=True)

    headings: list[str] = []
    paragraphs: list[str] = []
    for paragraph in document.paragraphs:
        text = _normalize_ws(paragraph.text)
        if not text:
            continue
        if _is_heading_paragraph(paragraph):
            headings.append(text)
        else:
            paragraphs.append(text)

    tables = _extract_tables(document)
    lesson_plan_table = _extract_lesson_plan_table(tables)

    return {
        "source_path": str(path),
        "headings": headings,
        "paragraphs": paragraphs,
        "tables": tables,
        "lesson_plan_table": lesson_plan_table,
    }


# --- Б3.3: построение профиля стиля через LLM ---

STYLE_PROFILE_SYSTEM_PROMPT = (
    "Ты анализируешь несколько реальных, ранее написанных краткосрочных "
    "планов урока (КСП) ОДНОГО педагога, чтобы описать его манеру письма. "
    "Не придумывай ничего, чего нет в переданных документах — используй "
    "только то, что реально встречается в тексте. Если какого-то элемента "
    "в документах нет, оставь соответствующий список пустым, не выдумывай."
)

STYLE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "goal_phrasing": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 формулировок целей урока, взятых дословно из документов",
        },
        "stage_structure": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string"},
                    "timing": {"type": "string"},
                },
            },
            "description": "типичные этапы урока с таймингом, в порядке следования",
        },
        "assessment_methods": {
            "type": "array",
            "items": {"type": "string"},
            "description": "методы оценивания, встречающиеся больше одного раза",
        },
        "resources_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "ресурсы, которые педагог обычно указывает",
        },
    },
    "required": ["goal_phrasing", "stage_structure", "assessment_methods", "resources_used"],
}


def _render_document_for_prompt(index: int, parsed: dict) -> str:
    lines = [f"--- Документ {index} ---"]

    if parsed.get("headings"):
        lines.append("Заголовки: " + " | ".join(parsed["headings"]))

    if parsed.get("paragraphs"):
        lines.append("Текст вне таблиц:")
        lines.extend(f"  {p}" for p in parsed["paragraphs"])

    # Все таблицы документа целиком, не только "Ход урока": в реальных
    # КСП "Раздел", "Тема урока", "Цели обучения", "Цели урока" почти
    # всегда лежат внутри таблиц (см. официальную форму, MASTER.md п.4),
    # а не отдельными заголовками/абзацами. Без этого блока LLM физически
    # не видит формулировки целей — найдено не в теории, а на реальном
    # прогоне приёмки (Б12.2): goal_phrasing стабильно приходил пустым
    # список, хотя цели в документах были, просто в ячейках таблицы.
    if parsed.get("tables"):
        lines.append("Таблицы документа (все строки, включая шапку):")
        for table in parsed["tables"]:
            for row in table:
                row_text = " | ".join(cell for cell in row if cell)
                if row_text:
                    lines.append(f"  {row_text}")

    lesson_table = parsed.get("lesson_plan_table")
    if lesson_table and lesson_table.get("rows"):
        lines.append("Ход урока (роли колонок распознаны явно):")
        for row in lesson_table["rows"]:
            row_text = "; ".join(f"{role}: {text}" for role, text in row.items() if text)
            if row_text:
                lines.append(f"  {row_text}")

    return "\n".join(lines)


def _build_style_prompt(parsed_list: list[dict]) -> str:
    documents_text = "\n\n".join(
        _render_document_for_prompt(i, parsed) for i, parsed in enumerate(parsed_list, start=1)
    )
    return (
        f"Вот {len(parsed_list)} реальных КСП одного педагога:\n\n"
        f"{documents_text}\n\n"
        "На основе ТОЛЬКО этих документов заполни четыре поля:\n"
        "- goal_phrasing: 3-5 характерных формулировок целей урока, взятых "
        "дословно из документов;\n"
        "- stage_structure: типичные этапы урока с их таймингом, в порядке "
        "следования;\n"
        "- assessment_methods: методы оценивания, которые встречаются больше "
        "одного раза;\n"
        "- resources_used: ресурсы, которые педагог обычно указывает."
    )


async def build_style_profile(
    parsed_list: list[dict],
    llm_client: LLMClient | None = None,
) -> dict:
    """Строит профиль стиля педагога из 2-5 РЕАЛЬНЫХ, ранее написанных им
    КСП (parsed_list — результаты parse_ksp этих файлов).

    НИКОГДА не передавай сюда документы из библиотеки шаблонов
    (core/templates.py, блок Б4): шаблон задаёт форму, профиль стиля —
    манеру письма конкретного педагога, подмешивать образцовый текст
    из интернета в профиль запрещено (MASTER.md, п.1.1.1 и п.11)."""
    if not parsed_list:
        raise ValueError("build_style_profile: нужен хотя бы один разобранный КСП")

    client = llm_client or LLMClient()
    owns_client = llm_client is None

    try:
        result = await client.complete_json(
            system=STYLE_PROFILE_SYSTEM_PROMPT,
            user=_build_style_prompt(parsed_list),
            schema=STYLE_PROFILE_SCHEMA,
        )
    finally:
        if owns_client:
            await client.aclose()

    return {
        "goal_phrasing": result.get("goal_phrasing", []),
        "stage_structure": result.get("stage_structure", []),
        "assessment_methods": result.get("assessment_methods", []),
        "resources_used": result.get("resources_used", []),
        "raw_samples_count": len(parsed_list),
    }


# --- Б3.4: сохранение профиля ---


def save_style_profile(teacher_id: int, profile: dict, db_path=None) -> None:
    """Upsert профиля стиля: один профиль на учителя. В schema.sql нет
    UNIQUE-ограничения на style_profiles.teacher_id (менять уже принятую
    в Б1 схему здесь не входит в задачи этого блока), поэтому уникальность
    держит эта функция сама — проверяет наличие строки и обновляет её,
    а не полагается на ON CONFLICT."""
    existing = query(
        "SELECT id FROM style_profiles WHERE teacher_id = ?", (teacher_id,), db_path=db_path
    )

    payload = (
        json.dumps(profile.get("goal_phrasing", []), ensure_ascii=False),
        json.dumps(profile.get("stage_structure", []), ensure_ascii=False),
        json.dumps(profile.get("assessment_methods", []), ensure_ascii=False),
        json.dumps(profile.get("resources_used", []), ensure_ascii=False),
        profile.get("raw_samples_count", 0),
    )

    if existing:
        execute(
            "UPDATE style_profiles SET goal_phrasing = ?, stage_structure = ?, "
            "assessment_methods = ?, resources_used = ?, raw_samples_count = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE teacher_id = ?",
            payload + (teacher_id,),
            db_path=db_path,
        )
    else:
        execute(
            "INSERT INTO style_profiles "
            "(teacher_id, goal_phrasing, stage_structure, assessment_methods, "
            "resources_used, raw_samples_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (teacher_id,) + payload,
            db_path=db_path,
        )
