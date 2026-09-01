"""
tests/test_frontend_design.py — сторож соответствия кабинета артбордам (блок Ф1).

Проверяет ровно одно: то, что кабинет во frontend/ нарисован теми же
токенами, теми же правилами и теми же иконками, что макеты в design/.
Кабинет, переставший быть похожим на артборд, — это откат уже сделанной
и утверждённой работы, и заметить это должен прогон, а не глаз.

Чего эти тесты осознанно не делают: не запускают Next.js, не ходят в сеть
и не сравнивают картинки. Сравнение растра требовало бы браузера в
прогоне — вместо него сверяются исходники, из которых растр получается.
"""

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESIGN_HEAD = PROJECT_ROOT / "design" / "src" / "_head.html"
DESIGN_ICONS = PROJECT_ROOT / "design" / "src" / "_icons.json"
FRONTEND = PROJECT_ROOT / "frontend"
TOKENS_CSS = FRONTEND / "app" / "tokens.css"
BASE_CSS = FRONTEND / "app" / "base.css"
LANDING_CSS = FRONTEND / "app" / "landing.css"
GLOBALS_CSS = FRONTEND / "app" / "globals.css"
ICON_TSX = FRONTEND / "components" / "Icon.tsx"

# Стопки шрифтов сверяются отдельно: next/font раздаёт шрифты под
# сгенерированными именами, поэтому перед стопкой из макета стоит его
# переменная, а сама стопка обязана сохраниться дословно.
FONT_TOKENS = {"--sans", "--serif", "--mono"}

# Анимации артбордов лежат не в base.css, а в landing.css: рабочим
# экранам кабинета они не нужны, лендингу (блок Ф13) нужны. Правило ниже
# только разводит их по файлам — потеряться при этом не может ни одно.
ANIMATION_ONLY = re.compile(r"^\.(sc|an|an-f|an-z|an-l|d[1-9]|wv)\b")

# Единственное, что не переносится: класс .sc и его анимации scene и
# prog. Это покадровая смена сцен в промо-макетах для магазинов
# приложений — картинки, а не интерфейс продукта.
НЕ_ПЕРЕНОСИМ = re.compile(r"^\.sc\b")
НЕ_ПЕРЕНОСИМ_АНИМАЦИИ = {"scene", "prog"}


def _root_tokens(css: str) -> dict[str, str]:
    """Пары «переменная — значение» из первого блока :root."""
    block = css[css.index(":root") :]
    body = block[block.index("{") + 1 : block.index("}")]
    tokens = {}
    for chunk in body.split(";"):
        match = re.search(r"(--[a-z0-9-]+)\s*:\s*(.+)", chunk, re.I | re.S)
        if match:
            value = re.sub(r"/\*.*?\*/", "", match.group(2), flags=re.S).strip()
            tokens[match.group(1)] = value
    return tokens


def _artboard_css() -> str:
    """Содержимое единственного блока <style> в шапке макетов."""
    head = DESIGN_HEAD.read_text(encoding="utf-8")
    return head[head.index("<style>") + len("<style>") : head.index("</style>")]


def _rules(css: str) -> dict[str, str]:
    """Правила вида «селектор { объявления }», без @-блоков и комментариев."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@keyframes[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", css, flags=re.S)
    rules = {}
    for selector, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        selector = " ".join(selector.split())
        # Пути к фону в кабинете абсолютные — файл лежит в public/.
        body = body.replace("url(/", "url(")
        decls = tuple(sorted(d.strip() for d in body.split(";") if d.strip()))
        rules[selector] = decls
    return rules


def test_токены_кабинета_совпадают_с_макетами():
    """Ни один токен не потерян и ни одно значение не округлено."""
    design = _root_tokens(_artboard_css())
    cabinet = _root_tokens(TOKENS_CSS.read_text(encoding="utf-8"))

    assert set(design) == set(cabinet), "набор токенов разошёлся с макетами"
    for name, value in design.items():
        if name in FONT_TOKENS:
            assert cabinet[name].endswith(value), f"стопка {name} обрезана"
            assert cabinet[name].startswith("var(--font-"), f"перед {name} нет переменной next/font"
        else:
            assert cabinet[name] == value, f"токен {name} разошёлся с макетом"


def test_правила_артборда_перенесены_во_фронтенд_дословно():
    """
    Каждое правило шапки макетов лежит во фронтенде без переделки.

    Разложены они по двум файлам: рабочие стили в base.css, анимации —
    в landing.css. Тест смотрит на оба сразу, поэтому переложить правило
    из файла в файл можно, а переписать или потерять — нет.
    """
    design = _rules(_artboard_css())
    фронтенд = _rules(BASE_CSS.read_text(encoding="utf-8"))
    фронтенд.update(_rules(LANDING_CSS.read_text(encoding="utf-8")))

    пропущено = []
    for selector, decls in design.items():
        if selector == ":root" or НЕ_ПЕРЕНОСИМ.match(selector):
            пропущено.append(selector)
            continue
        assert selector in фронтенд, f"правило «{selector}» из макетов во фронтенд не перенесено"
        assert фронтенд[selector] == decls, f"правило «{selector}» переписано против макета"

    assert all(s == ":root" or НЕ_ПЕРЕНОСИМ.match(s) for s in пропущено)


def test_анимации_макета_лежат_в_landing_css():
    """@keyframes из макетов перенесены целиком: их вырезает _rules,
    поэтому проверяются отдельно."""
    в_макете = set(re.findall(r"@keyframes\s+(\w+)", _artboard_css()))
    в_кабинете = set(re.findall(r"@keyframes\s+(\w+)", LANDING_CSS.read_text(encoding="utf-8")))
    нужные = в_макете - НЕ_ПЕРЕНОСИМ_АНИМАЦИИ
    assert нужные <= в_кабинете, f"потеряны анимации: {sorted(нужные - в_кабинете)}"


# Рамка 1px запрещена не как таковая: разделители в шапке лендинга и
# документации нарисованы в макетах именно ей, и цвет там токен --line.
# Запрещён откат к типовому виду — рамка литеральным цветом вроде
# #E5E7EB, которую волосяной контур внутри тени как раз и заменяет.
РАМКА_1PX = re.compile(r"border[A-Za-z-]*\s*:\s*'?1px solid ([^;'\",)]+)")


def test_рамок_1px_литеральным_цветом_нет():
    """Волосяной контур живёт внутри тени; border: 1px solid #E5E7EB — откат."""
    подозрительные = []
    for path in sorted(FRONTEND.glob("**/*")):
        if path.is_dir() or "node_modules" in path.parts or ".next" in path.parts:
            continue
        if path.suffix not in {".css", ".ts", ".tsx"}:
            continue
        for цвет in РАМКА_1PX.findall(path.read_text(encoding="utf-8")):
            if not цвет.strip().startswith("var("):
                подозрительные.append(f"{path.name}: {цвет.strip()}")
    assert подозрительные == [], f"вернулись рамки 1px литеральным цветом: {подозрительные}"


