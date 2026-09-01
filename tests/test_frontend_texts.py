"""
tests/test_frontend_texts.py — тексты кабинета совпадают с текстами бота.

У бота и у веба должна быть одна формулировка на одну ситуацию. Держится
это не дисциплиной, а выгрузкой: frontend/content/texts.generated.ts
собирается скриптом из bot/texts.py, и здесь проверяется, что выгрузка
не устарела и что руками её никто не правил.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
СКРИПТ = PROJECT_ROOT / "scripts" / "export_texts_to_frontend.py"
ВЫГРУЗКА = PROJECT_ROOT / "frontend" / "content" / "texts.generated.ts"

sys.path.insert(0, str(PROJECT_ROOT))
from bot import texts  # noqa: E402
from scripts.export_texts_to_frontend import ИМЕНА, собрать  # noqa: E402


def test_выгрузка_текстов_не_устарела():
    """Правку в bot/texts.py надо было выгрузить — вот команда в ошибке."""
    assert ВЫГРУЗКА.exists(), "frontend/content/texts.generated.ts не собран"
    assert ВЫГРУЗКА.read_text(encoding="utf-8") == собрать(), (
        "тексты кабинета разошлись с bot/texts.py. Выполните:\n"
        "    python scripts/export_texts_to_frontend.py"
    )


def test_скрипт_умеет_проверять_сам_себя():
    """Флаг --check пригодится в ручной проверке и не должен сломаться."""
    итог = subprocess.run(
        [sys.executable, str(СКРИПТ), "--check"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert итог.returncode == 0, итог.stdout + итог.stderr


@pytest.mark.parametrize("имя", ИМЕНА)
def test_каждый_выгруженный_текст_дошёл_дословно(имя):
    содержимое = ВЫГРУЗКА.read_text(encoding="utf-8")
    значение = getattr(texts, имя)
    # В файле строка лежит в JSON-виде: переносы экранированы.
    ожидается = значение.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    assert f'{имя}: "{ожидается}"' in содержимое


def test_тексты_согласия_в_кабинете_не_переписаны():
    """
    Согласие законно значимо: в нём прямым текстом названа трансграничная
    передача (статья 8 Закона «О персональных данных»), а у ученика —
    ещё и согласие законного представителя. Переписывать его во фронтенде
    нельзя даже «для краткости».
    """
    содержимое = ВЫГРУЗКА.read_text(encoding="utf-8")
    assert "трансграничная передача данных" in содержимое
    assert "законного представителя" in содержимое


def test_экраны_берут_тексты_из_выгрузки_а_не_из_своих_строк():
    """Формулировки бота во фронтенде не продублированы вручную."""
    from tests.test_frontend_landing import _без_комментариев

    подозрительные = []
    маркеры = [
        "Код не найден",
        "Прежде чем продолжить",
        "Вы уже состоите в классе",
    ]
    for путь in sorted((PROJECT_ROOT / "frontend").glob("**/*.tsx")):
        if "node_modules" in путь.parts or ".next" in путь.parts:
            continue
        # В комментариях эти же слова стоят законно и объясняют, откуда
        # текст берётся, — их не считаем.
        текст = _без_комментариев(путь.read_text(encoding="utf-8"))
        for маркер in маркеры:
            if маркер in текст:
                подозрительные.append(f"{путь.name}: {маркер}")
    assert подозрительные == [], f"текст скопирован руками вместо ТЕКСТЫ.*: {подозрительные}"
