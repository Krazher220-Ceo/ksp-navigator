"""
tests/test_ktp_web.py — КТП из кабинета (02.09.2026).

До этого дня пункт «Собрать КТП» в меню кабинета вёл в никуда:
эндпоинта не было, загрузить план можно было только ботом. При этом весь
мастер КСП опирается на КТП — из него берутся тема, раздел и код цели.

Отдельно сторожится то, на чём я едва не обжёгся при проверке: загрузка
КТП ЗАМЕНЯЕТ прежний план целиком. Проверять это на живом сервере
нельзя — у автора там реальный план на учебный год.
"""

from pathlib import Path

import pytest

from web import api_v1

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_принимаются_только_те_форматы_которые_разборщик_умеет():
    """
    Лишнее расширение в списке — это «файл принят» и пустой КТП следом.
    Список сверяется с самим разборщиком, а не с памятью.
    """
    разборщик = (PROJECT_ROOT / "core" / "ktp_parser.py").read_text(encoding="utf-8")
    for расширение in api_v1.РАСШИРЕНИЯ_КТП:
        assert f'"{расширение}"' in разборщик or f"'{расширение}'" in разборщик, (
            f"кабинет принимает {расширение}, а core/ktp_parser.py его не читает"
        )
    assert api_v1.РАСШИРЕНИЯ_КТП == {".docx", ".xlsx"}


def test_загрузка_ктп_заменяет_а_не_дописывает():
    """
    КТП — один документ на учебный год, а не журнал приращений.

    Замена названа прямым текстом и в шапке эндпоинта, и на экране: это
    ровно то место, где человек может потерять план за год, не поняв,
    что произошло.
    """
    api = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/ktp/upload")'):]
    кусок = кусок[: кусок.index('@router.post("/ktp/generate")')]
    assert "ЗАМЕНЯЕТ" in кусок, "замена прежнего плана не названа в шапке"
    assert "save_ktp_entries" in кусок

    экран = (PROJECT_ROOT / "frontend" / "app" / "(cabinet)" / "app" / "ktp" / "page.tsx").read_text(encoding="utf-8")
    assert "заменяет прежний план" in экран, "человека не предупредили о замене"


def test_исходник_ктп_не_остаётся_на_диске():
    """Из файла уже взяли всё нужное; то же правило, что у аудиозаписи."""
    api = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/ktp/upload")'):]
    кусок = кусок[: кусок.index('@router.post("/ktp/generate")')]
    assert "unlink(missing_ok=True)" in кусок
    assert "finally:" in кусок, "удаление не в finally — при ошибке разбора файл останется"


def test_разбор_не_ставится_в_очередь():
    """
    Разбор таблицы не зовёт нейросеть и занимает доли секунды. Очередь
    здесь заставила бы человека опрашивать статус ради операции быстрее,
    чем загрузка файла.
    """
    api = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/ktp/upload")'):]
    кусок = кусок[: кусок.index('@router.post("/ktp/generate")')]
    assert "enqueue(" not in кусок


def test_сборка_ктп_наоборот_идёт_очередью():
    """Десятки секунд нейросети в HTTP-запросе оборвутся по дороге."""
    api = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/ktp/generate")'):]
    кусок = кусок[: кусок.index('@router.get("/ktp/entries")')]
    assert 'enqueue(\n        "generate_ktp"' in кусок
    assert "task_id" in кусок


@pytest.mark.parametrize("поле", ["predmet", "klass", "chasov_v_nedelu", "chasov_v_god"])
def test_сборка_ктп_спрашивает_всё_что_нужно_обработчику(поле):
    """
    Обработчик очереди берёт эти поля по ключу: недостающее — KeyError
    в очереди, то есть задача, падающая уже после постановки.
    """
    api = (PROJECT_ROOT / "web" / "api_v1.py").read_text(encoding="utf-8")
    кусок = api[api.index('@router.post("/ktp/generate")'):]
    кусок = кусок[: кусок.index('@router.get("/ktp/entries")')]
    assert f'"{поле}"' in кусок


def test_пункт_меню_больше_не_ведёт_в_никуда():
    """Пункт без адреса — кнопка, которая молчит при нажатии."""
    меню = (PROJECT_ROOT / "frontend" / "components" / "Sidebar.tsx").read_text(encoding="utf-8")
    ктп = меню[меню.index("key: 'ktp'"): меню.index("key: 'ktp'") + 120]
    assert "href: '/app/ktp'" in ктп