def test_у_карточки_рамки_нет_вовсе():
    """У .card контур — только внутри тени --lift, как в макете."""
    правило = _rules(BASE_CSS.read_text(encoding="utf-8"))[".card"]
    # border-radius — не рамка, а скругление: его у карточки как раз 16px.
    рамки = [d for d in правило if re.match(r"border(-(width|style|color))?\s*:", d)]
    assert рамки == [], f"у карточки появилась рамка: {рамки}"


def test_активный_пункт_меню_это_залитая_таблетка():
    """Полоска слева отвергнута автором: активный пункт — заливка с бликом."""
    base = BASE_CSS.read_text(encoding="utf-8")
    правило = _rules(base)[".nav-item.on"]
    assert any(d.startswith("background: linear-gradient") for d in правило)
    assert any("inset 0 1px 0" in d for d in правило)

    sidebar = (FRONTEND / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    assert "border-left" not in base and "borderLeft" not in sidebar


def test_набор_иконок_совпадает_с_макетами():
    """В кабинете ровно те иконки, что в design/src/_icons.json, и ни одной сверх."""
    макет = json.loads(DESIGN_ICONS.read_text(encoding="utf-8"))
    блок = re.search(r"export const ICONS = \{(.*?)\n\} as const;", ICON_TSX.read_text(encoding="utf-8"), re.S)
    assert блок, "в Icon.tsx не нашлась карта ICONS"
    карта = dict(re.findall(r"^\s*([a-z]+):\s*([A-Za-z]+),$", блок.group(1), re.M))

    assert sorted(карта) == sorted(макет), "набор иконок кабинета разошёлся с макетами"
    # Совпадение самих контуров — на совести scripts/check-icons.mjs: он
    # разбирает пакет Phosphor, а тянуть node в прогон pytest незачем.
    assert len(карта) == len(макет)


def test_тема_tailwind_перекрыта_целиком():
    """Чужая палитра и Inter в проекте не появляются."""
    globals_css = GLOBALS_CSS.read_text(encoding="utf-8")
    for группа in ("--color-*", "--font-*", "--radius-*", "--shadow-*", "--ease-*"):
        assert f"{группа}: initial" in globals_css, f"группа {группа} из темы Tailwind не обнулена"
    assert "--default-font-family: var(--sans)" in globals_css


def test_ассеты_макетов_лежат_в_public():
    """Знак и два фирменных фона доступны кабинету по тем же именам."""
    for имя in ("mazmun-logo.png", "mazmun-fon.jpg", "mazmun-fon-app.jpg"):
        оригинал = PROJECT_ROOT / "design" / имя
        копия = FRONTEND / "public" / имя
        assert копия.exists(), f"{имя} не скопирован в frontend/public"
        assert копия.read_bytes() == оригинал.read_bytes(), f"{имя} отличается от макетного"


@pytest.mark.parametrize("пакет", ["next", "react", "react-dom", "@phosphor-icons/react"])
def test_каркас_собран_на_заявленных_пакетах(пакет):
    """Зависимости этапа 5 названы в package.json, а не подтянуты случайно."""
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert пакет in package["dependencies"], f"{пакет} пропал из зависимостей кабинета"


def test_сверка_иконок_и_токенов_скриптами_кабинета():
    """
    Прогоняет scripts/check-tokens.mjs и scripts/check-icons.mjs.

    Эти двое сверяют то, чего с Python не видно: контуры иконок внутри
    пакета Phosphor и значения токенов в шапке макетов. Пропускается, если
    зависимости кабинета не установлены, — прогон не должен краснеть
    только потому, что на машине нет node_modules.
    """
    import shutil
    import subprocess

    if not (FRONTEND / "node_modules").is_dir() or shutil.which("npm") is None:
        pytest.skip("зависимости кабинета не установлены: npm install во frontend/")

    итог = subprocess.run(
        ["npm", "run", "--silent", "check"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=180,
    )
    assert итог.returncode == 0, итог.stdout + итог.stderr
