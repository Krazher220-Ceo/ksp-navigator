"""
core/ktp_generator.py — генератор среднесрочного (календарно-тематического)
плана: предмет + класс + часы + (опционально) список тем → JSON → .docx.

Зачем модуль: вторая обязательная бумага педагога (блок Р4, наряду с КСП,
core/ksp_generator.py). Собранный документ сохраняется на диск тем же
принципом, что и КСП (core/ktp_builder.py), а разобранные уроки заодно
попадают в ktp_entries — тем же путём, что при /upload_ktp
(core/ktp_parser.py.save_ktp_entries) — чтобы guess_objective_code видел
их одинаково, независимо от того, загрузил учитель файл или сгенерировал
план здесь.

Что осознанно не делает: не выдумывает коды целей обучения. Реальные коды
в curriculum_objectives сейчас есть только для физики (curriculum_seed.sql,
блок Б1) — для любого другого предмета celi_obucheniya в ответе модели
остаётся пустым, учитель впишет код сам при /generate (тот же путь, что
уже есть для КСП без автоподстановки). Не вычисляет реальные календарные
даты ("Сроки") — это требует знания графика четвертей конкретной школы,
которого у системы нет; поле остаётся пустым, заполняется вручную.

На что опирается: core.llm_client.LLMClient (блок Б2), core.db, core.ktp_builder
(блок Р4.1), core.ktp_parser.save_ktp_entries (блок Б8, переиспользуется
для записи в ktp_entries).
"""

import re
import uuid
from datetime import date, datetime
from pathlib import Path

from core.config import settings
from core.db import query
from core.ktp_builder import QUARTER_LABELS, build_ktp_docx, build_ktp_filename
from core.ktp_parser import save_ktp_entries
from core.llm_client import LLMClient

# Генерация полного КТП (десятки уроков за один ответ) заметно тяжелее
# одного урока КСП — на реальном прогоне DeepSeek первый запрос упёрся в
# дефолтный httpx-таймаут LLMClient (60с) и ушёл в сетевой ретрай, хотя
# сама модель отвечала штатно, просто дольше. Свой, увеличенный таймаут —
# не общий дефолт LLMClient, чтобы не менять поведение KSP-генерации,
# для которой 60с достаточно (core/ksp_generator.py).
_KTP_REQUEST_TIMEOUT_SECONDS = 180.0

SYSTEM_PROMPT = (
    "Ты помогаешь педагогу Республики Казахстан составить черновик "
    "среднесрочного (календарно-тематического) плана по форме, утверждённой "
    "приказом МОН РК №130 от 06.04.2020.\n"
    "Отвечай строго в формате JSON по заданной схеме, без пояснений.\n\n"
    "Правила:\n"
    "- Ровно 4 четверти, в указанном порядке.\n"
    "- Сумма часов всех уроков по всем четвертям должна точно равняться "
    "количеству часов в год, которое дано в задаче.\n"
    "- Если ниже дан список реальных кодов целей обучения — подбирай "
    "celi_obucheniya ТОЛЬКО из этого списка, дословно копируя код. Если ни "
    "один код не подходит теме урока — оставь celi_obucheniya пустой "
    "строкой, не выдумывай новый код.\n"
    "- Если список кодов не дан — celi_obucheniya всегда пустая строка. "
    "Педагог впишет код сам, когда система не знает достоверных кодов для "
    "этого предмета.\n"
    "- Поле 'sroki' всегда пустая строка — точные календарные даты "
    "определяются графиком конкретной школы, система его не знает."
)

TASK_HEADER = "ЗАДАЧА:"
TASK_PREDMET_LABEL = "Предмет:"
TASK_KLASS_LABEL = "Класс:"
TASK_HOURS_WEEK_LABEL = "Часов в неделю:"
TASK_HOURS_YEAR_LABEL = "Часов в год:"
TASK_TOPICS_HEADER = (
    "Список тем (используй ровно эти темы, в этом порядке, распредели по "
    "четвертям и назначь часы каждой так, чтобы сумма сошлась с часами "
    "в год):"
)
TASK_NO_TOPICS_NOTE = (
    "Список тем не дан — составь план сам, по типичной для этого предмета "
    "и класса последовательности разделов."
)
TASK_OBJECTIVES_HEADER = (
    "Реальные коды целей обучения для этого предмета и класса (используй "
    "только их, см. правила выше):"
)
TASK_NO_OBJECTIVES_NOTE = (
    "Проверенных кодов целей обучения для этого предмета в базе нет — "
    "celi_obucheniya у всех уроков оставь пустым."
)

REPAIR_HEADER = "Предыдущий ответ не прошёл проверку по следующим причинам:"
REPAIR_INSTRUCTION = (
    "Исправь именно эти проблемы и верни полный корректный JSON заново, "
    "строго по той же схеме."
)

