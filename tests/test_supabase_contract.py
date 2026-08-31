"""tests/test_supabase_contract.py — прогон против настоящего Supabase (блок Э2).

Не входит в обычный прогон: pytest.ini добавляет "-m not supabase" в
addopts, поэтому "pytest -q"/"pytest" этот файл не трогает вообще. Явный
запуск: "pytest -m supabase". Нет SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY в
окружении — pytest.skip с понятной причиной, а не падение: у следующего
исполнителя ключа может не быть.

Что проверяется — ровно то, что статика блока Э1 (tests/test_sql_contract.py)
не ловит:
  - пункт 4 контракта RPC ksp_execute_sql (в ON CONFLICT ... DO UPDATE SET
    каждая колонка справа от "=" обязана быть квалифицирована) — это
    единственный способ поймать 42702 "column reference is ambiguous"
    (хотфикс 8e4b581), и ловится он только на настоящем Postgres, не на
    тексте запроса;
  - синтаксис CASE ... END * INTERVAL '1 second' и CURRENT_TIMESTAMP +
    (?::interval), который core/queue.py собирает для Postgres-ветки
    claim_next()/recover_stuck_tasks() — тоже нет способа статически
    узнать, что Postgres примет именно такой синтаксис интервалов.

Сознательно НЕ трогается таблица tasks. В проде параллельно этому прогону
обычно работает настоящий воркер очереди (core/queue.py, QueueWorker.
run_forever), а claim_next() забирает ГЛОБАЛЬНО самую старую задачу со
статусом pending, без разбора по пользователю — вставить тестовую задачу
значило бы рискнуть, что настоящий воркер подхватит её раньше теста и
попробует реально её выполнить (настоящий вызов LLM за настоящие деньги,
настоящая попытка отправить сообщение в несуществующий чат), либо что тест
подхватит и испортит статус чужой настоящей задачи. Вместо этого CASE/
INTERVAL проверяется отдельными чистыми SELECT без единой записи — тот же
синтаксис, что и в _build_claim_query()/recover_stuck_tasks(), но без риска
для боевой очереди. Это сознательное сужение периметра блока, объяснено
подробно в REPORT.md.

Все остальные тесты пишут только в строки с telegram_user_id из заведомо
невозможного для Telegram диапазона (Telegram выдаёт исключительно
положительные id) — боевые процессы такие id никогда не видят и не
подхватят. Удаление — в finally каждого теста; последний тест модуля
проверяет, что весь этот диапазон в боевых таблицах пуст, даже если один
из предыдущих тестов оборвался на середине или прошлый прогон упал.
"""

from datetime import datetime, timedelta

import pytest

from core.config import settings
from core.db import execute, query


pytestmark = pytest.mark.supabase

# Заведомо невозможный для Telegram диапазон id — используется во всех
# тестах этого файла, чтобы боевые процессы гарантированно не увидели
# тестовые строки ни при каких условиях.
TEST_USER_BASE = -900_000_000

_CLEANUP_TABLES = ("usage_daily", "admin_access", "consents", "template_selections")


@pytest.fixture(autouse=True)
def _require_real_supabase():
    if not settings.supabase_url or not settings.supabase_service_role_key:
        pytest.skip("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY не заданы — прогон против Supabase пропущен")


def _cleanup_user(telegram_user_id: int) -> None:
    for table in _CLEANUP_TABLES:
        execute(f"DELETE FROM {table} WHERE telegram_user_id = ?", (telegram_user_id,))


def test_record_usage_on_conflict_accumulates_on_real_postgres():
    """Хотфикс 8e4b581: голое 'SET count = count + excluded.count' давало
    42702 "column reference is ambiguous" именно на Postgres — SQLite
    молчал. Текущий код квалифицирует usage_daily.count/usage_daily.tokens
    (core/limits.py, record_usage) — здесь это проверяется на настоящем
    движке, а не на тексте запроса."""
    from core.limits import get_usage_today, record_usage

    user_id = TEST_USER_BASE - 1
    try:
        record_usage(user_id, "generate_ksp", count_delta=1, tokens_delta=100)
        record_usage(user_id, "generate_ksp", count_delta=1, tokens_delta=50)

        usage = get_usage_today(user_id)
        assert usage["counts"]["generate_ksp"] == 2
        assert usage["tokens_total"] == 150
    finally:
        _cleanup_user(user_id)


