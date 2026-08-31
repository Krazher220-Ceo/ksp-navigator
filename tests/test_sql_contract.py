"""Весь SQL проекта сверен с контрактом RPC ksp_execute_sql (блок Э1).

Контракт — краткий пересказ; источник правды — тело функции
ksp_execute_sql в storage/schema_supabase.sql:
  1. запрос начинается ровно с select/with/insert/update/delete, без
     ведущих пробелов и переводов строки;
  2. внутри нет точки с запятой (RPC отвергает запрос целиком);
  3. число '?' совпадает с числом переданных параметров.
Пункт 4 (в ON CONFLICT ... DO UPDATE SET каждая колонка справа от "="
квалифицирована именем таблицы или excluded.) статикой не ловится —
это грабля 2.12/блок Э2, отдельный прогон против настоящего Postgres.

Пункт 5 добавлен по Находке 3 AUDIT.md: SQLite-специфичных псевдоколонок
(rowid, _rowid_, oid) в запросе быть не может — в Postgres их нет, и
запрос с ними отвергается целиком. Ровно так /generate падал в проде на
первом же шаге, а блок Э1 этого не поймал: тогда проверялись только
четыре пункта выше.

Два источника проверяемого SQL:
  - литералы, переданные первым аргументом в query()/execute()/executemany()
    — ищутся по всему core/, bot/, web/ через ast, без импорта модулей
    и без сети;
  - core/queue.py собирает несколько запросов динамически (CASE по
    retries, разные интервалы под SQLite/Postgres) — их не найти как
    текстовый литерал (например, в _build_claim_query() f-строка ещё и
    оборачивается в .strip() — самой распространённой находкой этого
    класса ошибок и был забытый .strip()). Для них тест реально вызывает
    функции очереди в режиме Supabase и перехватывает то, что они передали
    бы в conn.execute() — тоже без единого сетевого запроса, connect()
    подменён на локальный "магнитофон".
"""

import ast
import re
from pathlib import Path

from core.config import settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("core", "bot", "web")

_SQL_CALL_NAMES = {"query", "execute", "executemany"}
_START_RE = re.compile(r"^(select|with|insert|update|delete)\s", re.IGNORECASE)
# Псевдоколонки, которые есть в SQLite и которых нет в Postgres. Границы
# слова обязательны: без них сюда попадал бы, например, "lastrowid".
_SQLITE_PSEUDOCOLUMN_RE = re.compile(r"\b(rowid|_rowid_|oid)\b", re.IGNORECASE)


def _contract_violations(sql: str, param_count: int | None) -> list[str]:
    """param_count=None — число параметров не определить статически
    (например, params передан переменной, а не литеральным кортежем) —
    пункт 3 в этом случае пропускается для конкретного вызова, а не
    считается нарушением."""
    problems = []
    if not _START_RE.match(sql):
        problems.append("не начинается с select/with/insert/update/delete без ведущих пробелов")
    if ";" in sql:
        problems.append("содержит точку с запятой")
    if param_count is not None and sql.count("?") != param_count:
        problems.append(f"число '?' ({sql.count('?')}) не совпадает с числом параметров ({param_count})")
    pseudocolumn = _SQLITE_PSEUDOCOLUMN_RE.search(sql)
    if pseudocolumn:
        problems.append(
            f"использует псевдоколонку SQLite '{pseudocolumn.group(0)}', которой нет в Postgres"
        )
    return problems


def _call_target_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_str(node: ast.AST) -> str | None:
    """None для чего угодно, кроме строкового литерала — в т.ч. для
    f-строк (ast.JoinedStr) и вызовов функций: это не текстовые ошибки,
    а сознательно пропущенные случаи, проверяемые отдельно, динамически."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_param_count(node: ast.AST) -> int | None:
    if isinstance(node, (ast.Tuple, ast.List)):
        return len(node.elts)
    return None


def _iter_literal_sql_calls(path: Path):
    """(номер строки, sql, число параметров или None) для каждого вызова
    query()/execute()/executemany() с литеральной строкой первым
    аргументом в файле path."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if _call_target_name(node) not in _SQL_CALL_NAMES:
            continue
        sql = _literal_str(node.args[0])
        if sql is None:
            continue
        param_count = _literal_param_count(node.args[1]) if len(node.args) > 1 else 0
        yield node.lineno, sql, param_count


