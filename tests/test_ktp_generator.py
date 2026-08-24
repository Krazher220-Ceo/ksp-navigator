"""
tests/test_ktp_generator.py — тесты core/ktp_generator.py (блок Р4.2).

LLM мокается тем же простым fake, что и в tests/test_generator.py —
логика генератора, не транспорт.
"""

import json

import pytest
from docx import Document

from core.db import execute, init_db, query
from core.ktp_builder import QUARTER_LABELS
from core.ktp_generator import (
    KTPValidationError,
    _fetch_available_objectives,
    _validate_ktp_content,
    build_prompt,
    generate_and_save_ktp,
    generate_ktp,
    save_generated_ktp,
)
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"

CHASOV_V_GOD = 68


def _empty_quarter():
    return {"uroki": []}


VALID_CONTENT = {
    "chetverti": [
        {
            "uroki": [
                {"razdel": "Механика", "tema_uroka": "Кинематика точки", "celi_obucheniya": "10.1.1.5", "chasov": 34, "primechanie": ""},
            ]
        },
        {
            "uroki": [
                {"razdel": "Механика", "tema_uroka": "Закон сохранения импульса", "celi_obucheniya": "10.1.4.1", "chasov": 34, "primechanie": ""},
            ]
        },
        _empty_quarter(),
        _empty_quarter(),
    ],
}


class _ScriptedLLMClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def complete_json(self, system, user, schema, max_retries=3):
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.responses:
            raise AssertionError("LLM вызван больше раз, чем задано сценарием теста")
        return self.responses.pop(0)


# --- Р4.2: валидация ---


def test_validate_accepts_correct_content():
    assert _validate_ktp_content(VALID_CONTENT, CHASOV_V_GOD) == []


def test_validate_reports_wrong_quarter_count():
    content = {"chetverti": VALID_CONTENT["chetverti"][:3]}
    problems = _validate_ktp_content(content, CHASOV_V_GOD)
    assert any("4" in p for p in problems)


def test_validate_reports_hours_mismatch():
    content = json.loads(json.dumps(VALID_CONTENT))
    content["chetverti"][0]["uroki"][0]["chasov"] = 1  # сумма перестаёт сходиться
    problems = _validate_ktp_content(content, CHASOV_V_GOD)
    assert any("сумма часов" in p for p in problems)


def test_validate_reports_empty_topic():
    content = json.loads(json.dumps(VALID_CONTENT))
    content["chetverti"][0]["uroki"][0]["tema_uroka"] = "   "
    problems = _validate_ktp_content(content, CHASOV_V_GOD)
    assert any("tema_uroka" in p for p in problems)


def test_validate_reports_non_positive_hours():
    content = json.loads(json.dumps(VALID_CONTENT))
    content["chetverti"][0]["uroki"][0]["chasov"] = 0
    problems = _validate_ktp_content(content, CHASOV_V_GOD)
    assert any("chasov" in p for p in problems)


def test_physics_10_full_year_sums_to_68_hours():
    """Р4.2, КГ буквально: физика 10 класса, 34 недели, 2 ч/нед, 68 ч —
    сумма часов = 68, четвертей ровно 4."""
    assert _validate_ktp_content(VALID_CONTENT, 68) == []
    assert len(VALID_CONTENT["chetverti"]) == 4


# --- Р4.2: build_prompt — не выдумывает коды целей ---


def test_prompt_offers_real_objective_codes_for_physics(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', "
        "'применять законы сохранения', 'применение')",
        db_path=db_path,
    )
    prompt = build_prompt("физика", "10А", 2, 68, db_path=db_path)
    assert "10.1.4.1" in prompt
    assert "применять законы сохранения" in prompt


def test_prompt_does_not_offer_codes_for_unknown_subject(tmp_path):
    """Ловушка Р8/Р4.2: для предмета, которого нет в curriculum_objectives
    (сейчас там только физика), промпт честно говорит модели не
    подставлять код — не выдаёт пустой список молча."""
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', 'применять законы сохранения', 'применение')",
        db_path=db_path,
    )
    prompt = build_prompt("химия", "10А", 2, 68, db_path=db_path)
    assert "10.1.4.1" not in prompt
    assert "Проверенных кодов" in prompt