def test_grant_admin_access_on_conflict_updates_expiry_on_real_postgres():
    from core.limits import grant_admin_access, has_admin_access

    user_id = TEST_USER_BASE - 2
    try:
        first_expiry = grant_admin_access(user_id, duration=timedelta(minutes=1))
        second_expiry = grant_admin_access(user_id, duration=timedelta(hours=1))
        assert second_expiry > first_expiry
        assert has_admin_access(user_id) is True
    finally:
        _cleanup_user(user_id)


def test_has_admin_access_deletes_expired_row_on_real_postgres():
    from core.limits import KOSTANAY_TZ, has_admin_access

    user_id = TEST_USER_BASE - 3
    expired = (datetime.now(KOSTANAY_TZ) - timedelta(hours=1)).isoformat()
    try:
        execute(
            "INSERT INTO admin_access (telegram_user_id, expires_at) VALUES (?, ?)",
            (user_id, expired),
        )
        assert has_admin_access(user_id) is False
        assert query("SELECT 1 FROM admin_access WHERE telegram_user_id = ?", (user_id,)) == []
    finally:
        _cleanup_user(user_id)


def test_record_consent_on_conflict_updates_given_at_on_real_postgres():
    from bot.handlers import has_given_consent, record_consent

    user_id = TEST_USER_BASE - 4
    try:
        record_consent(user_id)
        assert has_given_consent(user_id) is True
        record_consent(user_id)  # второй раз — та же строка, через ON CONFLICT
        rows = query("SELECT COUNT(*) AS n FROM consents WHERE telegram_user_id = ?", (user_id,))
        assert rows[0]["n"] == 1
    finally:
        _cleanup_user(user_id)


def test_template_selection_on_conflict_updates_template_on_real_postgres():
    """web/api.py, api_save_template_selection — тот же приём ON CONFLICT,
    что и в остальных двух местах выше, но с внешним ключом на templates:
    берём реальные встроенные шаблоны только на чтение, не создаём новых."""
    template_rows = query("SELECT id FROM templates WHERE is_builtin = 1 ORDER BY id LIMIT 2")
    if len(template_rows) < 2:
        pytest.skip("нужно минимум 2 встроенных шаблона в боевой базе для этого теста")
    first_id, second_id = template_rows[0]["id"], template_rows[1]["id"]

    user_id = TEST_USER_BASE - 5
    upsert_sql = (
        "INSERT INTO template_selections (telegram_user_id, template_id, selected_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(telegram_user_id) DO UPDATE SET "
        "template_id = excluded.template_id, selected_at = excluded.selected_at"
    )
    try:
        execute(upsert_sql, (user_id, first_id))
        execute(upsert_sql, (user_id, second_id))

        row = query("SELECT template_id FROM template_selections WHERE telegram_user_id = ?", (user_id,))
        assert row[0]["template_id"] == second_id
    finally:
        _cleanup_user(user_id)


def test_worker_heartbeat_upsert_on_real_postgres():
    """worker_heartbeat — единственная боевая строка (id=1), которую
    настоящий воркер и так обновляет каждые ~2 секунды: наш вызов —
    легитимная перезапись того же значения по смыслу (CURRENT_TIMESTAMP),
    ничего не создаём и не удаляем отдельно."""
    from core.queue import update_worker_heartbeat

    update_worker_heartbeat()
    rows = query("SELECT updated_at FROM worker_heartbeat WHERE id = 1")
    assert rows  # ON CONFLICT сработал — строка на месте


def test_claim_query_case_interval_syntax_is_valid_postgres():
    """Синтаксис CASE ... END * INTERVAL '1 second', который
    core.queue._build_claim_query() собирает для Postgres-ветки —
    проверяется чистым SELECT, без единой записи (см. докстринг модуля,
    почему боевая tasks не трогается)."""
    rows = query(
        "SELECT CURRENT_TIMESTAMP - (CASE ? WHEN 1 THEN 10 WHEN 2 THEN 30 ELSE 90 END * INTERVAL '1 second') AS x",
        (1,),
    )
    assert rows and rows[0]["x"] is not None


def test_recover_stuck_tasks_interval_cast_syntax_is_valid_postgres():
    """CURRENT_TIMESTAMP + (?::interval) — синтаксис из
    core.queue.recover_stuck_tasks() для Postgres-ветки, тоже чистый SELECT."""
    rows = query("SELECT CURRENT_TIMESTAMP + (?::interval) AS x", ("-10 minutes",))
    assert rows and rows[0]["x"] is not None


