"""Тесты идемпотентной миграции выбора шаблона из Mini App (И1)."""

import importlib.util
from pathlib import Path

from core.db import init_db, query

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "scripts" / "migrate_add_template_selections.py"
SCHEMA_PATH = PROJECT_ROOT / "storage" / "schema.sql"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("migrate_add_template_selections", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_creates_table_and_is_idempotent(tmp_path):
    db_path = tmp_path / "old.db"
    # Имитируем старую базу: применяем схему без новой таблицы.
    old_schema = tmp_path / "old_schema.sql"
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    start = schema_text.index("CREATE TABLE IF NOT EXISTS template_selections")
    end = schema_text.index("CREATE TABLE IF NOT EXISTS curriculum_objectives", start)
    old_schema.write_text(schema_text[:start] + schema_text[end:], encoding="utf-8")
    init_db(db_path=db_path, schema_path=old_schema)

    migration = _load_migration_module()
    assert migration.migrate(db_path) is True
    assert migration.migrate(db_path) is False
    assert query("SELECT * FROM template_selections", db_path=db_path) == []