KTP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "chetverti": {
            "type": "array",
            "description": "Ровно 4 элемента — I, II, III, IV четверти по порядку.",
            "items": {
                "type": "object",
                "properties": {
                    "uroki": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "razdel": {"type": "string"},
                                "tema_uroka": {"type": "string"},
                                "celi_obucheniya": {"type": "string"},
                                "chasov": {"type": "integer"},
                                "primechanie": {"type": "string"},
                            },
                            "required": ["razdel", "tema_uroka", "chasov"],
                        },
                    },
                },
                "required": ["uroki"],
            },
        },
    },
    "required": ["chetverti"],
}

_GRADE_RE = re.compile(r"(\d+)")


class KTPGenerationError(Exception):
    """Базовое исключение генератора КТП."""


class KTPValidationError(KTPGenerationError):
    """Ответ модели дважды подряд не прошёл валидацию."""


def _parse_grade(klass: str) -> int | None:
    match = _GRADE_RE.search(klass or "")
    return int(match.group(1)) if match else None


def _fetch_available_objectives(predmet: str, klass: str, db_path=None) -> list[dict]:
    """Реальные цели обучения из curriculum_objectives — только если они у
    нас реально есть для этого предмета и класса. Сейчас в базе только
    физика (curriculum_seed.sql, блок Б1); таблица не хранит предмет явно
    (коды кодируют его косвенно), поэтому единственный надёжный способ не
    подмешать чужой предмет — сверяться с названием предмета в тексте
    (PLAN_STAGE1_EXT.md, блок Р8, уточнение автора от 25.08.2026: без
    проверенного источника лучше пусто, чем угадывать)."""
    grade = _parse_grade(klass)
    if grade is None:
        return []
    if "физ" not in (predmet or "").strip().lower():
        return []
    rows = query(
        "SELECT code, section, subsection, description FROM curriculum_objectives WHERE grade = ?",
        (grade,),
        db_path=db_path,
    )
    return [dict(r) for r in rows]


def build_prompt(
    predmet: str,
    klass: str,
    chasov_v_nedelu: int,
    chasov_v_god: int,
    topics: list[str] | None = None,
    db_path=None,
) -> str:
    """М4.2 (PLAN_STAGE2.md): objectives (список целей обучения) зависит
    только от predmet+klass — при повторной генерации КТП с тем же
    предметом и классом, но другим списком тем, этот блок совпадает
    слово в слово. Поставлен сразу после predmet/klass/часов и ДО topics
    (списка тем — то, что реально меняется от вызова к вызову), чтобы
    совпадающий префикс при повторных вызовах для одного предмета/класса
    был длиннее для кэша провайдера. Выгода этой перестановки скромнее,
    чем в core.ksp_generator (КТП генерируется редко, лимит 2/сутки, см.
    MASTER.md 0.6 п.5) — но это только порядок строк, ничего не меняет по
    смыслу, поэтому сделано для единообразия с М4.2."""
    objectives = _fetch_available_objectives(predmet, klass, db_path=db_path)

    lines = [
        TASK_HEADER,
        f"{TASK_PREDMET_LABEL} {predmet}",
        f"{TASK_KLASS_LABEL} {klass}",
        f"{TASK_HOURS_WEEK_LABEL} {chasov_v_nedelu}",
        f"{TASK_HOURS_YEAR_LABEL} {chasov_v_god}",
    ]

    if objectives:
        lines.append(TASK_OBJECTIVES_HEADER)
        lines.extend(f"  {o['code']} — {o['description']}" for o in objectives)
    else:
        lines.append(TASK_NO_OBJECTIVES_NOTE)

    if topics:
        lines.append(TASK_TOPICS_HEADER)
        lines.extend(f"  {i+1}. {t}" for i, t in enumerate(topics))
    else:
        lines.append(TASK_NO_TOPICS_NOTE)

    return "\n".join(lines)


def _build_repair_prompt(original_prompt: str, problems: list[str]) -> str:
    problems_text = "\n".join(f"- {p}" for p in problems)
    return f"{original_prompt}\n\n{REPAIR_HEADER}\n{problems_text}\n\n{REPAIR_INSTRUCTION}"


def _validate_ktp_content(content: dict, chasov_v_god: int) -> list[str]:
    """Р4.2, КГ: сумма часов всех уроков должна точно равняться часам в
    год. Ничего не чинит, только диагностирует — решение (повтор/ошибка)
    принимает вызывающий код, тем же путём, что core.ksp_generator."""
    problems: list[str] = []

    chetverti = content.get("chetverti")
    if not isinstance(chetverti, list):
        return ["отсутствует или не список поле 'chetverti'"]
    if len(chetverti) != 4:
        problems.append(f"четвертей {len(chetverti)}, должно быть ровно 4")

    total_hours = 0
    for i, quarter in enumerate(chetverti):
        if not isinstance(quarter, dict) or not isinstance(quarter.get("uroki"), list):
            problems.append(f"chetverti[{i}].uroki отсутствует или не список")
            continue
        for j, urok in enumerate(quarter["uroki"]):
            if not isinstance(urok, dict):
                problems.append(f"chetverti[{i}].uroki[{j}] не объект")
                continue
            for key in ("razdel", "tema_uroka"):
                if not isinstance(urok.get(key), str) or not urok[key].strip():
                    problems.append(f"chetverti[{i}].uroki[{j}].{key} пустое")
            chasov = urok.get("chasov")
            if not isinstance(chasov, int) or chasov <= 0:
                problems.append(f"chetverti[{i}].uroki[{j}].chasov не положительное целое")
            else:
                total_hours += chasov

    if not problems and total_hours != chasov_v_god:
        problems.append(
            f"сумма часов уроков ({total_hours}) не равна часам в год ({chasov_v_god})"
        )

    return problems


