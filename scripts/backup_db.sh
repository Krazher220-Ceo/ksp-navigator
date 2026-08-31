#!/usr/bin/env bash
#
# scripts/backup_db.sh — ежедневный бэкап storage/app.db, блок Б11.
#
# ⚠️ С блока Э4 (PLAN.md) боевая база — Supabase, а не storage/app.db.
# Этот файл больше НЕ стоит в расписании launchd (его сменил
# scripts/backup_supabase.py, launchd/com.alikhan.backup.plist) — 30.08
# storage/app.db замер в момент переезда на Supabase, и этот скрипт молча
# продолжал бы бэкапить мёртвый файл, рапортуя "OK". Скрипт НЕ удалён:
# storage/app.db остаётся рабочим резервным режимом (DB_BACKEND=sqlite,
# тесты и восстановление на отдельной машине) — и именно для него этот
# скрипт по-прежнему нужен и корректен. Запускать его руками при работе в
# режиме DB_BACKEND=sqlite.
#
# Через "sqlite3 ... .backup", НЕ через cp. База работает в режиме WAL
# (core/db.py, блок Б1) — часть свежих изменений может лежать в
# storage/app.db-wal и ещё не быть перенесена в сам файл .db. Обычное
# копирование файла .db в этот момент даёт согласованную по себе, но
# НЕПОЛНУЮ картину (потеря последних транзакций), а если скопировать
# ровно во время записи — возможна и структурно повреждённая копия.
# Команда .backup — часть самой SQLite, безопасна на живой базе, не
# требует остановки бота и не блокирует его надолго.
#
# Хранит бэкапы 14 дней (RETENTION_DAYS), более старые удаляет.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="$PROJECT_ROOT/storage/app.db"
BACKUP_DIR="$PROJECT_ROOT/backup"
RETENTION_DAYS=14
LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/backup.log"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

if [ ! -f "$DB_PATH" ]; then
    echo "$(timestamp) ОШИБКА: $DB_PATH не найден, бэкап пропущен" >> "$LOG_FILE"
    exit 1
fi

DATE_SUFFIX="$(date '+%Y-%m-%d')"
BACKUP_FILE="$BACKUP_DIR/app_${DATE_SUFFIX}.db"

if ! sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'" 2>>"$LOG_FILE"; then
    echo "$(timestamp) ОШИБКА: sqlite3 .backup завершился с ошибкой, см. вывод выше" >> "$LOG_FILE"
    exit 1
fi

if [ ! -s "$BACKUP_FILE" ]; then
    echo "$(timestamp) ОШИБКА: бэкап $BACKUP_FILE создался пустым" >> "$LOG_FILE"
    exit 1
fi

# .backup копирует и структуру, и данные одним файлом — если файл
# открывается sqlite3 и целостен, бэкап рабочий (integrity_check —
# штатная проверка самой SQLite, не самодельная эвристика).
# journal_mode=DELETE — иначе сама проверка (просто открытие файла)
# оставляет рядом с бэкапом пустые -wal/-shm, которые тут не нужны:
# .backup уже даёт цельный самодостаточный файл.
integrity="$(sqlite3 "$BACKUP_FILE" "PRAGMA journal_mode=DELETE;" "PRAGMA integrity_check;" 2>>"$LOG_FILE" | tail -1)"
rm -f "${BACKUP_FILE}-wal" "${BACKUP_FILE}-shm" "${BACKUP_FILE}-journal"
if [ "$integrity" != "ok" ]; then
    echo "$(timestamp) ОШИБКА: $BACKUP_FILE не прошёл integrity_check: $integrity" >> "$LOG_FILE"
    exit 1
fi

size_human="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "$(timestamp) OK: бэкап создан -> $BACKUP_FILE ($size_human)" >> "$LOG_FILE"

# удаляем бэкапы старше RETENTION_DAYS дней
deleted="$(find "$BACKUP_DIR" -name 'app_*.db' -type f -mtime "+$RETENTION_DAYS" -print -delete 2>>"$LOG_FILE")"
if [ -n "$deleted" ]; then
    echo "$(timestamp) удалены старые бэкапы (>$RETENTION_DAYS дней): $deleted" >> "$LOG_FILE"
fi
