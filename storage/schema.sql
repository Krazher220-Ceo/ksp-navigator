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
    -- school добавлен блоком Р9 (PLAN_STAGE1_EXT.md). Для базы, созданной
    -- ДО этого блока, CREATE TABLE IF NOT EXISTS колонку не добавит —
    -- см. scripts/migrate_add_school_to_teachers.py (аддитивная миграция,
    -- ALTER TABLE ... ADD COLUMN, SQLite это умеет напрямую).
    school TEXT,
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
    -- 'generate_ktp' добавлен блоком Р4.3 (PLAN_STAGE1_EXT.md). 'transcribe'
    -- и 'generate_konspekt' — блоком К2.1 (PLAN_STAGE2.md). Для базы,
    -- созданной ДО этих блоков, одного перезапуска schema.sql недостаточно —
    -- CREATE TABLE IF NOT EXISTS не трогает уже существующую таблицу с
    -- другим CHECK. См. scripts/migrate_add_generate_ktp_task_type.py и
    -- scripts/migrate_add_transcribe_task_type.py.
    type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp','transcribe','generate_konspekt')),
    status TEXT CHECK(status IN ('pending','processing','done','failed')) DEFAULT 'pending',
    payload TEXT,
    result TEXT,
    error TEXT,
    telegram_chat_id INTEGER,
    retries INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);

-- usage_daily — дневные лимиты на аккаунт (блок М6, PLAN_STAGE2.md).
-- Ключ — telegram_user_id, НЕ teacher_id: учитель может не иметь профиля
-- (core/dashboard.py и весь остальной проект так и живут — отсутствие
-- профиля не ошибка), а telegram_user_id есть у любого сообщения всегда.
-- Отклонение от черновой формулировки блока М6 в самом плане (там в
-- одном месте написано "teacher_id") — решение в пользу ловушки М6.1,
-- которая как раз это и разбирает; см. NIGHT_REPORT_STAGE2.md.
CREATE TABLE IF NOT EXISTS usage_daily (
    telegram_user_id INTEGER NOT NULL,
    day TEXT NOT NULL,             -- ГГГГ-ММ-ДД по времени Костаная (UTC+5)
    operation TEXT NOT NULL,       -- 'generate_ksp' | 'generate_ktp' | ...
    count INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (telegram_user_id, day, operation)
);

-- transcripts/konspekty — аудио урока (блоки К2-К5, PLAN_STAGE2.md).
-- Цепочка одна: аудиозапись -> whisper.cpp -> transcripts -> konspekty ->
-- КСП. Конспект БЕЗ транскрипта (source='audio' обязателен на входе)
-- в проекте не делается — решение автора, MASTER.md 0.6 п.2.
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    source TEXT,               -- 'audio' | 'manual'
    text TEXT NOT NULL,
    duration_seconds INTEGER,
    language TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS konspekty (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    transcript_id TEXT REFERENCES transcripts(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    tema TEXT,
    content_json TEXT NOT NULL,
    docx_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transcripts_teacher ON transcripts(teacher_id);
CREATE INDEX IF NOT EXISTS idx_konspekty_teacher ON konspekty(teacher_id);

-- incidents — живучесть (блок М7, PLAN_STAGE2.md). Пишет scripts/watchdog.sh
-- напрямую через sqlite3 CLI (у watchdog нет доступа к процессу бота —
-- М7.1, ловушка 2), читает и закрывает уведомлением core/incidents.py.
-- reason: 'dns_fail' | 'tcp_fail' | 'telegram_5xx' | 'bot_process' |
-- 'worker_stuck' — коды причин watchdog различает по коду возврата curl,
-- не по тексту сообщения (М7.2, ловушка).
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    reason TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);

-- worker_heartbeat — одна строка (id=1), обновляется core.queue.QueueWorker
-- на каждом проходе цикла (блок М7.3). Отдельная таблица, не поле в
-- tasks: воркер жив даже когда задач нет вообще, и это тоже нужно видеть.
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Индексы сверх документа (PLAN_STAGE1.md, задача Б1.1)
CREATE INDEX IF NOT EXISTS idx_ktp_teacher ON ktp_entries(teacher_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_incidents_unresolved ON incidents(ended_at, notified);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_tg ON teachers(telegram_user_id);
