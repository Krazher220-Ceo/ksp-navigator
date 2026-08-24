-- storage/schema.sql — схема базы данных, этап 1.
--
-- Имена таблиц и полей — по MASTER.md, раздел 1.3, БЕЗ ИЗМЕНЕНИЙ.
-- Переименование любого поля здесь ломает все следующие блоки (Б2–Б13),
-- которые обращаются к этим таблицам по именам.
--
-- Применяется идемпотентно: CREATE TABLE/INDEX IF NOT EXISTS — повторный
-- запуск (sqlite3 storage/app.db < storage/schema.sql) безопасен.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    subject TEXT,
    telegram_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS style_profiles (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    -- извлечённые паттерны в JSON
    goal_phrasing TEXT,        -- как формулирует цели урока
    stage_structure TEXT,      -- типичные этапы и их тайминг
    assessment_methods TEXT,   -- любимые методы оценивания
    resources_used TEXT,       -- какие ресурсы обычно указывает
    raw_samples_count INTEGER,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    source TEXT,               -- откуда взят: приказ №130 / интернет / загружен
    is_official INTEGER DEFAULT 0,   -- соответствует приказу №130
    is_builtin INTEGER DEFAULT 0,    -- встроенный или загружен пользователем
    uploaded_by INTEGER REFERENCES teachers(id),  -- NULL для встроенных
    structure_json TEXT NOT NULL,    -- описание блоков и колонок
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS curriculum_objectives (
    code TEXT PRIMARY KEY,     -- 10.1.1.1
    grade INTEGER,
    section TEXT,
    subsection TEXT,
    description TEXT,
    thinking_level TEXT        -- знание/применение/высокий порядок
);

CREATE TABLE IF NOT EXISTS ktp_entries (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    lesson_number INTEGER,
    section TEXT,
    topic TEXT,
    objective_code TEXT REFERENCES curriculum_objectives(code),
    hours INTEGER,
    planned_date TEXT,
    quarter INTEGER
);

CREATE TABLE IF NOT EXISTS generated_ksp (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    template_id INTEGER REFERENCES templates(id),
    content_json TEXT,
    docx_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    -- 'generate_ktp' добавлен блоком Р4.3 (PLAN_STAGE1_EXT.md). Для базы,
    -- созданной ДО этого блока, одного перезапуска schema.sql недостаточно —
    -- CREATE TABLE IF NOT EXISTS не трогает уже существующую таблицу с
    -- другим CHECK. См. scripts/migrate_add_generate_ktp_task_type.py.
    type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp')),
    status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',
    payload TEXT,
    result TEXT,
    error TEXT,
    telegram_chat_id INTEGER,
    retries INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- Индексы сверх документа (PLAN_STAGE1.md, задача Б1.1)
CREATE INDEX IF NOT EXISTS idx_ktp_teacher ON ktp_entries(teacher_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_tg ON teachers(telegram_user_id);
