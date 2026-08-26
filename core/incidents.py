"""
core/incidents.py — уведомление автора о завершённых инцидентах (блок М7.1,
PLAN_STAGE2.md).

Зачем модуль: scripts/watchdog.sh пишет строки в incidents напрямую через
sqlite3 CLI (у него нет доступа к процессу бота — ловушка 2 плана), а
отправка сообщения — только работа бота, у которого есть Bot/aiogram.
Разбор события "инцидент завершился, пора сказать автору" — здесь.

Что осознанно не делает: не решает, ЧТО написано в тексте сообщения на
русском — это bot/texts.py. Не открывает инцидент и не закрывает его —
это делает watchdog.sh (открывает при первом сбое, закрывает при
восстановлении). Здесь только чтение неотправленных завершённых
инцидентов и отметка "отправлено".

На что опирается: core.db. ADMIN_TELEGRAM_CHAT_ID из core.config.settings —
если не задан, некому слать (не падает, просто не отправляет, тем же
принципом, что WEBAPP_URL)."""

from core.db import execute, query

REASON_LABELS = {
    "dns_fail": "не резолвился DNS",
    "tcp_fail": "сеть недоступна (TCP)",
    "telegram_5xx": "Telegram отвечает ошибкой сервера",
    "bot_process": "процесс бота не запущен",
    "worker_stuck": "воркер очереди завис",
}


def get_unnotified_resolved_incidents(db_path=None) -> list[dict]:
    """Завершённые (ended_at не NULL), ещё не отправленные (notified=0)
    инциденты, в хронологическом порядке — если их накопилось несколько
    (бот перезапускался несколько раз подряд), автор должен узнать про
    каждый, а не только про последний."""
    rows = query(
        "SELECT id, started_at, ended_at, reason FROM incidents "
        "WHERE ended_at IS NOT NULL AND notified = 0 ORDER BY started_at",
        db_path=db_path,
    )
    return [dict(row) for row in rows]


def mark_notified(incident_id: int, db_path=None) -> None:
    execute("UPDATE incidents SET notified = 1 WHERE id = ?", (incident_id,), db_path=db_path)


def format_incident_message(incident: dict) -> str:
    """Один текст на один инцидент — М7.1, ловушка: одно сообщение на
    инцидент, а не на каждую тревогу watchdog внутри него."""
    reason_label = REASON_LABELS.get(incident["reason"], incident["reason"])
    started = str(incident["started_at"])[:16].replace("T", " ")
    ended = str(incident["ended_at"])[:16].replace("T", " ")
    return f"Связь пропадала с {started} до {ended}. Причина: {reason_label}."
