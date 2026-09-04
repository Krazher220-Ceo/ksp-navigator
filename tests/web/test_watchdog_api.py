"""
tests/web/test_watchdog_api.py — мониторинг веб-API (блок Ф15).

С появлением собственного фронтенда простой FastAPI человек замечает
раньше, чем простой бота: кабинет просто перестаёт открываться. Проверка
живёт в том же watchdog, что и всё остальное — второго мониторинга в
проекте не заводится.

Здесь проверяется, что проверка на месте, причина инцидента названа
по-русски и порт не разъехался с настройками.
"""

import re
import subprocess
from pathlib import Path

from core.incidents import REASON_LABELS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = PROJECT_ROOT / "scripts" / "watchdog.sh"
PLIST = PROJECT_ROOT / "launchd" / "com.alikhan.webapi.plist"


def test_watchdog_проверяет_health():
    текст = WATCHDOG.read_text(encoding="utf-8")
    assert "/api/v1/health" in текст
    assert 'cause="api_down"' in текст


def test_причина_простоя_названа_по_русски():
    """Автор читает уведомление о простое, а не код причины."""
    assert REASON_LABELS["api_down"] == "веб-API не отвечает"


def test_проверка_идёт_на_localhost_а_не_через_туннель():
    """
    Туннель — отдельная причина отказа, и она уже разбирается сетевыми
    проверками выше. Здесь важно, поднят ли сам процесс.
    """
    текст = WATCHDOG.read_text(encoding="utf-8")
    проверка = текст[текст.index("webapi_ok()"): текст.index("# --- запись инцидента")]
    assert "127.0.0.1" in проверка
    assert "cloudflare" not in проверка.lower()


def test_порт_берётся_из_env_а_не_прошит():
    """Порт задан в трёх местах — .env, plist и здесь. Читаем из .env."""
    текст = WATCHDOG.read_text(encoding="utf-8")
    assert "WEBAPP_PORT=" in текст
    assert "WEBAPI_PORT=8000" in текст, "запасное значение должно совпадать с plist"


def test_порт_в_plist_совпадает_с_запасным_значением():
    plist = PLIST.read_text(encoding="utf-8")
    порты = re.findall(r"<string>(\d{4,5})</string>", plist)
    assert "8000" in порты, "порт в launchd разъехался с watchdog"


def test_скрипт_синтаксически_цел():
    """Watchdog запускается launchd раз в пять минут: синтаксическая
    ошибка в нём означает, что о простое никто не узнает."""
    итог = subprocess.run(["bash", "-n", str(WATCHDOG)], capture_output=True, text=True, timeout=30)
    assert итог.returncode == 0, итог.stderr


def test_проверка_api_идёт_последней():
    """
    Порядок причин важен: сначала сеть, потом бот, потом воркер, и только
    затем API. Иначе обрыв интернета показался бы отказом веб-API.
    """
    текст = WATCHDOG.read_text(encoding="utf-8")
    блок = текст[текст.index('cause="ok"'): текст.index('if [ "$cause" = "ok" ]')]
    assert блок.index("bot_process") < блок.index("worker_stuck") < блок.index("api_down")
