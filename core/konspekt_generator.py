"""
core/konspekt_generator.py — конспект урока из транскрипта (блок К4,
PLAN_STAGE2.md).

Зачем модуль: превращает сырой транскрипт записи (core.transcriber,
блок К3) в структурированный конспект — тезисы, формулы, термины,
разобранные примеры, вопросы для самопроверки, домашнее задание.

Что осознанно не делает:
- **Не генерирует конспект без транскрипта.** Единственный источник —
  запись урока. Путь «из темы и учебника, без аудио» обсуждался и был
  отвергнут автором: конспект фиксирует то, что реально было на уроке
  (слой «Факт», MASTER.md раздел 1), а не то, что должно было быть по
  программе — иначе это пересказ учебника, а не конспект. Решение
  зафиксировано в MASTER.md 0.6, пункт 2. Если сюда добавится параметр
  вида "generate_without_transcript" — это ошибка, откатить.
- **Не рендерит формулы через LaTeX** — MASTER.md, п.11, запрещает это
  раньше этапа 3. Формулы — обычным текстом ("p = m*v"), это осознанное
  ограничение, а не недоделка.
- **Не пересказывает то, чего не было.** Модель прямо просят не
  дописывать содержание, отсутствующее в транскрипте — прямое следствие
  принципа честности (MASTER.md 3.3), который здесь начинается, а не
  только в аналитике этапа 3.
- **Не считает "покрытие программы"** — check_coverage ниже (К4.2) это
  одна LLM-проверка одного конспекта против конкретных кодов целей, не
  аналитика этапа 3 (эмбеддинги, пороги, уровень уверенности —
  PLAN_STAGE3.md, отложено до сентября). Формулировка результата —
  "не нашлось упоминания цели X", не "покрытие N%".
- **Не строит .docx/.pdf** — это core/konspekt_builder.py (блок К6).
- **Не определяет говорящих.** Диаризации в проекте нет: опорные реплики
  выделяются по смыслу расшифровки и могут ошибочно попасть не к тому
  говорящему.

На что опирается: core.llm_client.LLMClient (тот же мультипровайдерный
клиент, что и core.ksp_generator/core.ktp_generator), core.db (для
описаний целей обучения в check_coverage).
"""

import json

from core.db import query
from core.llm_client import LLMClient

SYSTEM_PROMPT = (
    "Ты помогаешь педагогу Республики Казахстан составить конспект урока "
    "по расшифровке аудиозаписи этого же урока.\n\n"
    "ГЛАВНОЕ ПРАВИЛО — ЧЕСТНОСТЬ: конспект — это то, что РЕАЛЬНО прозвучало "
    "на уроке по тексту расшифровки. Не дописывай то, чего нет в "
    "расшифровке, даже если по теме урока это было бы уместно или "
    "ожидаемо. Если какой-то части материала в расшифровке нет — просто "
    "не включай её в конспект, не выдумывай и не восстанавливай по "
    "своим знаниям предмета. Расшифровка может быть неточной (ошибки "
    "распознавания речи) — если фраза непонятна, пропусти её, а не "
    "додумывай.\n\n"
    "Сначала выдели опорные реплики: приветствие, объявление темы, постановку "
    "задачи, формулировку задания и завершение урока. Это не стенограмма: "
    "не более одной-двух реплик на каждую опорную точку. Реплики "
    "выделяются по смыслу расшифровки без определения говорящих, поэтому не "
    "приписывай их конкретному человеку. Можно исправить только очевидную "
    "ошибку распознавания в понятном контексте; непонятную фразу пропусти.\n\n"
    "Формулы пиши обычным текстом (например: p = m*v), без специальной "
    "разметки — LaTeX и подобный рендер здесь не поддерживается.\n\n"
    "Отвечай строго в формате JSON по заданной схеме, без пояснений."
)