def _project_python_files() -> list[Path]:
    """Все модули, которые МОГУТ передать SQL-текст на исполнение через
    query()/execute()/executemany() — то есть вызывающий код, а не сам
    core/db.py: его "conn.execute('PRAGMA ...')" внутри connect() работает
    с сырым sqlite3.Connection в ветке до какой-либо маршрутизации в
    Supabase (при _use_supabase() там ранний return SupabaseConnection())
    и контракту RPC заведомо не подчиняется — это не находка, а неверная
    цель сканирования."""
    files = []
    for directory in SCAN_DIRS:
        files.extend(sorted((PROJECT_ROOT / directory).rglob("*.py")))
    return [path for path in files if path != PROJECT_ROOT / "core" / "db.py"]


def test_every_literal_sql_call_matches_rpc_contract():
    violations = []
    for path in _project_python_files():
        for lineno, sql, param_count in _iter_literal_sql_calls(path):
            for problem in _contract_violations(sql, param_count):
                rel = path.relative_to(PROJECT_ROOT)
                violations.append(f"{rel}:{lineno}: {problem} — {sql[:70]!r}")
    assert not violations, "\n" + "\n".join(violations)


def test_scanner_actually_finds_known_calls():
    # Страховка от того, что сканер сломается и начнёт находить 0 вызовов
    # везде — тогда test_every_literal_sql_call... был бы зелёным просто
    # потому, что ничего не проверил.
    assert len(list(_iter_literal_sql_calls(PROJECT_ROOT / "core" / "limits.py"))) >= 6
    assert len(list(_iter_literal_sql_calls(PROJECT_ROOT / "bot" / "handlers.py"))) >= 10


def test_contract_violations_catches_leading_whitespace():
    assert _contract_violations("\n  SELECT 1", None) == [
        "не начинается с select/with/insert/update/delete без ведущих пробелов"
    ]


def test_contract_violations_catches_semicolon():
    assert _contract_violations("SELECT 1;", None) == ["содержит точку с запятой"]


def test_contract_violations_catches_placeholder_mismatch():
    assert _contract_violations("SELECT * FROM t WHERE a = ?", 2) == [
        "число '?' (1) не совпадает с числом параметров (2)"
    ]


def test_contract_violations_catches_sqlite_rowid():
    """Находка 3 AUDIT.md: "ORDER BY created_at DESC, rowid DESC" в
    core/generation_defaults.py роняло /generate на боевом Postgres у
    обоих настоящих учителей."""
    assert _contract_violations("SELECT a FROM t ORDER BY created_at DESC, rowid DESC", 0) == [
        "использует псевдоколонку SQLite 'rowid', которой нет в Postgres"
    ]


def test_contract_violations_does_not_confuse_lastrowid_with_rowid():
    """Страховка от того, что проверка станет слишком жадной: столбца с
    таким именем в проекте нет, но слово "lastrowid" в коде есть."""
    assert _contract_violations("SELECT lastrowid_column FROM t", 0) == []


def test_contract_violations_accepts_clean_query():
    assert _contract_violations("SELECT * FROM t WHERE a = ? AND b = ?", 2) == []


# =====================================================================
# Находка 2 AUDIT.md — сама RPC-функция в storage/schema_supabase.sql.
#
# Проверки ниже статические и по тексту функции: тело живёт в проде, и
# единственный способ поймать откат этой правки без сети — смотреть на
# файл, который человек применяет через SQL Editor (грабля 2.13).
# Поведение на настоящем Postgres проверяют тесты с меткой supabase
# (tests/test_supabase_contract.py) — они и должны быть красными, пока
# SQL в проде не применён.
# =====================================================================

SUPABASE_SCHEMA = PROJECT_ROOT / "storage" / "schema_supabase.sql"


def _execute_sql_function_body() -> str:
    text = SUPABASE_SCHEMA.read_text(encoding="utf-8")
    start = text.index("create or replace function public.ksp_execute_sql")
    end = text.index("revoke all on function public.ksp_execute_sql")
    return text[start:end]


def test_placeholder_search_never_rereads_the_substituted_text():
    """Ровно этот откат ронял прод: поиск следующего '?' в уже
    подставленной строке принимал знак вопроса ВНУТРИ значения
    параметра за неизрасходованный плейсхолдер."""
    body = _execute_sql_function_body()
    assert "strpos(remainder, '?')" in body
    assert "strpos(compiled, '?')" not in body, (
        "поиск плейсхолдера снова идёт по подставленной строке — вернулась Находка 2"
    )


