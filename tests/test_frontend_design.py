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
GLOBALS_CSS = FRONTEND / "app" / "globals.css"
ICON_TSX = FRONTEND / "components" / "Icon.tsx"

# Стопки шрифтов сверяются отдельно: next/font раздаёт шрифты под
# сгенерированными именами, поэтому перед стопкой из макета стоит его
# переменная, а сама стопка обязана сохраниться дословно.
FONT_TOKENS = {"--sans", "--serif", "--mono"}

# Анимации артбордов в кабинет не переносились — они нужны лендингу
# (блок Ф13), а не рабочим экранам. Список закрытый: появится в макетах
# новый класс не из него — тест это заметит.
ANIMATION_ONLY = re.compile(r"^\.(sc|an|an-f|an-z|an-l|d[1-9]|wv)\b")


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


def test_правила_артборда_перенесены_в_кабинет_дословно():
    """Каждое правило шапки макетов лежит в base.css без переделки."""
    design = _rules(_artboard_css())
    cabinet = _rules(BASE_CSS.read_text(encoding="utf-8"))

    пропущено = []
    for selector, decls in design.items():
        if selector == ":root" or ANIMATION_ONLY.match(selector):
            пропущено.append(selector)
            continue
        assert selector in cabinet, f"правило «{selector}» из макетов в кабинет не перенесено"
        assert cabinet[selector] == decls, f"правило «{selector}» переписано против макета"

    # Пропустить можно только анимации и сам блок токенов: если из макетов
    # выпал или в них появился какой-то другой класс — это надо заметить.
    assert all(s == ":root" or ANIMATION_ONLY.match(s) for s in пропущено)


def test_рамок_1px_в_кабинете_нет():
    """Волосяной контур живёт внутри тени; border: 1px solid — откат к типовому виду."""
    подозрительные = []
    for path in sorted(FRONTEND.glob("**/*")):
        if path.is_dir() or "node_modules" in path.parts or ".next" in path.parts:
            continue
        if path.suffix not in {".css", ".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"border(?:-\w+)?\s*:\s*1px\s+solid", text) or re.search(
            r"border(?:Top|Right|Bottom|Left)?\s*:\s*'1px solid", text
        ):
            подозрительные.append(path.name)
    assert подозрительные == [], f"вернулись рамки 1px: {подозрительные}"


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