KONSPEKT_UCHENIKA_SCHEMA = {
    "type": "object",
    "properties": {
        "tema": {"type": "string", "description": "Тема урока, как она следует из расшифровки."},
        "celi": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Чего добивался учитель на уроке — по тому, что реально звучало.",
        },
        "glavnoe": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ключевые тезисы урока — то главное, что было объяснено.",
        },
        "formuly": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "formula": {"type": "string", "description": "Обычным текстом, например: p = m*v"},
                    "znachenie": {"type": "string", "description": "Что означает каждая величина в формуле."},
                },
                "required": ["formula", "znachenie"],
            },
        },
        "primery": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Разобранные на уроке примеры и задачи, как они звучали.",
        },
        "terminy": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "termin": {"type": "string"},
                    "opredelenie": {"type": "string"},
                },
                "required": ["termin", "opredelenie"],
            },
        },
        "voprosy_dlya_samoproverki": {"type": "array", "items": {"type": "string"}},
        "domashnee_zadanie": {
            "type": "string",
            "description": "Если на уроке было озвучено домашнее задание — как оно прозвучало. Если не звучало — пустая строка, не выдумывать.",
        },
    },
    "required": ["tema", "celi", "glavnoe", "voprosy_dlya_samoproverki", "domashnee_zadanie"],
}

KONSPEKT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "opornye_repliki": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Опорные реплики по ходу урока, не более одной-двух на точку, без стенограммы.",
        },
        "konspekt_uchenika": KONSPEKT_UCHENIKA_SCHEMA,
    },
    "required": ["opornye_repliki", "konspekt_uchenika"],
}

_REQUIRED_STUDENT_KEYS = tuple(KONSPEKT_UCHENIKA_SCHEMA["required"])
_MAX_OPORNYE_REPLIKI = 10

# Что показать вместо раздела «Цели», когда целей на записи не прозвучало
# (аудит этапа 2, находка 1). Молча пропустить раздел здесь нельзя: читатель
# не отличит «целей не было» от «модель забыла их выписать». Живёт рядом со
# схемой, а не в bot/texts.py, потому что читают её оба представления
# конспекта — и текст в чате (bot/handlers.py), и .docx
# (core/konspekt_builder.py), а строка должна быть одна на оба.
CELI_NOT_STATED_NOTE = "целей урока на записи не прозвучало"

TASK_HEADER = "ЗАДАЧА:"
TASK_TRANSCRIPT_HEADER = "Расшифровка аудиозаписи урока (может содержать ошибки распознавания речи):"
TASK_TOPIC_HINT = "Подсказка (необязательно, тема урока по календарному плану): {topic}"
TASK_OBJECTIVE_HINT = "Подсказка (необязательно, цель обучения по программе — {code}): {description}"

REPAIR_HEADER = "ПРОБЛЕМЫ В ПРЕДЫДУЩЕМ ОТВЕТЕ:"
REPAIR_INSTRUCTION = (
    "Исправь именно эти проблемы. Не дописывай содержание, которого нет в "
    "расшифровке, даже чтобы исправить проблему — если поле не заполняется "
    "честно, оставь его пустым списком/строкой."
)


class KonspektGenerationError(Exception):
    """Ответ модели дважды не прошёл валидацию — конспект не собран."""


def build_prompt(transcript_text: str, topic: str | None = None, objective_code: str | None = None, db_path=None) -> str:
    """topic/objective_code — необязательный вспомогательный контекст
    (К4.1: "плюс, если есть, тема и код цели из КТП"), НЕ источник
    содержания — конспект строится по transcript_text, подсказки только
    помогают модели сориентироваться в теме, не заменяют расшифровку.

    М4.2-совместимая компоновка: стабильная часть (заголовок задачи) —
    в начало, расшифровка (самая переменная и обычно самая длинная часть) —
    в конец, чтобы не ломать возможный будущий кэш префикса на коротких
    вспомогательных подсказках."""
    parts = [TASK_HEADER]

    if topic:
        parts.append(TASK_TOPIC_HINT.format(topic=topic))
    if objective_code:
        description = _fetch_objective_description(objective_code, db_path=db_path)
        if description:
            parts.append(TASK_OBJECTIVE_HINT.format(code=objective_code, description=description))

    parts.append(f"{TASK_TRANSCRIPT_HEADER}\n{transcript_text.strip()}")
    return "\n\n".join(parts)


def _fetch_objective_description(objective_code: str, db_path=None) -> str | None:
    rows = query("SELECT description FROM curriculum_objectives WHERE code = ?", (objective_code,), db_path=db_path)
    return rows[0]["description"] if rows else None