async def generate_ktp(
    predmet: str,
    klass: str,
    chasov_v_nedelu: int,
    chasov_v_god: int,
    topics: list[str] | None = None,
    llm_client: LLMClient | None = None,
    db_path=None,
) -> dict:
    """Генерирует и валидирует JSON-содержимое КТП (без сборки .docx —
    save_generated_ktp). При невалидном ответе — ровно один повторный
    запрос; если и он не проходит — KTPValidationError."""
    prompt = build_prompt(predmet, klass, chasov_v_nedelu, chasov_v_god, topics, db_path=db_path)

    client = llm_client or LLMClient(request_timeout=_KTP_REQUEST_TIMEOUT_SECONDS)
    owns_client = llm_client is None
    try:
        content = await client.complete_json(
            system=SYSTEM_PROMPT, user=prompt, schema=KTP_RESPONSE_SCHEMA
        )
        problems = _validate_ktp_content(content, chasov_v_god)

        if problems:
            repair_prompt = _build_repair_prompt(prompt, problems)
            content = await client.complete_json(
                system=SYSTEM_PROMPT, user=repair_prompt, schema=KTP_RESPONSE_SCHEMA
            )
            problems = _validate_ktp_content(content, chasov_v_god)
            if problems:
                raise KTPValidationError(
                    "ответ модели дважды не прошёл валидацию: " + "; ".join(problems)
                )
    finally:
        if owns_client:
            await client.aclose()

    return content


def save_generated_ktp(
    teacher_id: int,
    predmet: str,
    klass: str,
    chasov_v_nedelu: int,
    chasov_v_god: int,
    content: dict,
    generated_at: date | None = None,
    db_path=None,
    output_dir: Path | str | None = None,
) -> dict:
    """Строит .docx (core.ktp_builder) и заодно сохраняет разобранные
    уроки в ktp_entries (core.ktp_parser.save_ktp_entries) — тем же путём,
    что при /upload_ktp: guess_objective_code (core.ksp_generator, F8)
    должен видеть сгенерированный КТП одинаково с загруженным файлом.
    КТП один на учебный год — save_ktp_entries сам заменяет прошлые
    записи этого учителя, не дублирует."""
    generated_at = generated_at or datetime.now().date()

    docx_content = {
        "predmet": predmet,
        "klass": klass,
        "chasov_v_nedelu": chasov_v_nedelu,
        "chasov_v_god": chasov_v_god,
        "chetverti": [
            {"label": QUARTER_LABELS[i], "uroki": q.get("uroki", [])}
            for i, q in enumerate(content["chetverti"])
        ],
    }
    filename = build_ktp_filename(predmet, klass, generated_at)
    out_path = Path(output_dir or settings.generated_dir) / filename
    saved_path = build_ktp_docx(docx_content, out_path)

    entries = []
    for quarter_index, quarter in enumerate(content["chetverti"], start=1):
        for urok in quarter.get("uroki", []):
            entries.append(
                {
                    "lesson_number": None,
                    "section": urok.get("razdel"),
                    "topic": urok.get("tema_uroka"),
                    "objective_code": urok.get("celi_obucheniya") or None,
                    "hours": urok.get("chasov"),
                    "planned_date": None,
                    "quarter": quarter_index,
                }
            )
    ktp_result = save_ktp_entries(teacher_id, entries, db_path=db_path)

    return {
        "id": str(uuid.uuid4()),
        "docx_path": str(saved_path),
        "content_json": docx_content,
        "ktp_entries_inserted": ktp_result["inserted"],
        "ktp_entries_replaced": ktp_result["replaced"],
    }


async def generate_and_save_ktp(
    teacher_id: int,
    predmet: str,
    klass: str,
    chasov_v_nedelu: int,
    chasov_v_god: int,
    topics: list[str] | None = None,
    llm_client: LLMClient | None = None,
    db_path=None,
    output_dir: Path | str | None = None,
) -> dict:
    """Полный конвейер: промпт -> LLM -> валидация -> .docx -> ktp_entries."""
    content = await generate_ktp(
        predmet,
        klass,
        chasov_v_nedelu,
        chasov_v_god,
        topics=topics,
        llm_client=llm_client,
        db_path=db_path,
    )
    return save_generated_ktp(
        teacher_id,
        predmet,
        klass,
        chasov_v_nedelu,
        chasov_v_god,
        content,
        db_path=db_path,
        output_dir=output_dir,
    )
