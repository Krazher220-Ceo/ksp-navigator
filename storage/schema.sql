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
    -- Ф4: город спрашивается на экране регистрации в вебе (макет
    -- Registraciya). У профилей, заведённых из бота, он пустой — /teacher
    -- город не спрашивает, и менять диалог бота этот блок не стал.
    city TEXT,
    telegram_user_id INTEGER,
    -- Ф4 (FRONTEND_PLAN.md): идентификатор аккаунта в Supabase Auth для
    -- тех, кто вошёл по почте. У пришедших из Telegram он пустой, у
    -- пришедших с сайта пустой telegram_user_id — связываются две
    -- половины отдельно, кодом из бота (блок Ф4). Для базы, созданной ДО
    -- этого блока, CREATE TABLE IF NOT EXISTS колонку не добавит:
    -- см. scripts/migrate_add_auth_user_id.py.
    auth_user_id TEXT,
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
    category TEXT NOT NULL DEFAULT 'personal' CHECK(category IN ('official', 'sample', 'personal')),
    uploaded_by INTEGER REFERENCES teachers(id),  -- NULL для встроенных
    structure_json TEXT NOT NULL,    -- описание блоков и колонок
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- И1: выбор из Mini App, открытого синей кнопкой меню, не приходит боту
-- через sendData. Web API сохраняет его здесь, а следующая /generate
-- забирает и сразу удаляет. selected_at нужен для короткого срока жизни.
CREATE TABLE IF NOT EXISTS template_selections (
    telegram_user_id INTEGER PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES templates(id),
    selected_at TIMESTAMP NOT NULL
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
    -- и 'generate_konspekt' — блоком К2.1 (PLAN_STAGE2.md). 'sverka_tetradi' —
    -- блоком У4 (PLAN.md): OCR фото тетради + LLM-сравнение с расшифровкой
    -- урока, оба вызова к LLM, задача очереди тем же принципом, что и
    -- остальные. Для базы, созданной ДО этих блоков, одного перезапуска
    -- schema.sql недостаточно — CREATE TABLE IF NOT EXISTS не трогает уже
    -- существующую таблицу с другим CHECK. См. scripts/migrate_add_generate_ktp_task_type.py,
    -- scripts/migrate_add_transcribe_task_type.py и scripts/migrate_add_sverka_task_type.py.
    type TEXT CHECK(type IN ('parse_ksp','generate_ksp','generate_ktp','transcribe','generate_konspekt','sverka_tetradi')),
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

CREATE TABLE IF NOT EXISTS admin_access (
    telegram_user_id INTEGER PRIMARY KEY,
    expires_at TEXT NOT NULL
);

-- transcripts/konspekty — аудио урока (блоки К2-К5, PLAN_STAGE2.md).
-- Цепочка одна: аудиозапись -> xAI STT -> transcripts -> konspekty ->
-- КСП. Конспект БЕЗ транскрипта (source='audio' обязателен на входе)
-- в проекте не делается — решение автора, MASTER.md 0.6 п.2.
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    ktp_entry_id INTEGER REFERENCES ktp_entries(id),
    source TEXT,               -- 'audio' | 'manual'
    mode TEXT NOT NULL DEFAULT 'student' CHECK(mode IN ('student', 'teacher')),
    text TEXT NOT NULL,
    duration_seconds INTEGER,
    language TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS konspekty (
    id TEXT PRIMARY KEY,
    teacher_id INTEGER REFERENCES teachers(id),
    transcript_id TEXT REFERENCES transcripts(id),
    mode TEXT NOT NULL DEFAULT 'student' CHECK(mode IN ('student', 'teacher')),
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

-- consents — согласие на использование системы (блок Ю3, статья 15 п.2
-- пп.5 Закона РК «Об искусственном интеллекте» № 230-VIII: пользователь
-- должен ознакомиться с условиями ДО начала использования).
--
-- Отдельная таблица, а не колонка в teachers, как буквально написано в
-- PLAN.md: согласие даётся ДО того, как появляется профиль педагога
-- (/teacher ещё не пройден в момент первого /start), а teachers.name/
-- teachers.subject читаются в нескольких местах bot/handlers.py без
-- проверки на NULL (генерация КСП/КТП). Завести в teachers "пустую"
-- строку раньше профиля значило бы тихо сломать эти места значением
-- NULL там, где ожидается текст. Решение записано в отчёте по блоку.
CREATE TABLE IF NOT EXISTS consents (
    telegram_user_id INTEGER PRIMARY KEY,
    given_at TIMESTAMP NOT NULL
);

-- Ф4: согласие того, кто пришёл с сайта. Отдельной таблицей, а не
-- колонкой в consents: там первичный ключ — telegram_user_id, и снять с
-- него NOT NULL нельзя, не разобрав ключ работающей в проде таблицы.
-- Решение автора от 02.09.2026 — аддитивный вариант.
CREATE TABLE IF NOT EXISTS consents_web (
    auth_user_id TEXT PRIMARY KEY,
    given_at TIMESTAMP NOT NULL
);

-- classes/students/class_members — класс и ученики (блок У1, MASTER.md
-- 0.9 п.2: мультипользовательский режим разрешён РОВНО в этом объёме —
-- педагог и ученик, без ролей и рейтингов сверх этого).
--
-- Персональных данных ученика — минимум: telegram_id и имя. Не заводить
-- сюда ИИН, фамилию отдельно от имени, дату рождения или оценки —
-- ловушка блока прямо запрещает.
--
-- students — не "ученик этого учителя", а ученик вообще: один ученик
-- может состоять в нескольких классах (например, у разных учителей
-- одного предмета), поэтому связь — отдельная таблица class_members, не
-- teacher_id в самой students.
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    name TEXT NOT NULL,             -- как ввёл педагог, например "10 А"
    subject TEXT,
    -- Код приглашения генерирует core/classes.py (блок У2) — короткий,
    -- читаемый вслух, без похожих символов (0/O, 1/l). Можно отозвать и
    -- перевыпустить: это обычный UPDATE значения, старый код при этом
    -- просто перестаёт находиться — отдельно хранить историю кодов не
    -- нужно, план этого не требует.
    invite_code TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    -- Ф4: telegram_id перестал быть обязательным — ученик может прийти с
    -- сайта, и тогда у него есть только auth_user_id. Ровно одно из двух
    -- полей заполнено всегда; проверять это на уровне БД не стали:
    -- в SQLite CHECK не добавляется через ALTER TABLE, а расходиться
    -- локальной и прод-схеме нельзя.
    telegram_id INTEGER,
    auth_user_id TEXT,
    name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- когда стал учеником в системе, не в конкретном классе
);

CREATE TABLE IF NOT EXISTS class_members (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    student_id INTEGER NOT NULL REFERENCES students(id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_classes_invite_code ON classes(invite_code);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_telegram_id ON students(telegram_id);
-- Ф4: по auth_user_id ищется профиль вошедшего по почте — поиск идёт на
-- каждый запрос кабинета, и он обязан быть по индексу. Уникальность —
-- защита от второго профиля на тот же аккаунт.
CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_auth_user ON teachers(auth_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_students_auth_user ON students(auth_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_class_members_unique ON class_members(class_id, student_id);
CREATE INDEX IF NOT EXISTS idx_classes_teacher ON classes(teacher_id);
CREATE INDEX IF NOT EXISTS idx_class_members_student ON class_members(student_id);

-- Индексы сверх документа (PLAN_STAGE1.md, задача Б1.1)
CREATE INDEX IF NOT EXISTS idx_ktp_teacher ON ktp_entries(teacher_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_incidents_unresolved ON incidents(ended_at, notified);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teachers_tg ON teachers(telegram_user_id);