def _build_repair_prompt(original_prompt: str, problems: list[str]) -> str:
    problems_text = "\n".join(f"- {p}" for p in problems)
    return f"{original_prompt}\n\n{REPAIR_HEADER}\n{problems_text}\n\n{REPAIR_INSTRUCTION}"


def _validate_konspekt_uchenika(content: dict) -> list[str]:
    """Возвращает список найденных проблем (пустой = валидно). Ничего не
    чинит и не дописывает заглушками — только диагностика, решение
    (повтор/ошибка) принимает вызывающий код (тот же принцип, что
    core.ksp_generator._validate_ksp_content, Б6.2)."""
    problems: list[str] = []

    for key in _REQUIRED_STUDENT_KEYS:
        if key not in content:
            problems.append(f"отсутствует обязательное поле '{key}'")
    if problems:
        return problems

    if not isinstance(content["tema"], str) or not content["tema"].strip():
        problems.append("поле 'tema' пустое")

    # Пустой 'celi' — НЕ проблема, и это осознанно (аудит этапа 2,
    # находка 1). Раньше пустой список здесь отклонялся, и это прямо
    # противоречило главному правилу блока: учителя обычно не проговаривают
    # цели урока вслух — цели пишутся в плане, а не произносятся. Отклонение
    # пустого поля оставляло модели два выхода, и оба плохие: либо выдумать
    # цели, которых на записи не было (проверено вживую — модель именно так
    # и делала, дописывая "Ознакомить с разделом..." по теме, а не по
    # записи), либо честно вернуть пустой список второй раз и уронить всю
    # сборку конспекта после двух оплаченных вызовов. Теперь отсутствие
    # целей — законный результат, о котором честно говорится читателю
    # (CELI_NOT_STATED_NOTE), а не повод к выдумыванию.
    glavnoe = content.get("glavnoe") or []
    if not any(str(item).strip() for item in glavnoe):
        problems.append("'glavnoe' пустой список — конспект без главных тезисов бессмыслен")

    for i, formula in enumerate(content.get("formuly") or []):
        if not isinstance(formula, dict):
            problems.append(f"formuly[{i}] не объект")
            continue
        if not formula.get("formula") or not formula.get("znachenie"):
            problems.append(f"formuly[{i}] неполная (нужны formula и znachenie)")

    for i, termin in enumerate(content.get("terminy") or []):
        if not isinstance(termin, dict):
            problems.append(f"terminy[{i}] не объект")
            continue
        if not termin.get("termin") or not termin.get("opredelenie"):
            problems.append(f"terminy[{i}] неполный (нужны termin и opredelenie)")

    return problems


def _validate_konspekt_content(content: dict) -> list[str]:
    """Проверяет две части конспекта, не дописывая отсутствующее."""
    problems: list[str] = []
    for key in KONSPEKT_RESPONSE_SCHEMA["required"]:
        if key not in content:
            problems.append(f"отсутствует обязательное поле '{key}'")
    if problems:
        return problems

    replicas = content["opornye_repliki"]
    if not isinstance(replicas, list) or not all(isinstance(item, str) and item.strip() for item in replicas):
        problems.append("'opornye_repliki' должен быть списком непустых строк")
    elif len(replicas) > _MAX_OPORNYE_REPLIKI:
        problems.append(f"'opornye_repliki' содержит больше {_MAX_OPORNYE_REPLIKI} реплик")

    student = content["konspekt_uchenika"]
    if not isinstance(student, dict):
        problems.append("'konspekt_uchenika' не объект")
    else:
        problems.extend(_validate_konspekt_uchenika(student))
    return problems


