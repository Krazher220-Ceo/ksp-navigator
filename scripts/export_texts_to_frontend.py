"""
scripts/export_texts_to_frontend.py — переносит тексты бота во фронтенд
(блок Ф4 FRONTEND_PLAN.md).

Зачем: у бота и у кабинета на одну ситуацию должна быть одна
формулировка. Единственный способ добиться этого надёжно — не копировать
руками, а выгружать из bot/texts.py и сверять тестом.

Выгружаются только перечисленные ниже константы: файл текстов бота
большой, и тащить в браузер восемьсот строк, из которых нужны двадцать,
незачем.

Где формулировка бота на сайте не годится, берётся веб-редакция из
web/texts_web.py: имя константы то же, текст другой. В Telegram уместно
«наберите /start», на сайте команд нет вовсе. Функции при этом общие —
разводятся только слова.

Что осознанно не делает: не форматирует и не сокращает тексты. Что
написано в исходнике, то и приезжает — включая переносы строк и
предупреждающие значки.

Запуск (и проверка расхождения):
    python scripts/export_texts_to_frontend.py
    python scripts/export_texts_to_frontend.py --check
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import texts  # noqa: E402
from web import texts_web  # noqa: E402

ЦЕЛЬ = Path(__file__).resolve().parent.parent / "frontend" / "content" / "texts.generated.ts"

# Что нужно кабинету. Добавлять сюда по мере блоков; лишнее не тащить.
ИМЕНА = [
    # согласие
    "CONSENT_TEXT",
    "STUDENT_CONSENT_TEXT",
    "CONSENT_ACCEPT_BUTTON",
    "CONSENT_DECLINE_BUTTON",
    "CONSENT_DECLINED",
    # вступление в класс
    "STUDENT_JOIN_ASK_CODE",
    "STUDENT_JOIN_CODE_NOT_FOUND",
    "STUDENT_JOIN_CONFIRM",
    "STUDENT_JOIN_CONFIRM_BUTTON",
    "STUDENT_JOIN_CANCEL_BUTTON",
    "STUDENT_JOIN_ALREADY_MEMBER",
    "STUDENT_JOIN_SUCCESS",
    # сверка тетради (блок Ф10)
    "SVERKA_NO_TRANSCRIPT",
    "SVERKA_PROCESSING",
    "SVERKA_RESULT_HEADER",
    "SVERKA_TOO_MUCH_MISSING",
    "SVERKA_NOTHING_MISSING",
    "SVERKA_NOTEBOOK_UNREADABLE",
    "SVERKA_NOT_A_STUDENT",
    "SVERKA_NO_CLASSES",
    # ошибки веб-API
    "API_NOT_AUTHORIZED",
    "API_NOT_FOUND",
    "API_SERVER_UNAVAILABLE",
    "API_CONSENT_REQUIRED",
    "API_PROFILE_REQUIRED",
    "API_ALREADY_A_TEACHER",
    "ERROR_UNEXPECTED",
]

ШАПКА = """/**
 * Тексты, выгруженные из bot/texts.py.
 *
 * ⚠️ Файл собирается скриптом — руками не править. Правка делается в
 * bot/texts.py, после чего:
 *     python scripts/export_texts_to_frontend.py
 *
 * Так у бота и у кабинета остаётся одна формулировка на одну ситуацию.
 * Расхождение ловит tests/test_frontend_texts.py.
 */

export const ТЕКСТЫ = {
"""


def собрать() -> str:
    строки = []
    for имя in ИМЕНА:
        # Веб-редакция важнее: она пишется ровно для тех случаев, где
        # формулировка бота на сайте читается как инструкция к другому
        # продукту. Нет веб-редакции — едет текст бота, как и раньше.
        значение = getattr(texts_web, имя, None)
        if значение is None:
            значение = getattr(texts, имя)
        if not isinstance(значение, str):
            raise TypeError(f"{имя} — не строка, выгружать нечего")
        строки.append(f"  {имя}: {json.dumps(значение, ensure_ascii=False)},")
    return ШАПКА + "\n".join(строки) + "\n} as const;\n"


if __name__ == "__main__":
    содержимое = собрать()
    проверка = "--check" in sys.argv
    было = ЦЕЛЬ.read_text(encoding="utf-8") if ЦЕЛЬ.exists() else None
    if проверка:
        if было != содержимое:
            print("тексты кабинета разошлись с bot/texts.py — перезапустите скрипт без --check")
            raise SystemExit(1)
        print(f"тексты сверены с bot/texts.py: {len(ИМЕНА)} шт., расхождений нет")
    else:
        ЦЕЛЬ.write_text(содержимое, encoding="utf-8")
        print(f"выгружено текстов: {len(ИМЕНА)} -> {ЦЕЛЬ.relative_to(ЦЕЛЬ.parents[2])}")