def test_fetch_available_objectives_empty_for_other_subject(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute(
        "INSERT INTO curriculum_objectives (code, grade, section, subsection, description, thinking_level) "
        "VALUES ('10.1.4.1', 10, 'Механика', 'Законы сохранения', 'применять законы сохранения', 'применение')",
        db_path=db_path,
    )
    assert _fetch_available_objectives("физика", "10А", db_path=db_path) != []
    assert _fetch_available_objectives("химия", "10А", db_path=db_path) == []
    assert _fetch_available_objectives("физика", "11А", db_path=db_path) == []  # другой класс


def test_prompt_lists_given_topics_in_order():
    prompt = build_prompt("физика", "10А", 2, 68, topics=["Тема раз", "Тема два"])
    assert prompt.index("Тема раз") < prompt.index("Тема два")


def test_prompt_notes_when_no_topics_given():
    prompt = build_prompt("физика", "10А", 2, 68, topics=None)
    assert "не дан" in prompt


# --- Р4.2: generate_ktp — повтор и ошибка ---


async def test_generate_ktp_valid_response_passes_without_repair():
    fake = _ScriptedLLMClient([VALID_CONTENT])
    result = await generate_ktp("физика", "10А", 2, CHASOV_V_GOD, llm_client=fake)
    assert result == VALID_CONTENT
    assert len(fake.calls) == 1


async def test_generate_ktp_hours_mismatch_triggers_one_repair_then_succeeds():
    broken = json.loads(json.dumps(VALID_CONTENT))
    broken["chetverti"][0]["uroki"][0]["chasov"] = 1
    fake = _ScriptedLLMClient([broken, VALID_CONTENT])

    result = await generate_ktp("физика", "10А", 2, CHASOV_V_GOD, llm_client=fake)
    assert result == VALID_CONTENT
    assert len(fake.calls) == 2
    assert "сумма часов" in fake.calls[1]["user"]


async def test_generate_ktp_second_failure_raises_validation_error():
    broken = json.loads(json.dumps(VALID_CONTENT))
    broken["chetverti"][0]["uroki"][0]["chasov"] = 1
    fake = _ScriptedLLMClient([broken, broken])

    with pytest.raises(KTPValidationError):
        await generate_ktp("физика", "10А", 2, CHASOV_V_GOD, llm_client=fake)
    assert len(fake.calls) == 2


# --- Р4.2/Р4.3 связка: save_generated_ktp пишет .docx И ktp_entries ---


@pytest.fixture
def db_with_teacher(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path=db_path, schema_path=REAL_SCHEMA_PATH)
    execute("INSERT INTO teachers (id, name, subject) VALUES (1, 'Т', 'физика')", db_path=db_path)
    return db_path


def test_save_generated_ktp_writes_docx_and_ktp_entries(db_with_teacher, tmp_path):
    db_path = db_with_teacher
    output_dir = tmp_path / "generated"

    result = save_generated_ktp(
        teacher_id=1,
        predmet="физика",
        klass="10А",
        chasov_v_nedelu=2,
        chasov_v_god=CHASOV_V_GOD,
        content=VALID_CONTENT,
        db_path=db_path,
        output_dir=output_dir,
    )

    docx_path = Path(result["docx_path"])
    assert docx_path.exists()
    assert docx_path.parent == output_dir

    document = Document(str(docx_path))
    full_text = "\n".join(p.text for p in document.paragraphs)
    assert "физика" in full_text

    rows = query("SELECT topic, objective_code, hours, quarter FROM ktp_entries WHERE teacher_id = 1", db_path=db_path)
    topics = {r["topic"] for r in rows}
    assert "Кинематика точки" in topics
    assert "Закон сохранения импульса" in topics
    assert result["ktp_entries_inserted"] == 2


def test_save_generated_ktp_replaces_previous_ktp(db_with_teacher, tmp_path):
    """КТП один на год — сгенерированный заменяет прошлый, как и
    загруженный (core/ktp_parser.py, save_ktp_entries)."""
    db_path = db_with_teacher
    output_dir = tmp_path / "generated"

    save_generated_ktp(1, "физика", "10А", 2, CHASOV_V_GOD, VALID_CONTENT, db_path=db_path, output_dir=output_dir)
    result = save_generated_ktp(1, "физика", "10А", 2, CHASOV_V_GOD, VALID_CONTENT, db_path=db_path, output_dir=output_dir)

    assert result["ktp_entries_replaced"] == 2
    rows = query("SELECT id FROM ktp_entries WHERE teacher_id = 1", db_path=db_path)
    assert len(rows) == 2  # не 4 — старые заменены, не дописаны


async def test_generate_and_save_ktp_full_pipeline(db_with_teacher, tmp_path):
    db_path = db_with_teacher
    fake = _ScriptedLLMClient([VALID_CONTENT])
    output_dir = tmp_path / "generated"

    result = await generate_and_save_ktp(
        teacher_id=1, predmet="физика", klass="10А", chasov_v_nedelu=2,
        chasov_v_god=CHASOV_V_GOD, llm_client=fake, db_path=db_path, output_dir=output_dir,
    )

    assert Path(result["docx_path"]).exists()
    assert result["ktp_entries_inserted"] == 2
