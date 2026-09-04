"""
tests/frontend/test_pwa.py — PWA: манифест, service worker, офлайн (блок Ф12).

Три вещи, на которых легко потерять доверие: закэшированная расшифровка
урока, закэшированный старый бандл и потерянная запись урока. Все три
сторожатся здесь.

Браузера в прогоне нет — проверяются исходники и манифест.
"""

import json
import re
from pathlib import Path

import pytest

from tests.frontend.test_frontend_landing import _без_комментариев

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = PROJECT_ROOT / "frontend"
МАНИФЕСТ = FRONTEND / "public" / "manifest.webmanifest"
SW = FRONTEND / "public" / "sw.js"
ОЧЕРЕДЬ = FRONTEND / "lib" / "offlineQueue.ts"


# --- манифест ---

def test_манифест_разбирается_и_описывает_приложение():
    манифест = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
    assert манифест["short_name"] == "Mazmun"
    assert манифест["display"] == "standalone"
    assert манифест["theme_color"] == "#0C2B49", "цвет темы — фирменный тёмно-синий из макетов"
    assert манифест["lang"] == "ru"


def test_иконки_манифеста_действительно_лежат_и_нужного_размера():
    """Иконка, объявленная и не положенная, — установка без картинки."""
    манифест = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
    for иконка in манифест["icons"]:
        файл = FRONTEND / "public" / иконка["src"].lstrip("/")
        assert файл.exists(), f"иконка {иконка['src']} объявлена, но не положена"
        # PNG хранит размеры в заголовке IHDR: 16-й байт — ширина.
        данные = файл.read_bytes()
        ширина = int.from_bytes(данные[16:20], "big")
        высота = int.from_bytes(данные[20:24], "big")
        объявлено = иконка["sizes"].split("x")
        assert (ширина, высота) == (int(объявлено[0]), int(объявлено[1])), (
            f"{иконка['src']} на самом деле {ширина}x{высота}, а объявлен как {иконка['sizes']}"
        )


def test_есть_ярлык_записать_урок():
    """Долгое нажатие на иконку — «Записать урок» (артборд MobUstanovka)."""
    манифест = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
    ярлыки = {я["name"]: я["url"] for я in манифест.get("shortcuts", [])}
    assert ярлыки == {"Записать урок": "/app/urok"}


def test_манифест_подключён_к_страницам():
    раскладка = (FRONTEND / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "manifest: '/manifest.webmanifest'" in раскладка
    assert "appleWebApp" in раскладка, "Safari читает манифест не полностью — нужны свои мета"


# --- service worker ---

def test_кэш_версионируется_и_старое_чистится():
    """
    Service worker со старым бандлом — классическая причина «у меня всё
    сломалось». При активации чужие кэши обязаны удаляться целиком.
    """
    код = SW.read_text(encoding="utf-8")
    assert re.search(r"const ВЕРСИЯ = '[^']+'", код), "версия кэша должна быть явной константой"
    активация = код[код.index("addEventListener('activate'"):]
    assert "caches.delete" in активация
    assert "НАШИ_КЭШИ.includes" in активация


def test_расшифровки_и_конспекты_не_кэшируются():
    """
    Кэшировать документы можно, расшифровки уроков и результаты сверки —
    нет. Им не место в браузере ни на минуту.
    """
    код = _без_комментариев(SW.read_text(encoding="utf-8"))
    # Единственное исключение среди путей /api/ — готовые документы.
    assert "'/api/v1/download/'" in код
    ветка = код[код.index("if (этоApi(url))"):]
    ветка = ветка[: ветка.index("if (этоСтатика")]
    assert "cache" not in ветка.lower(), "под /api/ ничего не кладётся в кэш"
    assert "return;" in ветка


def test_документы_кэшируются():
    код = SW.read_text(encoding="utf-8")
    ветка = код[код.index("if (этоДокумент(url))"):]
    ветка = ветка[: ветка.index("if (этоApi")]
    assert "КЭШ_ДОКУМЕНТОВ" in ветка and "кэш.put" in ветка


def test_service_worker_не_включается_в_разработке():
    """Закэшированный дев-бандл — полдня починки того, что уже исправлено."""
    регистрация = (FRONTEND / "components" / "pwa" / "РегистрацияSW.tsx").read_text(encoding="utf-8")
    assert "process.env.NODE_ENV === 'production'" in регистрация


# --- офлайн-очередь ---

def test_запись_урока_не_теряется_без_сети():
    """
    Урок уже прошёл, и второй раз его не записать. Запись, которую не
    удалось отправить, обязана лечь в очередь браузера.
    """
    страница = _без_комментариев(
        (FRONTEND / "app" / "(cabinet)" / "app" / "urok" / "page.tsx").read_text(encoding="utf-8")
    )
    assert "e.код === 'NETWORK'" in страница
    assert "await отложить(" in страница
    assert "'отложено'" in страница


def test_отложенное_уходит_при_появлении_сети():
    регистрация = (FRONTEND / "components" / "pwa" / "РегистрацияSW.tsx").read_text(encoding="utf-8")
    assert "addEventListener('online'" in регистрация
    assert "отправитьОтложенное" in регистрация


def test_в_очереди_лежит_только_сама_запись():
    """Ни расшифровок, ни конспектов в браузере не хранится."""
    код = ОЧЕРЕДЬ.read_text(encoding="utf-8")
    поля = re.search(r"export type ОтложеннаяЗапись = \{(.*?)\};", код, re.S).group(1)
    имена = set(re.findall(r"^\s*(\w+)\??:", поля, re.M))
    assert имена == {"id", "файл", "имя", "режим", "когда"}


def test_неудачная_отправка_не_выбрасывает_запись():
    код = ОЧЕРЕДЬ.read_text(encoding="utf-8")
    отправка = код[код.index("export async function отправитьОтложенное"):]
    # Удаление из очереди — только после успешной отправки.
    assert отправка.index("await отправитьЗапись(") < отправка.index("await забыть(")


# --- высота экрана на iOS ---

def test_на_телефоне_используется_dvh():
    """
    100vh на iOS считается по экрану без адресной строки: нижняя панель
    уезжает под неё. Правка стоит поверх артбордного значения, чтобы
    сверка с макетом осталась дословной.
    """
    css = (FRONTEND / "app" / "landing.css").read_text(encoding="utf-8")
    assert "@supports (height: 100dvh)" in css
    assert ".app, .phone { height: 100dvh; }" in css

    base = (FRONTEND / "app" / "base.css").read_text(encoding="utf-8")
    assert "height: 100vh" in base, "в base.css значение макета остаётся нетронутым"