def test_question_mark_inside_parameter_value_is_not_a_placeholder():
    """Находка 2 AUDIT.md: цикл подстановки в ksp_execute_sql искал
    следующий '?' в УЖЕ подставленной строке, поэтому знак вопроса внутри
    значения параметра считался неизрасходованным плейсхолдером и весь
    запрос отвергался с P0001 "Плейсхолдеров больше, чем параметров".

    Для этого продукта знак вопроса — норма, а не экзотика: тема урока
    ("Что такое сила?"), расшифровка урока с вопросами педагога классу,
    содержимое конспекта. Статика блока Э1 это поймать не может — текст
    запроса безупречен, ломается значение."""
    rows = query("SELECT ? AS a, ? AS b", ("Что такое сила?", "второй без знака"))
    assert rows == [{"a": "Что такое сила?", "b": "второй без знака"}]


def test_question_mark_survives_insert_and_select_roundtrip():
    """Тот же дефект на настоящей записи, а не на голом SELECT: именно так
    падали INSERT в transcripts и постановка задачи в очередь (enqueue
    кладёт тему урока внутрь JSON-параметра)."""
    user_id = TEST_USER_BASE - 10
    stored = "Что такое сила? Второй закон Ньютона"
    try:
        execute(
            "INSERT INTO admin_access (telegram_user_id, expires_at) VALUES (?, ?)",
            (user_id, stored),
        )
        rows = query(
            "SELECT expires_at FROM admin_access WHERE telegram_user_id = ?",
            (user_id,),
        )
        assert rows == [{"expires_at": stored}]
    finally:
        _cleanup_user(user_id)


def test_word_returning_inside_parameter_value_does_not_change_branch():
    """Следствие того же цикла: ветка исполнения выбиралась регуляркой по
    ПОДСТАВЛЕННОЙ строке, поэтому слово ' returning ' внутри значения
    уводило UPDATE в ветку WITH и давало 0A000 "does not have a RETURNING
    clause". Строк под условие нет — проверяется разбор запроса, не
    результат."""
    user_id = TEST_USER_BASE - 11
    execute(
        "UPDATE admin_access SET expires_at = ? WHERE telegram_user_id = ?",
        ("текст returning текст", user_id),
    )


# Настоящие идентификаторы Telegram — девяти-десятизначные. Всё, что
# меньше миллиона, живой человек прислать не мог: это след теста,
# писавшего в боевую базу (Находка 4 AUDIT.md).
_REAL_TELEGRAM_ID_FLOOR = 1_000_000

_ID_COLUMNS = {
    "teachers": "telegram_user_id",
    "students": "telegram_id",
    "consents": "telegram_user_id",
    "usage_daily": "telegram_user_id",
    "admin_access": "telegram_user_id",
    "template_selections": "telegram_user_id",
    "tasks": "telegram_chat_id",
}


def test_no_test_sized_ids_in_production_tables():
    """Находка 4 нашла механизм («тесты пишут в бой»), но не проверила,
    не осталось ли уже написанного. Осталось: строка admin_access с
    telegram_user_id = 960 — это id из
    tests/test_bot_handlers.py::test_admin_command_grants_temporary_access…,
    пережившая уборку 31.08, потому что admin_access в списке
    вычищенных таблиц не было.

    Тест намеренно смотрит на боевую базу целиком, а не только на свой
    диапазон: диапазон охраняет тест от себя, а этот — базу от всех
    тестов сразу."""
    leftovers = {}
    for table, column in _ID_COLUMNS.items():
        rows = query(
            f"SELECT {column} AS id FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} > 0 AND {column} < ?",
            (_REAL_TELEGRAM_ID_FLOOR,),
        )
        if rows:
            leftovers[table] = sorted({row["id"] for row in rows})
    assert not leftovers, (
        f"в боевой базе лежат строки с тестовыми идентификаторами: {leftovers}. "
        "Удалять их — решение автора, готовый SQL в AUDIT.md."
    )


def test_no_leftover_test_rows_in_negative_id_range():
    """Замыкающий тест модуля (порядок в файле — pytest по умолчанию идёт
    сверху вниз, без переупорядочивания: ни pytest-randomly, ни xdist в
    requirements.txt нет). Даже если один из тестов выше оборвался на
    середине или прошлый прогон упал раньше своего finally, весь диапазон
    TEST_USER_BASE и ниже в боевых таблицах обязан быть пуст."""
    for table in _CLEANUP_TABLES:
        rows = query(f"SELECT COUNT(*) AS n FROM {table} WHERE telegram_user_id <= ?", (TEST_USER_BASE,))
        assert rows[0]["n"] == 0, f"{table}: остались тестовые строки после прогона этого файла"
