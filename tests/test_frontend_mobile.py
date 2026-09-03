"""
tests/test_frontend_mobile.py — кабинет на телефоне и вход через Telegram.

Сторожит две дыры, найденные автором на живом продукте 01.09.2026:
кабинет на телефоне открывался вообще без навигации (боковое меню в
250px и никакого медиазапроса), а кнопка «Войти через Telegram» вела в
бота и никуда не пускала.

Ни сети, ни браузера здесь нет: проверяются исходники. Само поведение
виджета проверить статикой нельзя — за подпись отвечает
tests/test_telegram_login.py, а за то, что заголовок доедет, —
tests/test_api_v1.py (список allow_headers у CORS).
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = PROJECT_ROOT / "frontend"
MOBILE_CSS = FRONTEND / "app" / "adaptive.css"
TABS_TSX = FRONTEND / "components" / "MobileTabs.tsx"
LAYOUT_TSX = FRONTEND / "app" / "(cabinet)" / "layout.tsx"
VHOD_TSX = FRONTEND / "app" / "(auth)" / "vhod" / "page.tsx"
API_TS = FRONTEND / "lib" / "api.ts"


def _медиазапрос_телефона() -> str:
    css = MOBILE_CSS.read_text(encoding="utf-8")
    блок = re.search(r"@media \(max-width: \d+px\) \{(.+?)\n\}", css, re.S)
    assert блок, "медиазапрос телефона пропал из adaptive.css"
    return блок.group(1)


# --- навигация на телефоне ---

def test_на_телефоне_боковое_меню_уступает_место_вкладкам():
    """250px бокового меню на экране в 390px — половина рабочей области."""
    запрос = _медиазапрос_телефона()
    assert ".side { display: none" in запрос, "боковое меню на телефоне не убрано"
    assert ".tabbar-fixed { display: grid" in запрос, "вкладки на телефоне не показаны"
    assert re.search(r"\.app \{[^}]*grid-template-columns: minmax\(0, 1fr\)", запрос), (
        "кабинет на телефоне остался двухколоночным"
    )


def test_панель_вкладок_на_широком_экране_скрыта():
    """Иначе она перекрывала бы кабинет на ноутбуке."""
    css = MOBILE_CSS.read_text(encoding="utf-8")
    правило = re.search(r"\.tabbar-fixed \{(.+?)\}", css, re.S)
    assert правило, "правило .tabbar-fixed пропало"
    assert "display: none" in правило.group(1)


def test_под_вкладками_остаётся_место_для_содержимого():
    """Без отступа последняя карточка навсегда прячется под панелью."""
    запрос = _медиазапрос_телефона()
    assert re.search(r"\.main \{[^}]*padding-bottom:", запрос), (
        "рабочей области не оставлено места под панелью вкладок"
    )
    # Кнопка вкладок не должна прижиматься к самому низу iPhone.
    assert "env(safe-area-inset-bottom)" in MOBILE_CSS.read_text(encoding="utf-8")


def test_вкладки_нарисованы_в_кабинете():
    """Компонент, который никто не рисует, телефону не помогает."""
    раскладка = LAYOUT_TSX.read_text(encoding="utf-8")
    assert "<MobileTabs" in раскладка, "вкладки в раскладку кабинета не подключены"
    assert "роль={роль}" in раскладка, "вкладкам не передана роль — ученик увидит меню педагога"


def test_меню_телефона_берётся_из_того_же_списка_что_боковое():
    """
    Два списка разделов разошлись бы при первой же правке.

    Именно поэтому MobileTabs импортирует NAV из Sidebar, а не повторяет
    его: правило У3 «ученику пункты педагога не показываются» тогда
    держится в одном месте, а не в двух.
    """
    вкладки = TABS_TSX.read_text(encoding="utf-8")
    assert "from './Sidebar'" in вкладки
    assert "NAV_УЧЕНИКА" in вкладки and "NAV" in вкладки
    assert re.search(r"роль === 'student' \? NAV_УЧЕНИКА : NAV", вкладки), (
        "лист «Ещё» перестал зависеть от роли"
    )


def test_у_каждой_вкладки_есть_адрес():
    """Вкладка без href — кнопка, которая молчит при нажатии."""
    вкладки = TABS_TSX.read_text(encoding="utf-8")
    for список in ("ВКЛАДКИ_ПЕДАГОГА", "ВКЛАДКИ_УЧЕНИКА"):
        блок = re.search(rf"{список}: Вкладка\[\] = \[(.+?)\];", вкладки, re.S)
        assert блок, f"список {список} пропал"
        пункты = re.findall(r"\{[^}]*\}", блок.group(1))
        assert пункты, f"список {список} опустел"
        for пункт in пункты:
            assert "href:" in пункт, f"вкладка без адреса: {пункт}"


# --- вход через Telegram ---

def test_на_экране_входа_стоит_настоящий_виджет():
    """
    Кнопка-ссылка в бота из кабинета не пускала: она просто открывала
    чат. Настоящий вход — виджет Telegram, чьи данные проверяет сервер.
    """
    вход = VHOD_TSX.read_text(encoding="utf-8")
    assert "<ВходTelegram" in вход, "виджет Telegram с экрана входа пропал"

    виджет = (FRONTEND / "components" / "auth" / "ВходTelegram.tsx").read_text(encoding="utf-8")
    assert "telegram-widget.js" in виджет
    assert "data-telegram-login" in виджет and "data-onauth" in виджет


def test_кабинет_присылает_данные_входа_telegram():
    """Сохранить подпись и не отправить её — то же, что не входить."""
    api = API_TS.read_text(encoding="utf-8")
    assert api.count("'X-Telegram-Login'") >= 3, (
        "заголовок входа Telegram ставится не на всех запросах"
    )
    assert "import { входTelegram }" in api


def test_подпись_входа_в_браузере_не_проверяется():
    """
    Проверка подписи в браузере ничего не защищает: токен бота знает
    только сервер. Появившийся здесь hmac означал бы, что токен уехал
    в клиентский бандл.
    """
    хранилище = (FRONTEND / "lib" / "telegramLogin.ts").read_text(encoding="utf-8")
    for опасное in ("hmac", "BOT_TOKEN", "createHmac", "sha256"):
        assert опасное.lower() not in хранилище.lower(), (
            f"в браузерном коде появилось {опасное} — токен бота там оказаться не должен"
        )
