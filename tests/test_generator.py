"""
tests/test_generator.py — тесты core/ksp_generator.py.

LLM мокается через _ScriptedLLMClient (свой, простой fake, а не
httpx.MockTransport — здесь важна логика generate_ksp, не транспорт;
транспорт уже покрыт tests/test_llm_client.py). Реальные вызовы к
провайдерам в тестах запрещены.

guess_objective_code проверяется на настоящем curriculum_seed.sql —
том самом, что попадёт в реальную базу через scripts/init_db.py.
"""

import json
from pathlib import Path

import pytest
from docx import Document

from core.db import execute, init_db, query
from core.ksp_generator import (
    KSPGenerationError,
    KSPValidationError,
    _extract_minutes,
    _validate_ksp_content,
    build_prompt,
    generate_and_save_ksp,
    generate_ksp,
    guess_objective_code,
    save_generated_ksp,
)
from core.templates import get_template, load_builtin_templates

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"
REAL_SEED_PATH = PROJECT_ROOT / "curriculum_seed.sql"

DURATION = 40

VALID_CONTENT = {
    "tema_uroka": "Закон сохранения импульса",
    "razdel": "Механика",
    "celi_obucheniya": "10.1.4.1 применять законы сохранения при решении задач",
    "celi_uroka": ["Все учащиеся смогут сформулировать закон сохранения импульса"],
    "hod_uroka": [
        {
            "etap": "Начало урока",
            "vremya": "0-5",
            "deystviya_pedagoga": "Объясняет тему",
            "deystviya_uchenika": "Слушают",
            "resursy": "Учебник",
            "ocenivanie": "Устный опрос",
        },
        {
            "etap": "Основная часть",
            "vremya": "5-35",
            "deystviya_pedagoga": "Ведёт урок, разбирает задачи",
            "deystviya_uchenika": "Решают задачи в парах",
            "resursy": "Доска, калькулятор",
            "ocenivanie": "Взаимооценивание",
        },
        {
            "etap": "Итог урока",
            "vremya": "35-40",
            "deystviya_pedagoga": "Подводит итоги",
            "deystviya_uchenika": "Формулируют вывод",
            "resursy": "Дневник",
            "ocenivanie": "Самооценивание",
        },
    ],
}


