"""
core/ksp_generator.py — генератор КСП: тема + код цели + профиль стиля → JSON.

Зачем модуль: единственное место, где реальные входные данные учителя
(тема урока, код цели обучения, класс, продолжительность) и его профиль
стиля (core/ksp_parser.py, блок Б3, опционально) превращаются в JSON,
из которого core/docx_builder.py (блок Б5) строит файл.

Что осознанно не делает: не выбирает шаблон и не форматирует документ —
это ответственность вызывающего кода и core/docx_builder.py. Не
подставляет заглушки вместо недостающих данных ни при валидации (Б6.2),
ни при подборе кода цели (Б6.3, guess_objective_code) — там, где данных
нет, возвращается честное None/исключение, а не выдумка.

На что опирается: core.llm_client.LLMClient (блок Б2), core.db (блок
Б1), core.docx_builder (блок Б5), core.templates.get_template (блок Б4).
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core.config import settings
from core.db import execute, query
from core.docx_builder import build_docx, build_filename
from core.llm_client import LLMClient
from core.templates import get_template
from core.values import get_value

# =====================================================================
# Р5.2/Р5.3 — необязательные опции урока сверх обязательного минимума
# Б6.1. Одним объектом, а не россыпью параметров у каждой функции: их
# много (7+), логически это одна группа настроек "что ещё учесть", и
# добавлять их по одной как позиционные/именованные параметры build_prompt/
# generate_ksp/generate_and_save_ksp сделало бы сигнатуры нечитаемыми.
# =====================================================================

WORK_FORMS = [
    "Самостоятельная работа",
    "Парная работа",
    "Групповая работа",
    "Проектная деятельность",
    "Лабораторная работа",
    "Практическая работа",
]
FUNCTIONAL_LITERACY_TYPES = [
    "Математическая грамотность",
    "Грамотность чтения",
    "Естественно-научная грамотность",
    "Финансовая грамотность",
    "Креативное мышление",
]
MAX_VIDY_DEYATELNOSTI = 3

TIP_UROKA_OPTIONS = [
    "Комбинированный урок",
    "Изучение нового материала",
    "Закрепление",
    "Контроль",
]


@dataclass
class LessonOptions:
    """Необязательные опции урока (Р5.2, Р5.3). Все поля по умолчанию
    "выключены" — учитель, который не заполнял расширенную часть формы,
    получает ровно тот же результат, что и до блока Р5."""

    cennost_key: str | None = None  # ключ из core.values.VALUES
    vidy_deyatelnosti: list[str] = field(default_factory=list)  # до MAX_VIDY_DEYATELNOSTI
    ima_oop: bool = False
    sor_instead_of_reflection: bool = False
    fizkultminutka: bool = False
    predvaritelnye_znaniya: str | None = None
    tip_uroka: str | None = None
    mezhpredmetnye_svyazi: list[str] = field(default_factory=list)
    page_orientation: str = "book"  # "book" | "album" — влияет только на docx_builder, не на промпт

    def __post_init__(self) -> None:
        # Р5.2, ловушка из плана: "до трёх" — ограничение, не пожелание.
        # Отсекаем здесь, а не полагаемся, что вызывающий код проверит сам.
        if len(self.vidy_deyatelnosti) > MAX_VIDY_DEYATELNOSTI:
            self.vidy_deyatelnosti = self.vidy_deyatelnosti[:MAX_VIDY_DEYATELNOSTI]

# =====================================================================
# Тексты промптов — строго по MASTER.md, раздел 1.6. Ничего из этого
# не вкраплено в код ниже: сборка промпта только компонует эти куски.
# =====================================================================

SYSTEM_PROMPT = (
    "Ты помогаешь педагогу Республики Казахстан составить черновик "
    "краткосрочного (поурочного) плана по форме, утверждённой приказом "
    "МОН РК №130 от 06.04.2020.\n"
    "Отвечай строго в формате JSON по заданной схеме, без пояснений.\n\n"
    "Требования к качеству содержания (PLAN_STAGE1_EXT.md, блок Р2 —\n"
    "разбор реального КСП конкурента показал, что короткие названия\n"
    "действий вместо реального содержания — то, из-за чего учитель\n"
    "переписывает документ заново, а не пользуется черновиком):\n"
    "- 'Действия педагога' — не название действия, а то, что учитель "
    "реально произносит классу: полная реплика прямой речью в кавычках, "
    "3-6 предложений на этап. Плохо: 'Объясняет тему'. Хорошо: сама "
    "объяснительная реплика целиком, с конкретными вопросами к классу.\n"
    "- 'Оценивание' — конкретные проверяемые дескрипторы (2-4 штуки), "
    "каждый с новой строки через дефис, а не название метода. Плохо: "
    "'Устный опрос'. Хорошо: что именно проверяется ('называет не менее "
    "двух причин явления', 'верно применяет формулу').\n"
    "- 'Ресурсы' — конкретные, при уместности включая цифровые "
    "инструменты (интерактивная доска, симулятор, приложение), не только "
    "учебник.\n"
    "- Если ниже есть КОНТЕКСТ с манерой этого педагога — формулировки "
    "должны быть в его стиле, а не обобщённые."
)

CONTEXT_HEADER = "КОНТЕКСТ — стиль этого педагога (из его прошлых КСП):"
CONTEXT_GOAL_PHRASING_LABEL = "- формулировки целей урока:"
CONTEXT_STAGE_STRUCTURE_LABEL = "- типичная структура этапов:"
CONTEXT_ASSESSMENT_METHODS_LABEL = "- методы оценивания:"
CONTEXT_RESOURCES_USED_LABEL = "- используемые ресурсы:"

TASK_HEADER = "ЗАДАЧА:"
TASK_TOPIC_LABEL = "Тема урока:"
TASK_RAZDEL_LABEL = "Раздел:"
TASK_OBJECTIVE_LABEL = "Цель обучения по программе:"
TASK_KLASS_LABEL = "Класс:"
TASK_DURATION_LABEL = "Продолжительность:"

# Колонки "Хода урока", которых нет в официальной форме №130, но которые
# может просить выбранный шаблон (core/templates.py). Модель узнаёт о них
# из промпта — иначе она просто не знает, что документ их ждёт, и они
# выходят пустыми.
EXTRA_COLUMN_LABELS = {
    "domashnee_zadanie": "домашнее задание",
    "dop_literatura": "дополнительная литература",
    # Р5.3: не от шаблона, а от флага "ima_oop" (LessonOptions) — тот же
    # механизм подсказки модели переиспользован для генерационного флага,
    # не только для колонок, зашитых в конкретный шаблон (см. build_prompt).
    "differenciaciya_oop": "дифференциация/адаптация для обучающихся с ООП",
}
TASK_EXTRA_COLUMNS_LABEL = (
    "Выбранный шаблон дополнительно требует заполнить в каждом этапе урока:"
)

# Р3.1: раздаточные материалы — необязательная часть ответа, включается
# только по явному запросу учителя (include_razdatochnye_materialy=True;
# сама галочка в форме бота появится в блоке Р5, здесь — только механизм).
# НЕ входит в required схемы: документ без карточек остаётся законным по
# приказу №130, это не часть его формы, а сервис сверх неё.
TASK_RAZDATOCHNYE_MATERIALY_INSTRUCTION = (
    "Дополнительно подготовь раздаточные материалы для урока: 2-3 "
    "разноуровневые карточки с заданиями (уровни: базовый, основной, "
    "продвинутый; у каждого своя цветная метка — зелёная/жёлтая/синяя). "
    "У каждой карточки: условие задания, при уместности — подсказка "
    "(формула, метод), и решение, если это расчётная задача (для "
    "творческого/исследовательского задания решения может не быть — "
    "тогда оставь его пустым, не выдумывай). Плюс отдельно — 2-4 общих "
    "критерия успеха урока."
)

# Р5.1: ценность для интеграции — имя ценности мы уже знаем сама (core.
# values), модель НЕ должна её придумывать или переформулировать; задача
# модели — вплести её реально в ход урока, не просто упомянуть в шапке.
TASK_CENNOST_LABEL = "Ценность для интеграции в урок (обязательно отрази в ходе урока, не только формально):"

# Р5.2: виды учебной деятельности — до MAX_VIDY_DEYATELNOSTI, отсекается
# на входе (LessonOptions.__post_init__), здесь только рендер того, что
# уже прошло проверку.
TASK_VIDY_DEYATELNOSTI_LABEL = "Виды учебной деятельности, которые нужно заложить в урок:"

# Р5.3: флаги урока — каждый включается независимо, текст добавляется в
# промпт только если флаг реально включён (иначе промпт не меняется
# вообще для учителя, который не трогал расширенную форму).
TASK_OOP_INSTRUCTION = (
    "В классе есть обучающиеся с особыми образовательными потребностями — "
    "для каждого этапа урока заполни поле 'differenciaciya_oop': конкретная "
    "адаптация именно этого этапа (не общая фраза)."
)
TASK_SOR_INSTRUCTION = (
    "Вместо отдельных этапов закрепления и рефлексии сделай один "
    "заключительный этап с названием, начинающимся на 'СОР' (суммативное "
    "оценивание раздела) — он завершает урок, отдельной рефлексии после "
    "него быть не должно."
)
TASK_FIZKULTMINUTKA_INSTRUCTION = (
    "Добавь короткий этап физкультминутки где-то в середине урока (не "
    "первым и не последним этапом)."
)
TASK_TIP_UROKA_LABEL = "Тип урока:"
TASK_MEZHPREDMETNYE_SVYAZI_LABEL = "Учти межпредметные связи с предметами:"

# Р6.2: текст страницы учебника, распознанный по фото (core.textbook_ocr).
TASK_TEXTBOOK_HEADER = (
    "Текст со страницы учебника, распознанный по фото — урок должен "
    "реально опираться на это содержание, не только на тему:"
)


def _render_lesson_options(options: "LessonOptions | None") -> str:
    """Рендерит инструкции по опциям Р5.2/Р5.3 в промпт. Пустая строка,
    если опций нет вообще — учитель, не трогавший расширенную форму, не
    должен получать другой промпт (Р5: "не превращать диалог в анкету")."""
    if options is None:
        return ""

    lines: list[str] = []

    cennost = get_value(options.cennost_key) if options.cennost_key else None
    if cennost:
        lines.append(f"{TASK_CENNOST_LABEL} {cennost['name']} — {cennost['goal']}.")

    if options.vidy_deyatelnosti:
        lines.append(f"{TASK_VIDY_DEYATELNOSTI_LABEL} {', '.join(options.vidy_deyatelnosti)}.")

    if options.tip_uroka:
        lines.append(f"{TASK_TIP_UROKA_LABEL} {options.tip_uroka}")

    if options.mezhpredmetnye_svyazi:
        lines.append(f"{TASK_MEZHPREDMETNYE_SVYAZI_LABEL} {', '.join(options.mezhpredmetnye_svyazi)}.")

    if options.ima_oop:
        lines.append(TASK_OOP_INSTRUCTION)
    if options.sor_instead_of_reflection:
        lines.append(TASK_SOR_INSTRUCTION)
    if options.fizkultminutka:
        lines.append(TASK_FIZKULTMINUTKA_INSTRUCTION)

    return "\n".join(lines)


RAZDATOCHNYE_MATERIALY_RESPONSE_PROPERTIES = {
    "razdatochnye_materialy": {
        "type": "array",
        "description": "Разноуровневые карточки с заданиями (Р3.1).",
        "items": {
            "type": "object",
            "properties": {
                "uroven": {"type": "string", "description": "базовый / основной / продвинутый"},
                "metka": {"type": "string", "description": "цвет метки уровня"},
                "zadanie": {"type": "string"},
                "podskazka": {"type": "string"},
                "reshenie": {"type": "string"},
            },
            "required": ["uroven", "zadanie"],
        },
    },
    "kriterii_uspeha": {
        "type": "array",
        "items": {"type": "string"},
        "description": "2-4 общих критерия успеха урока (Р3.1).",
    },
}

REPAIR_HEADER = "Предыдущий ответ не прошёл проверку по следующим причинам:"
REPAIR_INSTRUCTION = (
    "Исправь именно эти проблемы и верни полный корректный JSON заново, строго "
    "по той же схеме. Не оставляй пустые поля и не придумывай данные, которых "
    "нет в задаче, — если чего-то не хватает, сформулируй содержательно на "
    "основе темы и цели урока."
)

# Схема ответа — дословно структура из MASTER.md, раздел 1.6
# (hod_uroka: "etap" и "vremya" отдельными полями, не объединённые —
# так задано в MASTER.md; core/docx_builder.py уже умеет принимать
# оба варианта, см. блок Б5).
KSP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tema_uroka": {"type": "string"},
        "razdel": {"type": "string"},
        "celi_obucheniya": {"type": "string"},
        "celi_uroka": {"type": "array", "items": {"type": "string"}},
        "hod_uroka": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "etap": {"type": "string"},
                    "vremya": {"type": "string"},
                    "deystviya_pedagoga": {
                        "type": "string",
                        "description": (
                            "Полная реплика учителя прямой речью в кавычках, "
                            "3-6 предложений. Не название действия."
                        ),
                    },
                    "deystviya_uchenika": {"type": "string"},
                    "resursy": {
                        "type": "string",
                        "description": (
                            "Конкретные ресурсы, при уместности включая "
                            "цифровые (симулятор, приложение), не только учебник."
                        ),
                    },
                    "ocenivanie": {
                        "type": "string",
                        "description": (
                            "2-4 проверяемых дескриптора, каждый с новой строки "
                            "через дефис. Не название метода оценивания."
                        ),
                    },
                    # Колонки сверх официальной формы: их просит шаблон
                    # "Развёрнутый образец" (storage/builtin_templates/
                    # extended_ktp.json). В required их нет намеренно —
                    # официальной форме №130 они не нужны, и требовать их
                    # от модели всегда значило бы заставлять её выдумывать
                    # домашнее задание там, где шаблон его не спрашивает.
                    # Но и не знать о них нельзя: пока их не было в схеме,
                    # по этому шаблону две колонки из семи выходили
                    # пустыми при любой генерации.
                    "domashnee_zadanie": {"type": "string"},
                    "dop_literatura": {"type": "string"},
                    "differenciaciya_oop": {
                        "type": "string",
                        "description": (
                            "Как адаптировано для обучающихся с особыми "
                            "образовательными потребностями на этом этапе."
                        ),
                    },
                },
                "required": [
                    "etap",
                    "vremya",
                    "deystviya_pedagoga",
                    "deystviya_uchenika",
                    "resursy",
                    "ocenivanie",
                ],
            },
        },
    },
    "required": ["tema_uroka", "razdel", "celi_obucheniya", "celi_uroka", "hod_uroka"],
}

def _build_response_schema(include_razdatochnye_materialy: bool) -> dict:
    """Р3.1: базовая схема без изменений (include_razdatochnye_materialy=False —
    подавляющее большинство запросов), либо её копия с добавленными
    необязательными полями карточек. Копия, а не мутация KSP_RESPONSE_SCHEMA —
    модуль-константу нельзя менять под конкретный вызов, её видят все."""
    if not include_razdatochnye_materialy:
        return KSP_RESPONSE_SCHEMA
    schema = json.loads(json.dumps(KSP_RESPONSE_SCHEMA))
    schema["properties"].update(RAZDATOCHNYE_MATERIALY_RESPONSE_PROPERTIES)
    return schema


_REQUIRED_TOP_LEVEL_KEYS = ["tema_uroka", "razdel", "celi_obucheniya", "celi_uroka", "hod_uroka"]
_REQUIRED_HOD_UROKA_KEYS = [
    "etap",
    "vremya",
    "deystviya_pedagoga",
    "deystviya_uchenika",
    "resursy",
    "ocenivanie",
]

_MAX_TIMING_DEVIATION_MINUTES = 5

# Р2.3: признаки заглушки вместо реального содержания. Пороги не строгие
# критерии стиля (это не измерить формулой), а грубый фильтр самых
# очевидных заглушек — "Объясняет тему" вместо реплики, "Опрос" вместо
# дескрипторов. Не идеально, но дешёво и ловит худшие случаи без LLM-судьи.
_MIN_DEYSTVIYA_PEDAGOGA_CHARS = 120


class KSPGenerationError(Exception):
    """Базовое исключение генератора КСП."""


class KSPValidationError(KSPGenerationError):
    """Ответ модели дважды подряд не прошёл валидацию (Б6.2)."""


# =====================================================================
# Б6.1 — сборка промпта
# =====================================================================


def _fetch_objective_description(objective_code: str, db_path=None) -> str | None:
    rows = query(
        "SELECT description FROM curriculum_objectives WHERE code = ?",
        (objective_code,),
        db_path=db_path,
    )
    return rows[0]["description"] if rows else None


def _render_style_context(style_profile: dict | None) -> str:
    """КОНТЕКСТ подставляется только если профиль реально есть и в нём
    хоть что-то заполнено — иначе штатный режим без стилизации, а не
    блок с пустыми списками (Б6.1: "нет профиля — не ошибка")."""
    if not style_profile:
        return ""

    goal_phrasing = style_profile.get("goal_phrasing") or []
    stage_structure = style_profile.get("stage_structure") or []
    assessment_methods = style_profile.get("assessment_methods") or []
    resources_used = style_profile.get("resources_used") or []

    if not any([goal_phrasing, stage_structure, assessment_methods, resources_used]):
        return ""

    stage_lines = [
        f"{s.get('stage', '')} ({s.get('timing', '')})".strip() for s in stage_structure
    ]

    return "\n".join(
        [
            CONTEXT_HEADER,
            f"{CONTEXT_GOAL_PHRASING_LABEL} {'; '.join(goal_phrasing) or '—'}",
            f"{CONTEXT_STAGE_STRUCTURE_LABEL} {'; '.join(stage_lines) or '—'}",
            f"{CONTEXT_ASSESSMENT_METHODS_LABEL} {', '.join(assessment_methods) or '—'}",
            f"{CONTEXT_RESOURCES_USED_LABEL} {', '.join(resources_used) or '—'}",
        ]
    )


def _render_task_section(
    topic: str,
    razdel: str,
    objective_code: str | None,
    objective_description: str | None,
    klass: str,
    duration_minutes: int,
    extra_columns: list[str] | None = None,
) -> str:
    if objective_code and objective_description:
        objective_line = f"{objective_code} — {objective_description}"
    elif objective_code:
        objective_line = f"{objective_code} (описание не найдено в базе целей обучения)"
    else:
        objective_line = "не указан"

    lines = [
        TASK_HEADER,
        f"{TASK_TOPIC_LABEL} {topic}",
        f"{TASK_RAZDEL_LABEL} {razdel}",
        f"{TASK_OBJECTIVE_LABEL} {objective_line}",
        f"{TASK_KLASS_LABEL} {klass}",
        f"{TASK_DURATION_LABEL} {duration_minutes} мин",
    ]

    known_extra = [c for c in (extra_columns or []) if c in EXTRA_COLUMN_LABELS]
    if known_extra:
        described = ", ".join(f"{c} ({EXTRA_COLUMN_LABELS[c]})" for c in known_extra)
        lines.append(f"{TASK_EXTRA_COLUMNS_LABEL} {described}")

    return "\n".join(lines)


def _extra_columns_of_template(template: dict | None) -> list[str]:
    """Колонки "Хода урока" выбранного шаблона, которых нет в официальной
    форме. Нужны, чтобы промпт попросил модель их заполнить: без этого
    шаблон "Развёрнутый образец" давал две гарантированно пустые колонки
    из семи в каждом сгенерированном документе."""
    if not template:
        return []
    structure = template.get("structure_json")
    if isinstance(structure, str):
        structure = json.loads(structure)
    if not isinstance(structure, dict):
        return []

    for block in structure.get("blocks", []):
        if block.get("key") == "hod_uroka":
            return [c for c in (block.get("columns") or []) if c in EXTRA_COLUMN_LABELS]
    return []


def build_prompt(
    topic: str,
    razdel: str,
    objective_code: str | None,
    klass: str,
    duration_minutes: int,
    style_profile: dict | None = None,
    db_path=None,
    extra_columns: list[str] | None = None,
    include_razdatochnye_materialy: bool = False,
    options: "LessonOptions | None" = None,
    textbook_text: str | None = None,
) -> str:
    """КОНТЕКСТ (если есть профиль стиля) + ЗАДАЧА — ровно те два блока
    промпта из MASTER.md, раздел 1.6. Схема ответа сюда не встраивается
    текстом — её берёт на себя core.llm_client.LLMClient.complete_json
    (параметр schema), чтобы не дублировать её в двух местах.

    include_razdatochnye_materialy — Р3.1: учитель попросил разноуровневые
    карточки-раздатки. По умолчанию False — большинство запросов их не
    просят, и лишняя инструкция в промпте не нужна.

    options — Р5.2/Р5.3: необязательные опции урока (LessonOptions).
    None или пустой LessonOptions() не меняют промпт вообще.

    textbook_text — Р6.2: текст со страницы учебника, распознанный по
    фото (core.textbook_ocr). Урок должен опираться на него, а не только
    на тему — модель прямо просят использовать его содержание.

    М4.2 (PLAN_STAGE2.md): порядок частей ниже — не произвольный, это
    компоновка под префиксный кэш провайдера (у DeepSeek он автоматический —
    совпадающий ПРЕФИКС промпта берётся из кэша дешевле обычных токенов).
    Стабильное — то, что не меняется между генерациями ОДНОГО учителя
    (профиль стиля, инструкция про раздатки, если она включена) — идёт
    строго в начало. Переменное — своё у каждого урока (тема, раздел, класс,
    длительность, доп. опции, текст учебника) — строго в конец, одним
    хвостом. Это ТОЛЬКО перестановка порядка — ни один текст ниже не
    изменён, смысл промпта для модели тот же (ловушка плана: менять смысл
    ради кэша нельзя, расход и так копеечный)."""
    objective_description = (
        _fetch_objective_description(objective_code, db_path=db_path) if objective_code else None
    )

    # --- стабильная часть (префикс) ---
    parts = []
    context = _render_style_context(style_profile)
    if context:
        parts.append(context)
    if include_razdatochnye_materialy:
        parts.append(TASK_RAZDATOCHNYE_MATERIALY_INSTRUCTION)

    # --- переменная часть (суффикс) ---
    parts.append(
        _render_task_section(
            topic,
            razdel,
            objective_code,
            objective_description,
            klass,
            duration_minutes,
            extra_columns=extra_columns,
        )
    )
    options_text = _render_lesson_options(options)
    if options_text:
        parts.append(options_text)
    if textbook_text and textbook_text.strip():
        parts.append(f"{TASK_TEXTBOOK_HEADER}\n{textbook_text.strip()}")

    return "\n\n".join(parts)


def _build_repair_prompt(original_prompt: str, problems: list[str]) -> str:
    problems_text = "\n".join(f"- {p}" for p in problems)
    return f"{original_prompt}\n\n{REPAIR_HEADER}\n{problems_text}\n\n{REPAIR_INSTRUCTION}"


# =====================================================================
# Б6.2 — валидация ответа
# =====================================================================

_TIME_RANGE_RE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)")
_SINGLE_NUMBER_RE = re.compile(r"(\d+)")


def _extract_minutes(vremya_text: str) -> int | None:
    """Длительность этапа из текста вроде "0-5 мин" (диапазон — берём
    разницу) или "10 мин" (одно число — берём как есть). Не смогли
    распарсить — None: проверка суммы таймингов тогда пропускается,
    а не ломает генерацию из-за формата строки."""
    if not vremya_text:
        return None
    range_match = _TIME_RANGE_RE.search(vremya_text)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        return max(0, end - start)
    single_match = _SINGLE_NUMBER_RE.search(vremya_text)
    if single_match:
        return int(single_match.group(1))
    return None


def _validate_ksp_content(content: dict, duration_minutes: int) -> list[str]:
    """Возвращает список найденных проблем (пустой = валидно). Сама
    ничего не бросает и не чинит — решение (повтор/ошибка) принимает
    вызывающий код. Ловушка Б6.2: недостающие поля здесь НЕ дописываются
    заглушками — это только диагностика."""
    problems: list[str] = []

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in content:
            problems.append(f"отсутствует обязательное поле '{key}'")
    if problems:
        return problems  # без обязательных ключей дальнейшие проверки бессмысленны

    if not isinstance(content["hod_uroka"], list) or not content["hod_uroka"]:
        problems.append("'hod_uroka' пустой или не список")

    for field in ("tema_uroka", "razdel", "celi_obucheniya"):
        value = content.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"поле '{field}' пустое")

    celi_uroka = content.get("celi_uroka") or []
    if not any(str(item).strip() for item in celi_uroka):
        problems.append("'celi_uroka' пустой список")

    total_minutes = 0
    timing_unparseable = False
    for i, entry in enumerate(content.get("hod_uroka") or []):
        if not isinstance(entry, dict):
            problems.append(f"hod_uroka[{i}] не объект")
            continue
        for key in _REQUIRED_HOD_UROKA_KEYS:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"hod_uroka[{i}].{key} пустое")

        # Р2.3: заглушка вместо реплики педагога или вместо дескрипторов
        # оценивания — не пустое поле (проверено выше), а слишком бедное
        # содержание. Проверяем только непустые значения — пустые уже
        # получили свою проблему строкой выше, дублировать незачем.
        deystviya_pedagoga = entry.get("deystviya_pedagoga")
        if isinstance(deystviya_pedagoga, str) and deystviya_pedagoga.strip():
            length = len(deystviya_pedagoga.strip())
            if length < _MIN_DEYSTVIYA_PEDAGOGA_CHARS:
                problems.append(
                    f"hod_uroka[{i}].deystviya_pedagoga слишком короткое "
                    f"({length} симв.) — похоже на название действия, а не "
                    f"на реплику педагога прямой речью (нужно от "
                    f"{_MIN_DEYSTVIYA_PEDAGOGA_CHARS} симв.)"
                )

        ocenivanie = entry.get("ocenivanie")
        if isinstance(ocenivanie, str) and ocenivanie.strip():
            if len(ocenivanie.strip().split()) <= 1:
                problems.append(
                    f"hod_uroka[{i}].ocenivanie состоит из одного слова "
                    f"({ocenivanie.strip()!r}) — нужны проверяемые дескрипторы, "
                    "а не название метода оценивания"
                )

        minutes = _extract_minutes(entry.get("vremya", ""))
        if minutes is None:
            timing_unparseable = True
        else:
            total_minutes += minutes

    if not timing_unparseable and content.get("hod_uroka"):
        deviation = abs(total_minutes - duration_minutes)
        if deviation > _MAX_TIMING_DEVIATION_MINUTES:
            problems.append(
                f"сумма таймингов этапов ({total_minutes} мин) не укладывается в "
                f"продолжительность урока {duration_minutes} мин "
                f"(допуск ±{_MAX_TIMING_DEVIATION_MINUTES} мин)"
            )

    return problems


# =====================================================================
# Б6.3 — автоподстановка кода цели (F8, Should)
# =====================================================================


def guess_objective_code(teacher_id: int, topic: str, db_path=None) -> str | None:
    """Поиск кода цели обучения по теме урока среди ktp_entries ЭТОГО
    учителя: сначала точное совпадение темы (без учёта регистра и
    лишних пробелов), потом — тема встречается подстрокой в topic
    записи КТП. Никаких эмбеддингов и семантического поиска — это
    этап 3 (MASTER.md, раздел 3.2), здесь только Should-удобство.

    Сравнение сделано в Python, а не через SQL LIKE: LIKE в SQLite
    регистронезависим только для ASCII-букв, для кириллицы — нет
    (проверено отдельно на реальном примере из curriculum_seed.sql:
    'Закон' и 'закона' LIKE не считает совпадением). Раз тема урока
    почти всегда кириллица, буквальный LIKE здесь дал бы случайные
    промахи по регистру — то есть был бы Should, который иногда молча
    не работает без видимой причины."""
    rows = query(
        "SELECT topic, objective_code FROM ktp_entries "
        "WHERE teacher_id = ? AND objective_code IS NOT NULL",
        (teacher_id,),
        db_path=db_path,
    )
    if not rows:
        return None

    topic_normalized = " ".join(topic.strip().lower().split())
    if not topic_normalized:
        return None

    normalized_rows = [
        (" ".join((row["topic"] or "").strip().lower().split()), row["objective_code"]) for row in rows
    ]

    for entry_topic, code in normalized_rows:
        if entry_topic == topic_normalized:
            return code

    for entry_topic, code in normalized_rows:
        if topic_normalized and topic_normalized in entry_topic:
            return code

    return None


# =====================================================================
# Основной конвейер: LLM-генерация (Б6.1+Б6.2) и сохранение (Б6.4)
# =====================================================================


def _fetch_style_profile(teacher_id: int, db_path=None) -> dict | None:
    rows = query("SELECT * FROM style_profiles WHERE teacher_id = ?", (teacher_id,), db_path=db_path)
    if not rows:
        return None
    row = rows[0]

    def _load(field: str) -> list:
        raw = row[field]
        return json.loads(raw) if raw else []

    return {
        "goal_phrasing": _load("goal_phrasing"),
        "stage_structure": _load("stage_structure"),
        "assessment_methods": _load("assessment_methods"),
        "resources_used": _load("resources_used"),
        "raw_samples_count": row["raw_samples_count"],
    }


async def generate_ksp(
    teacher_id: int,
    topic: str,
    razdel: str,
    objective_code: str | None,
    klass: str,
    duration_minutes: int,
    llm_client: LLMClient | None = None,
    db_path=None,
    extra_columns: list[str] | None = None,
    include_razdatochnye_materialy: bool = False,
    options: "LessonOptions | None" = None,
    textbook_text: str | None = None,
) -> dict:
    """Генерирует и валидирует JSON-содержимое КСП (без сборки .docx —
    это отдельно, save_generated_ksp). Профиль стиля учителя (если
    есть) подставляется в промпт автоматически; нет профиля — штатный
    режим без стилизации, а не ошибка (Б6.1).

    При невалидном ответе — ровно один повторный запрос с указанием
    конкретных проблем; если и он не проходит — KSPValidationError.
    Недостающие поля никогда не дописываются заглушками (Б6.2).

    include_razdatochnye_materialy — Р3.1, options — Р5.2/Р5.3,
    textbook_text — Р6.2, см. build_prompt."""
    style_profile = _fetch_style_profile(teacher_id, db_path=db_path)
    prompt = build_prompt(
        topic,
        razdel,
        objective_code,
        klass,
        duration_minutes,
        style_profile,
        db_path=db_path,
        extra_columns=extra_columns,
        include_razdatochnye_materialy=include_razdatochnye_materialy,
        options=options,
        textbook_text=textbook_text,
    )
    schema = _build_response_schema(include_razdatochnye_materialy)

    client = llm_client or LLMClient()
    owns_client = llm_client is None
    try:
        content = await client.complete_json(system=SYSTEM_PROMPT, user=prompt, schema=schema)
        problems = _validate_ksp_content(content, duration_minutes)

        if problems:
            repair_prompt = _build_repair_prompt(prompt, problems)
            content = await client.complete_json(
                system=SYSTEM_PROMPT, user=repair_prompt, schema=schema
            )
            problems = _validate_ksp_content(content, duration_minutes)
            if problems:
                raise KSPValidationError(
                    "ответ модели дважды не прошёл валидацию: " + "; ".join(problems)
                )
    finally:
        if owns_client:
            await client.aclose()

    return content


def _fill_header_fields(
    content: dict,
    teacher_id: int,
    klass: str,
    generated_at: date,
    db_path=None,
    options: "LessonOptions | None" = None,
) -> dict:
    """Дописывает в content поля шапки формы №130, которые LLM не
    возвращает и вернуть не может: их неоткуда взять из темы урока, они
    известны самой системе.

    fio_pedagoga — из teachers.name (учитель назвал его в /teacher),
    organizaciya — из teachers.school (Р9, необязательное поле профиля —
    может быть NULL, тогда шапка остаётся с прочерком, как и раньше),
    klass — из аргумента (учитель назвал его в /generate),
    data — дата генерации.

    Это НЕ заглушки в смысле ловушки Б6.2: там запрещено выдумывать
    содержательные поля КСП вместо модели. Здесь наоборот — реальные
    известные данные, без которых обязательные поля приказа №130
    остаются пустыми (шапка шаблона объявляет 7 полей, схема ответа
    модели покрывает только razdel).

    Уже заполненные значения не перетираются: если content почему-то
    пришёл с этими полями, приоритет у него.

    options (Р5.1/Р5.3) — cennost_integracii/predvaritelnye_znaniya/
    page_orientation/ima_oop здесь же: их значения система знает сама
    (учитель выбрал ценность из справочника, вписал текст, выбрал
    ориентацию) — модель их не генерирует и не должна, это не то же
    самое, что "недостающие данные" из ловушки Б6.2 выше.
    """
    filled = dict(content)

    if not filled.get("klass") and klass:
        filled["klass"] = klass

    if not filled.get("data"):
        # generated_at может прийти строкой — build_filename такое тоже
        # допускает ("для тестов с фиксированной датой"), не расходимся.
        filled["data"] = (
            generated_at.strftime("%d.%m.%Y")
            if hasattr(generated_at, "strftime")
            else str(generated_at)
        )

    if not filled.get("fio_pedagoga") or not filled.get("organizaciya"):
        rows = query("SELECT name, school FROM teachers WHERE id = ?", (teacher_id,), db_path=db_path)
        row = rows[0] if rows else None
        if row and not filled.get("fio_pedagoga") and row["name"]:
            filled["fio_pedagoga"] = row["name"]
        if row and not filled.get("organizaciya") and row["school"]:
            filled["organizaciya"] = row["school"]

    if options is not None:
        if options.cennost_key:
            value = get_value(options.cennost_key)
            if value:
                filled["cennost_integracii"] = value["name"]
        if options.predvaritelnye_znaniya:
            filled["predvaritelnye_znaniya"] = options.predvaritelnye_znaniya
        if options.page_orientation == "album":
            filled["page_orientation"] = "album"
        if options.ima_oop:
            filled["ima_oop"] = True

    return filled


def save_generated_ksp(
    teacher_id: int,
    template_id: int,
    content: dict,
    template: dict,
    subject: str,
    klass: str,
    ktp_entry_id: int | None = None,
    generated_at: date | None = None,
    db_path=None,
    output_dir: Path | str | None = None,
    options: "LessonOptions | None" = None,
) -> dict:
    """Б6.4: строит .docx (core.docx_builder, блок Б5) в output_dir
    (по умолчанию settings.generated_dir) и сохраняет строку в
    generated_ksp. Путь в базе — тот же объект Path, что реально был
    сохранён на диск (не пересобирается заново), поэтому расхождения
    между базой и файловой системой здесь в принципе невозможны.
    output_dir — параметр ради тестируемости (как db_path у core.db):
    тесты не должны писать в реальную storage/generated/ проекта.

    Перед сборкой .docx content дополняется полями шапки, которых нет в
    ответе модели (_fill_header_fields) — иначе ФИО педагога, дата и
    класс в готовом документе остаются пустыми. В content_json пишется
    тот же дополненный словарь, что ушёл в файл, чтобы предпросмотр в
    Mini App показывал ровно то же, что лежит в .docx.

    options — Р5.1/Р5.3, см. _fill_header_fields."""
    generated_at = generated_at or datetime.now().date()
    content = _fill_header_fields(content, teacher_id, klass, generated_at, db_path=db_path, options=options)

    filename = build_filename(subject, klass, content.get("tema_uroka", ""), generated_at)
    out_path = Path(output_dir or settings.generated_dir) / filename

    saved_path = build_docx(content, template, out_path)

    new_id = str(uuid.uuid4())
    execute(
        "INSERT INTO generated_ksp "
        "(id, teacher_id, ktp_entry_id, template_id, content_json, docx_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            new_id,
            teacher_id,
            ktp_entry_id,
            template_id,
            json.dumps(content, ensure_ascii=False),
            str(saved_path),
        ),
        db_path=db_path,
    )

    return {
        "id": new_id,
        "teacher_id": teacher_id,
        "ktp_entry_id": ktp_entry_id,
        "template_id": template_id,
        "content_json": content,
        "docx_path": str(saved_path),
    }


async def generate_and_save_ksp(
    teacher_id: int,
    template_id: int,
    topic: str,
    razdel: str,
    subject: str,
    klass: str,
    duration_minutes: int,
    objective_code: str | None = None,
    ktp_entry_id: int | None = None,
    llm_client: LLMClient | None = None,
    db_path=None,
    output_dir: Path | str | None = None,
    include_razdatochnye_materialy: bool = False,
    options: "LessonOptions | None" = None,
    textbook_text: str | None = None,
) -> dict:
    """Полный конвейер: промпт -> LLM -> валидация -> .docx -> запись в
    generated_ksp. Удобный вызов для bot/handlers.py (блок Б8); тесты
    и другой код могут пользоваться generate_ksp/save_generated_ksp
    по отдельности.

    include_razdatochnye_materialy — Р3.1, options — Р5.1/Р5.2/Р5.3,
    textbook_text — Р6.2, см. build_prompt / _fill_header_fields."""
    template = get_template(template_id, db_path=db_path)
    if template is None:
        raise KSPGenerationError(f"шаблон с id={template_id} не найден")

    content = await generate_ksp(
        teacher_id,
        topic,
        razdel,
        objective_code,
        klass,
        duration_minutes,
        llm_client=llm_client,
        db_path=db_path,
        extra_columns=_extra_columns_of_template(template),
        include_razdatochnye_materialy=include_razdatochnye_materialy,
        options=options,
        textbook_text=textbook_text,
    )

    return save_generated_ksp(
        teacher_id,
        template_id,
        content,
        template,
        subject,
        klass,
        output_dir=output_dir,
        ktp_entry_id=ktp_entry_id,
        options=options,
        db_path=db_path,
    )
