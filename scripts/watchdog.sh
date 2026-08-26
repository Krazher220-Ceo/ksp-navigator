#!/usr/bin/env bash
#
# scripts/watchdog.sh — проверка живости сети, бота и воркера очереди,
# блоки Б11 (исходная версия) и М7 (PLAN_STAGE2.md — различение причин,
# запись инцидентов, живость воркера).
#
# Запускается launchd/com.alikhan.watchdog.plist раз в 5 минут
# (StartInterval — разовый запуск, не постоянный процесс). Проверяет:
#   1. доступен ли api.telegram.org, и ЕСЛИ НЕТ — по какой из причин
#      (М7.2: DNS не резолвится / TCP не проходит / Telegram отвечает
#      5xx — три разных состояния с разными действиями, различаются по
#      КОДУ ВОЗВРАТА curl, не по тексту сообщения — тот меняется от
#      версии к версии и локали);
#   2. загружен ли launchd-агент бота (com.alikhan.kspbot);
#   3. жив ли цикл воркера очереди (М7.3) — по свежести отметки в
#      таблице worker_heartbeat, которую core.queue.QueueWorker обновляет
#      на каждом проходе. Это ловит случай "процесс бота жив, а очередь
#      уже никто не разбирает" — watchdog версии Б11 такое не видел.
#
# Три неудачи ПОДРЯД -> строка "ТРЕВОГА" в логе, как и раньше. Начиная с
# блока М7 при достижении тревоги ещё и открывается запись в таблице
# incidents (если такой ещё не открыто) — её видит и досылает автору
# бот при следующем старте (core/incidents.py, М7.1). watchdog сам в
# Telegram ничего не шлёт — во время обрыва Telegram и есть то, что
# недоступно (М7.1, ловушка 2).
#
# Счётчик неудач и время первого сбоя хранятся в файлах между запусками
# (каждый запуск — отдельный процесс, в памяти ничего не переживает).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_ROOT/storage/app.db"

LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/watchdog.log"
STATE_FILE="$LOG_DIR/watchdog_failures.count"
FIRST_FAILURE_FILE="$LOG_DIR/watchdog_first_failure_at"
BOT_LABEL="com.alikhan.kspbot"
ALARM_THRESHOLD=3

# М7.3: воркер опрашивает очередь раз в POLL_INTERVAL_SECONDS=2 (core/queue.py) —
# отметка старше этого порога означает, что цикл реально не проворачивается
# уже минуты, а не долю секунды между опросами. 180с — с большим запасом
# против нормального ритма, чтобы не поднимать тревогу на разовой заминке.
WORKER_HEARTBEAT_STALE_SECONDS=180

mkdir -p "$LOG_DIR"
[ -f "$STATE_FILE" ] || echo 0 > "$STATE_FILE"

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

# --- М7.2: причина недоступности сети, по коду возврата curl ---
#
# Печатает одно из: ok | dns_fail | tcp_fail | telegram_5xx
probe_network_cause() {
    local http_code curl_exit
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://api.telegram.org" 2>/dev/null)
    curl_exit=$?

    if [ "$curl_exit" -ne 0 ]; then
        case "$curl_exit" in
            6) echo "dns_fail" ;;      # Could not resolve host — было 25.08
            *) echo "tcp_fail" ;;      # 7=connect failed, 28=timeout и остальное сетевое — было 26.08
        esac
        return
    fi

    case "$http_code" in
        5*) echo "telegram_5xx" ;;     # сеть цела, проблема на стороне Telegram — было 26.08 07:13
        *) echo "ok" ;;
    esac
}

bot_agent_loaded() {
    launchctl list "$BOT_LABEL" >/dev/null 2>&1
}

# --- М7.3: жив ли цикл воркера очереди ---
worker_heartbeat_ok() {
    [ -f "$DB_PATH" ] || return 1  # базы ещё нет — не наша забота здесь, не поднимаем worker_stuck
    local age_seconds
    age_seconds=$(sqlite3 "$DB_PATH" \
        "SELECT CAST((julianday('now') - julianday(updated_at)) * 86400 AS INTEGER) FROM worker_heartbeat WHERE id = 1;" \
        2>/dev/null)
    # Таблицы/строки может не быть на старой базе (миграция ещё не применена
    # или бот ни разу не стартовал после неё) — не считаем это отдельным
    # сбоем, worker_stuck про другое: живой воркер, который перестал тикать.
    [ -z "$age_seconds" ] && return 0
    [ "$age_seconds" -le "$WORKER_HEARTBEAT_STALE_SECONDS" ]
}

# --- запись инцидента (только через sqlite3 CLI — у watchdog нет доступа
#     к процессу бота, М7.1 ловушка 2) ---

open_incident_if_needed() {
    local reason="$1"
    [ -f "$DB_PATH" ] || return 0  # базы нет — нечего писать, не наша забота
    local already_open
    already_open=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM incidents WHERE ended_at IS NULL;" 2>/dev/null)
    [ "$already_open" = "0" ] || return 0

    local started_at
    started_at=$(cat "$FIRST_FAILURE_FILE" 2>/dev/null)
    [ -n "$started_at" ] || started_at=$(timestamp)

    sqlite3 "$DB_PATH" \
        "INSERT INTO incidents (started_at, reason) VALUES ('$started_at', '$reason');" 2>/dev/null
}

close_open_incident() {
    [ -f "$DB_PATH" ] || return 0
    sqlite3 "$DB_PATH" "UPDATE incidents SET ended_at = datetime('now') WHERE ended_at IS NULL;" 2>/dev/null
    rm -f "$FIRST_FAILURE_FILE"
}

failures=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
case "$failures" in
    ''|*[!0-9]*) failures=0 ;;
esac

network_cause=$(probe_network_cause)

cause="ok"
if [ "$network_cause" != "ok" ]; then
    cause="$network_cause"
elif ! bot_agent_loaded; then
    cause="bot_process"
elif ! worker_heartbeat_ok; then
    cause="worker_stuck"
fi

if [ "$cause" = "ok" ]; then
    if [ "$failures" -ne 0 ]; then
        echo "$(timestamp) OK: всё снова в порядке, сбрасываю счётчик ($failures -> 0)" >> "$LOG_FILE"
        close_open_incident
    fi
    echo 0 > "$STATE_FILE"
    exit 0
fi

if [ "$failures" -eq 0 ]; then
    timestamp > "$FIRST_FAILURE_FILE"
fi

failures=$((failures + 1))
echo "$failures" > "$STATE_FILE"

echo "$(timestamp) проблема, попытка $failures из $ALARM_THRESHOLD: причина=$cause" >> "$LOG_FILE"

if [ "$failures" -ge "$ALARM_THRESHOLD" ]; then
    echo "$(timestamp) ТРЕВОГА: $ALARM_THRESHOLD неудачи подряд — причина=$cause" >> "$LOG_FILE"
    open_incident_if_needed "$cause"
fi