class _ScriptedLLMClient:
    """Отдаёт заранее заданные ответы по очереди, запоминает все вызовы
    (system/user/schema) — для проверки, что промпт на повторе реально
    содержит причину провала, а не просто дублирует исходный запрос."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete_json(self, system, user, schema, max_retries=3):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise AssertionError("LLM вызван больше раз, чем задано сценарием теста")
        return self.responses.pop(0)


# --- Б6.1: build_prompt ---


def test_build_prompt_contains_all_required_pieces():
    prompt = build_prompt(
        topic="Закон сохранения импульса",
        razdel="Механика",
        objective_code="10.1.4.1",
        klass="10А",
        duration_minutes=DURATION,
    )
    assert "Закон сохранения импульса" in prompt
    assert "Механика" in prompt
    assert "10.1.4.1" in prompt
    assert "10А" in prompt
    assert f"{DURATION} мин" in prompt


def test_build_prompt_fetches_objective_description_from_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', "
        "'применять законы сохранения при решении задач', 'применение')",
        db_path=db_path,
    )

    prompt = build_prompt(
        "Тема", "Раздел", "10.1.4.1", "10А", DURATION, db_path=db_path
    )
    assert "применять законы сохранения при решении задач" in prompt


def test_build_prompt_without_objective_code_says_not_specified():
    prompt = build_prompt("Тема", "Раздел", None, "10А", DURATION)
    assert "не указан" in prompt


def test_build_prompt_includes_context_only_when_style_profile_present():
    without_profile = build_prompt("Тема", "Раздел", None, "10А", DURATION, style_profile=None)
    assert "КОНТЕКСТ" not in without_profile

    profile = {
        "goal_phrasing": ["Все учащиеся смогут..."],
        "stage_structure": [{"stage": "Начало", "timing": "5 мин"}],
        "assessment_methods": ["Устный опрос"],
        "resources_used": ["Учебник"],
    }
    with_profile = build_prompt("Тема", "Раздел", None, "10А", DURATION, style_profile=profile)
    assert "КОНТЕКСТ" in with_profile
    assert "Все учащиеся смогут..." in with_profile
    assert "Устный опрос" in with_profile


def test_build_prompt_empty_style_profile_is_treated_as_no_profile():
    """Свежесозданный учитель без загруженных КСП — профиль существует
    как объект, но все списки пустые. Это тоже штатный режим без
    стилизации, не КОНТЕКСТ с прочерками."""
    empty_profile = {
        "goal_phrasing": [],
        "stage_structure": [],
        "assessment_methods": [],
        "resources_used": [],
    }
    prompt = build_prompt("Тема", "Раздел", None, "10А", DURATION, style_profile=empty_profile)
    assert "КОНТЕКСТ" not in prompt


# --- Б6.2: валидация ---


def test_validate_accepts_correct_content():
    assert _validate_ksp_content(VALID_CONTENT, DURATION) == []


def test_validate_reports_missing_hod_uroka():
    content = {k: v for k, v in VALID_CONTENT.items() if k != "hod_uroka"}
    problems = _validate_ksp_content(content, DURATION)
    assert any("hod_uroka" in p for p in problems)


def test_validate_reports_empty_hod_uroka_list():
    content = dict(VALID_CONTENT)
    content["hod_uroka"] = []
    problems = _validate_ksp_content(content, DURATION)
    assert any("hod_uroka" in p for p in problems)


def test_validate_reports_empty_string_field():
    content = dict(VALID_CONTENT)
    content["tema_uroka"] = ""
    problems = _validate_ksp_content(content, DURATION)
    assert any("tema_uroka" in p for p in problems)


def test_validate_reports_empty_field_inside_hod_uroka_entry():
    content = json.loads(json.dumps(VALID_CONTENT))  # глубокая копия
    content["hod_uroka"][0]["deystviya_pedagoga"] = "   "
    problems = _validate_ksp_content(content, DURATION)
    assert any("deystviya_pedagoga" in p for p in problems)


def test_validate_reports_timing_far_off_duration():
    content = json.loads(json.dumps(VALID_CONTENT))
    content["hod_uroka"] = [
        {**content["hod_uroka"][0], "vremya": "0-5"}
    ]  # 5 минут вместо 40
    problems = _validate_ksp_content(content, DURATION)
    assert any("таймингов" in p for p in problems)


def test_validate_tolerates_5_minute_deviation():
    content = json.loads(json.dumps(VALID_CONTENT))
    content["hod_uroka"][-1]["vremya"] = "35-44"  # сумма 44 вместо 40, но в пределах ±5
    assert _validate_ksp_content(content, DURATION) == []


def test_validate_skips_timing_check_when_unparseable():
    content = json.loads(json.dumps(VALID_CONTENT))
    for stage in content["hod_uroka"]:
        stage["vremya"] = "в начале урока"  # без цифр вообще
    problems = _validate_ksp_content(content, DURATION)
    assert not any("таймингов" in p for p in problems)


def test_validate_does_not_mutate_or_patch_content():
    """Ловушка Б6.2: валидация только диагностирует, не подставляет
    заглушки вместо недостающих данных."""
    original = {k: v for k, v in VALID_CONTENT.items() if k != "hod_uroka"}
    before = json.loads(json.dumps(original))
    _validate_ksp_content(original, DURATION)
    assert original == before  # ничего не дописано и не изменено


def test_extract_minutes_range():
    assert _extract_minutes("0-5 мин") == 5
    assert _extract_minutes("5–35") == 30
    assert _extract_minutes("10 мин") == 10
    assert _extract_minutes("без цифр") is None
    assert _extract_minutes("") is None


# --- Б6.2: generate_ksp — сценарии из КГ ---


async def test_generate_ksp_valid_response_passes_without_repair():
    fake = _ScriptedLLMClient([VALID_CONTENT])
    result = await generate_ksp(
        teacher_id=1,
        topic="Закон сохранения импульса",
        razdel="Механика",
        objective_code=None,
        klass="10А",
        duration_minutes=DURATION,
        llm_client=fake,
    )
    assert result == VALID_CONTENT
    assert len(fake.calls) == 1


async def test_generate_ksp_missing_hod_uroka_triggers_one_repair_then_succeeds():
    broken = {k: v for k, v in VALID_CONTENT.items() if k != "hod_uroka"}
    fake = _ScriptedLLMClient([broken, VALID_CONTENT])

    result = await generate_ksp(
        teacher_id=1,
        topic="Тема",
        razdel="Раздел",
        objective_code=None,
        klass="10А",
        duration_minutes=DURATION,
        llm_client=fake,
    )

    assert result == VALID_CONTENT
    assert len(fake.calls) == 2
    assert "hod_uroka" in fake.calls[1]["user"]  # причина провала попала в повторный промпт


async def test_generate_ksp_second_failure_raises_validation_error():
    broken = {k: v for k, v in VALID_CONTENT.items() if k != "hod_uroka"}
    fake = _ScriptedLLMClient([broken, broken])

    with pytest.raises(KSPValidationError):
        await generate_ksp(
            teacher_id=1,
            topic="Тема",
            razdel="Раздел",
            objective_code=None,
            klass="10А",
            duration_minutes=DURATION,
            llm_client=fake,
        )
    assert len(fake.calls) == 2  # ровно один повтор, не больше


async def test_generate_ksp_uses_teacher_style_profile_when_present(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=db_path)
    execute(
        "INSERT INTO style_profiles (teacher_id, goal_phrasing, stage_structure, "
        "assessment_methods, resources_used, raw_samples_count) VALUES "
        "(1, ?, ?, ?, ?, 3)",
        (
            json.dumps(["Все учащиеся смогут применить закон"]),
            json.dumps([{"stage": "Начало", "timing": "5 мин"}]),
            json.dumps(["Взаимооценивание"]),
            json.dumps(["Учебник"]),
        ),
        db_path=db_path,
    )

    fake = _ScriptedLLMClient([VALID_CONTENT])
    await generate_ksp(
        teacher_id=1,
        topic="Тема",
        razdel="Раздел",
        objective_code=None,
        klass="10А",
        duration_minutes=DURATION,
        llm_client=fake,
        db_path=db_path,
    )

    assert "КОНТЕКСТ" in fake.calls[0]["user"]
    assert "Все учащиеся смогут применить закон" in fake.calls[0]["user"]


async def test_generate_ksp_without_style_profile_is_not_an_error(tmp_path):
    """Штатный режим: учитель без профиля стиля (ещё не загрузил КСП) —
    генерация всё равно проходит, просто без КОНТЕКСТ-раздела."""
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=db_path)

    fake = _ScriptedLLMClient([VALID_CONTENT])
    result = await generate_ksp(
        teacher_id=1,
        topic="Тема",
        razdel="Раздел",
        objective_code=None,
        klass="10А",
        duration_minutes=DURATION,
        llm_client=fake,
        db_path=db_path,
    )
    assert result == VALID_CONTENT
    assert "КОНТЕКСТ" not in fake.calls[0]["user"]


# --- Б6.3: guess_objective_code на настоящем curriculum_seed.sql ---


@pytest.fixture
def db_with_real_seed(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute(
        "INSERT INTO teachers (id, name, subject) VALUES (1, 'Дмитрий Александрович', 'физика')",
        db_path=db_path,
    )
    from core.db import connect

    conn = connect(db_path)
    try:
        conn.executescript(REAL_SEED_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_guess_objective_code_exact_topic_from_real_seed(db_with_real_seed):
    code = guess_objective_code(teacher_id=1, topic="Закон сохранения импульса", db_path=db_with_real_seed)
    assert code == "10.1.4.1"


def test_guess_objective_code_is_case_insensitive_for_cyrillic(db_with_real_seed):
    """Ловушка: SQL LIKE в SQLite регистронезависим только для ASCII.
    guess_objective_code сравнивает в Python именно поэтому."""
    code = guess_objective_code(teacher_id=1, topic="закон сохранения импульса", db_path=db_with_real_seed)
    assert code == "10.1.4.1"


def test_guess_objective_code_made_up_topic_returns_none(db_with_real_seed):
    code = guess_objective_code(
        teacher_id=1, topic="Квантовая хромодинамика в 10 классе", db_path=db_with_real_seed
    )
    assert code is None


def test_guess_objective_code_empty_topic_returns_none(db_with_real_seed):
    assert guess_objective_code(teacher_id=1, topic="   ", db_path=db_with_real_seed) is None


def test_guess_objective_code_unknown_teacher_returns_none(db_with_real_seed):
    code = guess_objective_code(teacher_id=999, topic="Закон сохранения импульса", db_path=db_with_real_seed)
    assert code is None


# --- Б6.4: save_generated_ksp / generate_and_save_ksp ---


@pytest.fixture
def db_with_official_template(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=db_path)
    load_builtin_templates(db_path=db_path)
    official_id = query(
        "SELECT id FROM templates WHERE is_official = 1", db_path=db_path
    )[0]["id"]
    return db_path, official_id


def test_save_generated_ksp_writes_file_and_matching_db_row(db_with_official_template, tmp_path):
    db_path, template_id = db_with_official_template
    template = get_template(template_id, db_path=db_path)
    output_dir = tmp_path / "generated"

    result = save_generated_ksp(
        teacher_id=1,
        template_id=template_id,
        content=VALID_CONTENT,
        template=template,
        subject="физика",
        klass="10А",
        db_path=db_path,
        output_dir=output_dir,
    )

    docx_path = Path(result["docx_path"])
    assert docx_path.exists()
    assert docx_path.parent == output_dir

    row = query("SELECT * FROM generated_ksp WHERE id = ?", (result["id"],), db_path=db_path)[0]
    assert row["docx_path"] == str(docx_path)
    assert row["teacher_id"] == 1
    assert row["template_id"] == template_id
    assert json.loads(row["content_json"]) == VALID_CONTENT

    # файл реально валиден
    document = Document(str(docx_path))
    assert len(document.tables) == 1


async def test_generate_and_save_ksp_full_pipeline(db_with_official_template, tmp_path):
    db_path, template_id = db_with_official_template
    fake = _ScriptedLLMClient([VALID_CONTENT])
    output_dir = tmp_path / "generated"

    result = await generate_and_save_ksp(
        teacher_id=1,
        template_id=template_id,
        topic="Закон сохранения импульса",
        razdel="Механика",
        subject="физика",
        klass="10А",
        duration_minutes=DURATION,
        llm_client=fake,
        db_path=db_path,
        output_dir=output_dir,
    )

    assert Path(result["docx_path"]).exists()
    rows = query("SELECT * FROM generated_ksp", db_path=db_path)
    assert len(rows) == 1


async def test_generate_and_save_ksp_unknown_template_raises(db_with_official_template):
    db_path, _template_id = db_with_official_template
    fake = _ScriptedLLMClient([VALID_CONTENT])

    with pytest.raises(KSPGenerationError):
        await generate_and_save_ksp(
            teacher_id=1,
            template_id=999999,
            topic="Тема",
            razdel="Раздел",
            subject="физика",
            klass="10А",
            duration_minutes=DURATION,
            llm_client=fake,
            db_path=db_path,
        )