async def generate_konspekt(
    transcript_text: str,
    topic: str | None = None,
    objective_code: str | None = None,
    llm_client: LLMClient | None = None,
    db_path=None,
) -> dict:
    """Генерирует и валидирует JSON-содержимое конспекта из транскрипта.
    При невалидном ответе — ровно один повторный запрос с указанием
    конкретных проблем (тот же принцип, что core.ksp_generator.generate_ksp);
    если и он не проходит — KonspektGenerationError. Недостающие поля
    никогда не дописываются заглушками."""
    prompt = build_prompt(transcript_text, topic=topic, objective_code=objective_code, db_path=db_path)

    client = llm_client or LLMClient()
    owns_client = llm_client is None
    try:
        content = await client.complete_json(system=SYSTEM_PROMPT, user=prompt, schema=KONSPEKT_RESPONSE_SCHEMA)
        problems = _validate_konspekt_content(content)

        if problems:
            repair_prompt = _build_repair_prompt(prompt, problems)
            content = await client.complete_json(
                system=SYSTEM_PROMPT, user=repair_prompt, schema=KONSPEKT_RESPONSE_SCHEMA
            )
            problems = _validate_konspekt_content(content)
            if problems:
                raise KonspektGenerationError(
                    "ответ модели дважды не прошёл валидацию: " + "; ".join(problems)
                )
    finally:
        if owns_client:
            await client.aclose()

    return content


# --- К4.2: простая сверка с целями обучения (НЕ аналитика этапа 3) ---

COVERAGE_SYSTEM_PROMPT = (
    "Ты сверяешь готовый конспект урока со списком целей обучения по "
    "программе. Для каждой цели определи, есть ли в конспекте хотя бы "
    "упоминание, отражающее эту цель. Если сомневаешься — считай, что "
    "упоминания НЕТ (лучше показать педагогу лишний повод перепроверить, "
    "чем пропустить реальный пробел). Не выдумывай связь цели с "
    "конспектом, если её явно не видно."
)

COVERAGE_TASK_HEADER = "Конспект урока (JSON):"
COVERAGE_OBJECTIVES_HEADER = "Цели обучения для сверки:"

COVERAGE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "rezultaty": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "naideno": {"type": "boolean"},
                    "kommentariy": {
                        "type": "string",
                        "description": "Коротко, почему найдено/не найдено. Не более одного предложения.",
                    },
                },
                "required": ["code", "naideno"],
            },
        },
    },
    "required": ["rezultaty"],
}


def _build_coverage_prompt(konspekt_content: dict, objectives: list[dict]) -> str:
    objectives_lines = "\n".join(f"- {o['code']}: {o['description']}" for o in objectives)
    konspekt_json = json.dumps(konspekt_content, ensure_ascii=False)
    return (
        f"{COVERAGE_TASK_HEADER}\n{konspekt_json}\n\n"
        f"{COVERAGE_OBJECTIVES_HEADER}\n{objectives_lines}"
    )


async def check_coverage(
    konspekt_content: dict,
    objective_codes: list[str],
    llm_client: LLMClient | None = None,
    db_path=None,
) -> list[dict]:
    """К4.2: "сверяло" из формулировки автора. Одна LLM-проверка одного
    конспекта против конкретных кодов целей обучения — НЕ аналитика
    этапа 3 (там эмбеддинги, пороги, уровень уверенности,
    PLAN_STAGE3.md).

    ВНИМАНИЕ (аудит этапа 2, находка 5): из бота эта функция НЕ
    вызывается — ни одного вызова в bot/, web/ или scripts/. Не потому
    что забыли, а потому что взять objective_codes неоткуда: /konspekt
    (К2.3) не спрашивает ни тему, ни класс, только принимает аудио, а
    связь с ktp_entries идёт именно через них. Место для вызова появится,
    когда у конспекта появится эта связь. Не читать наличие этой функции
    как "сверка с программой работает" — работает механизм, не фича.

    Возвращает список
    {"code", "naideno", "kommentariy"} — по одному на каждый переданный
    код. Пустой objective_codes -> пустой результат, без обращения к LLM."""
    if not objective_codes:
        return []

    objectives = []
    for code in objective_codes:
        description = _fetch_objective_description(code, db_path=db_path)
        objectives.append({"code": code, "description": description or "(описание не найдено в базе)"})

    prompt = _build_coverage_prompt(konspekt_content, objectives)

    client = llm_client or LLMClient()
    owns_client = llm_client is None
    try:
        result = await client.complete_json(system=COVERAGE_SYSTEM_PROMPT, user=prompt, schema=COVERAGE_RESPONSE_SCHEMA)
    finally:
        if owns_client:
            await client.aclose()

    return result.get("rezultaty") or []
