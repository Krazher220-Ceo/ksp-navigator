"""
tests/test_tariffs_page.py — витрина тарифов (блок Ф11).

Тарифы в пилоте — витрина и только витрина. Здесь сторожится, что в коде
не появилось ничего, что реально ограничивает или принимает деньги:
флаг выключен, платёжного провайдера нет, кнопки ведут на объяснение.

Числа сверяются с design/TARIFFS.md через frontend/content/tariffs.ts —
тем же тестом, что и на лендинге (tests/test_frontend_landing.py).
"""

import re
from pathlib import Path

import pytest

from core.limits import STUDENT_DAILY_SVERKA_LIMITS, STUDENT_TARIFFS_ENABLED
from tests.test_frontend_landing import _без_комментариев

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = PROJECT_ROOT / "frontend"
СТРАНИЦА = FRONTEND / "app" / "(cabinet)" / "app" / "tarif" / "page.tsx"
ТАРИФЫ_TS = FRONTEND / "content" / "tariffs.ts"


def код(путь: Path) -> str:
    return _без_комментариев(путь.read_text(encoding="utf-8"))


# --- пилот безлимитный ---

def test_флаг_тарифов_выключен():
    """Пилот безлимитный. Включение флага — не работа этого блока."""
    assert STUDENT_TARIFFS_ENABLED is False


def test_сверки_ученика_приведены_к_одной_и_пяти():
    """
    Решение автора от 01.09: верхний уровень «10» убран, остались одна
    сверка в сутки и пять. Числа — из design/TARIFFS.md.
    """
    assert STUDENT_DAILY_SVERKA_LIMITS == {"free": 1, "student": 1, "student_plus": 5}


def test_на_витрине_нет_платёжного_провайдера():
    """
    Работающей оплаты в проекте нет. Появившееся имя провайдера — это уже
    не витрина, а платёжная логика.
    """
    запрещено = ["stripe", "kaspi", "cloudpayments", "paybox", "robokassa", "yookassa", "checkout"]
    найдено = []
    for путь in sorted(FRONTEND.glob("**/*")):
        if путь.is_dir() or "node_modules" in путь.parts or ".next" in путь.parts:
            continue
        if путь.suffix not in {".ts", ".tsx"}:
            continue
        текст = _без_комментариев(путь.read_text(encoding="utf-8")).lower()
        найдено += [f"{путь.name}: {слово}" for слово in запрещено if слово in текст]
    assert найдено == [], f"на витрине появилась платёжная логика: {найдено}"


def test_кнопка_выбора_тарифа_ведёт_на_объяснение_а_не_на_оплату():
    текст = код(СТРАНИЦА)
    assert "setСпросили(true)" in текст
    assert "Оплата пока не включена" in текст
    # Ни одного перехода наружу и ни одного вызова к платёжному API.
    assert "window.location" not in текст
    assert "/pay" not in текст


def test_потребление_показано_в_операциях_а_не_в_тенге():
    """
    Решение автора от 01.09: в дэшборде и на витрине — операции. Тенге
    показываются только в ценах тарифов, а не в расходе.
    """
    текст = код(СТРАНИЦА)
    расход = текст[текст.index("Сейчас у вас пилот"): текст.index("ТАРИФЫ_ПЕДАГОГА.map")]
    assert "₸" not in расход, "расход не показывается в деньгах"
    assert "Черновики КСП сегодня" in расход


def test_слова_безлимитно_на_карточке_учителя_нет():
    """На «Учителе» 5 черновиков КСП и 2 конспекта в сутки — не безлимит."""
    текст = код(ТАРИФЫ_TS)
    учитель = текст[текст.index("ключ: 'teacher'"): текст.index("ключ: 'teacher_pro'")]
    assert "безлимит" not in учитель.lower()


def test_на_витрине_нет_скидок_и_таймеров():
    """Ни «−70%, только сегодня», ни обратного отсчёта (TARIFFS.md §7)."""
    текст = код(СТРАНИЦА).lower()
    for запрещённое in ["только сегодня", "скидк", "успей", "осталось дней", "−70", "-70%"]:
        assert запрещённое not in текст


def test_автопродление_по_умолчанию_не_включено():
    """Галочки автопродления, включённой заранее, на витрине нет."""
    текст = код(СТРАНИЦА).lower()
    assert "автопродлен" not in текст


@pytest.mark.parametrize("сумма", ["3 490 ₸", "9 900 ₸", "990 ₸", "2 990 ₸", "2 490 ₸"])
def test_цены_витрины_совпадают_с_tariffs_md(сумма):
    из_файла = (PROJECT_ROOT / "design" / "TARIFFS.md").read_text(encoding="utf-8")
    из_кода = ТАРИФЫ_TS.read_text(encoding="utf-8")
    assert сумма in из_файла
    assert сумма in из_кода


def test_витрина_берёт_числа_из_одного_места_с_лендингом():
    """Второй копии сетки в проекте нет: и лендинг, и кабинет читают
    frontend/content/tariffs.ts."""
    текст = код(СТРАНИЦА)
    assert "from '@/content/tariffs'" in текст
    # Своих сумм на странице нет — только те, что пришли из общего файла.
    свои_суммы = re.findall(r"\d[\d  ]*₸", текст)
    assert свои_суммы == [], f"на витрине появились свои числа: {свои_суммы}"
