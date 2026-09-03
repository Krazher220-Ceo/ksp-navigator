#!/usr/bin/env bash
#
# scripts/api_toggle.sh — включает/выключает веб-API одной командой,
# чтобы MacBook не грузился постоянным процессом, когда кабинет никто
# не открывает.
#
# Выключает вместе с API и com.alikhan.watchdog — без этого watchdog
# (проверяет /api/v1/health каждые 5 минут, scripts/watchdog.sh) через
# три неудачи подряд открыл бы запись в incidents и при следующем
# старте бота прислал бы автору «уведомление о простое» — хотя простой
# в этом случае устроен намеренно, а не является сбоем. При включении
# watchdog возвращается вместе с API.
#
# Бота (com.alikhan.kspbot) не трогает: это основной канал входа в
# продукт, выключать его тем же движением было бы неожиданно.
#
# Не редактирует .plist — только загружает/выгружает уже существующих
# launchd-агентов через launchctl (bootstrap/bootout), как это делает
# сам launchd при перезапуске Mac.

set -euo pipefail

UID_NUM=$(id -u)
DOMAIN="gui/${UID_NUM}"
WEBAPI_LABEL="com.alikhan.webapi"
WATCHDOG_LABEL="com.alikhan.watchdog"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

загружен() {
    launchctl print "${DOMAIN}/$1" >/dev/null 2>&1
}

включить() {
    if загружен "$WEBAPI_LABEL"; then
        echo "API уже включён."
    else
        launchctl bootstrap "$DOMAIN" "$LAUNCH_AGENTS/${WEBAPI_LABEL}.plist"
        launchctl kickstart -k "${DOMAIN}/${WEBAPI_LABEL}"
        echo "API включён: http://127.0.0.1:8000"
    fi
    if ! загружен "$WATCHDOG_LABEL"; then
        launchctl bootstrap "$DOMAIN" "$LAUNCH_AGENTS/${WATCHDOG_LABEL}.plist"
    fi
    echo "watchdog снова следит за API."
}

выключить() {
    if загружен "$WATCHDOG_LABEL"; then
        launchctl bootout "${DOMAIN}/${WATCHDOG_LABEL}"
    fi
    if загружен "$WEBAPI_LABEL"; then
        launchctl bootout "${DOMAIN}/${WEBAPI_LABEL}"
        echo "API выключён. Кабинет (сайт) сейчас недоступен — это ожидаемо."
    else
        echo "API уже выключен."
    fi
    echo "watchdog тоже остановлен — не будет писать ложные инциденты, пока API выключен."
}

статус() {
    for label in "$WEBAPI_LABEL" "$WATCHDOG_LABEL"; do
        if загружен "$label"; then
            echo "$label: включён"
        else
            echo "$label: выключен"
        fi
    done
}

case "${1:-}" in
    on)  включить ;;
    off) выключить ;;
    status) статус ;;
    *)
        echo "Использование: scripts/api_toggle.sh {on|off|status}" >&2
        exit 1
        ;;
esac