def test_every_regexp_looks_at_the_original_statement():
    """Слово ' returning ' внутри значения параметра уводило UPDATE в
    ветку WITH и давало 0A000.

    Проверяются все строки, где функция смотрит на текст запроса
    регуляркой, — и выбор ветки исполнения, и проверка whitelist в
    начале: обе обязаны читать ИСХОДНЫЙ запрос, а не тот, в который уже
    подставлены значения параметров."""
    body = _execute_sql_function_body()
    regexp_lines = [line for line in body.splitlines() if "~ '^" in line or "returning[[:space:]]" in line]
    assert regexp_lines, "не нашёл строк с разбором текста запроса — тест смотрит не туда"
    for line in regexp_lines:
        assert "lower(source)" in line, (
            f"текст запроса разбирается после подстановки значений: {line.strip()}"
        )


def test_rpc_strictness_is_not_weakened():
    """Правка Находки 2 меняла только цикл подстановки. Запрет DDL,
    запрет точки с запятой и whitelist ключевых слов обязаны остаться —
    ослабить их «заодно» нельзя."""
    body = _execute_sql_function_body()
    assert "like '%;%'" in body
    assert "^(select|with|insert|update|delete)[[:space:]]" in body
    assert "security definer" in body
    full = SUPABASE_SCHEMA.read_text(encoding="utf-8")
    assert "revoke all on function public.ksp_execute_sql(text, jsonb) from public, anon, authenticated;" in full
    assert "grant execute on function public.ksp_execute_sql(text, jsonb) to service_role;" in full


# =====================================================================
# core/queue.py — динамически собранный SQL, не литерал в источнике.
# =====================================================================


def _configure_supabase():
    original = {
        "db_backend": settings.db_backend,
        "supabase_url": settings.supabase_url,
        "supabase_service_role_key": settings.supabase_service_role_key,
    }
    object.__setattr__(settings, "db_backend", "supabase")
    object.__setattr__(settings, "supabase_url", "https://example.supabase.co")
    object.__setattr__(settings, "supabase_service_role_key", "test-key")
    return original


def _restore_settings(original):
    for field, value in original.items():
        object.__setattr__(settings, field, value)


class _RecordingCursor:
    """Достаточно правдоподобная строка, чтобы fail()/claim_next() не
    упали на "задача не найдена" — реальные значения тут не важны, важен
    только текст SQL и число параметров, которые дошли до .execute()."""

    def __init__(self):
        self.lastrowid = None

    def fetchone(self):
        return {"id": "fake-task-id", "status": "pending", "type": "generate_ksp", "payload": "{}"}

    def fetchall(self):
        return []


class _RecordingConnection:
    """Ничего не шлёт по сети — запоминает, что реально попало бы в RPC."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _RecordingCursor()

    def executemany(self, sql, params_list):
        for params in params_list:
            self.calls.append((sql, tuple(params)))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_queue_dynamically_built_sql_matches_rpc_contract(monkeypatch):
    import core.db as db_module
    import core.queue as queue_module

    recorder = _RecordingConnection()
    monkeypatch.setattr(db_module, "connect", lambda db_path=None: recorder)

    original = _configure_supabase()
    try:
        queue_module.enqueue("generate_ksp", {"a": 1}, chat_id=42)
        queue_module.claim_next()
        queue_module.complete("task-1", "результат")
        queue_module.fail("task-1", "ошибка", max_retries=3)
        queue_module.recover_stuck_tasks(stuck_minutes=10)
        queue_module.recover_stuck_tasks()
        queue_module.update_worker_heartbeat()
    finally:
        _restore_settings(original)

    # По одному вызову на enqueue/claim_next/complete/fail/heartbeat,
    # один на recover_stuck_tasks(stuck_minutes=10) и шесть на
    # recover_stuck_tasks() без порога (5 известных типов + ветка NOT IN) —
    # если сканер вдруг перестал что-то ловить, список будет короче.
    assert len(recorder.calls) >= 10

    violations = []
    for sql, params in recorder.calls:
        for problem in _contract_violations(sql, len(params)):
            violations.append(f"{problem} — {sql[:70]!r} params={params}")
    assert not violations, "\n" + "\n".join(violations)
