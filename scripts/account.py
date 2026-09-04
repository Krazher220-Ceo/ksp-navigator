"""
scripts/account.py — завести, посмотреть и удалить аккаунт кабинета.

Зачем скрипт. Аккаунт заводится обращением к /api/v1/auth/register, и
пароль в таком вызове приходится писать прямо в команду:

    curl ... -d '{"email":"...","password":"ТУТ_ПАРОЛЬ"}'

У этого две беды, и обе уже случились 04.09.2026. Первая: команду можно
запустить, не заменив подстановку, — и аккаунт заводится с паролем-
заглушкой. Вторая, тише и хуже: набранный пароль навсегда остаётся в
истории оболочки (~/.zsh_history), в логах терминала и в переписке, где
эту команду показали.

Здесь пароль спрашивается через getpass: он не появляется ни на экране,
ни в списке аргументов процесса (ps его не покажет), ни в истории команд.
Скрипт его никуда не пишет и никому не показывает — только передаёт
Supabase и забывает.

Что осознанно не делает: не придумывает пароли. Сгенерированный
ассистентом или скриптом пароль по дороге к человеку проходит через чат и
буфер обмена, то есть перестаёт быть паролем. Придумывает его человек.

Не заводит профиль педагога — это делает сам кабинет при первом входе
(web/api_v1.py, /teacher) или бот. Здесь только учётная запись.

На что опирается: core.config (адрес и service_role-ключ Supabase),
web.supabase_users (заведение аккаунта — тот же код, каким пользуется
кабинет, второй копии правил не заводим) и httpx, который уже в
зависимостях.
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from core.config import settings
from web.supabase_users import MIN_PASSWORD_LENGTH, UserCreateError, создать_подтверждённого


def _заголовки() -> dict[str, str]:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise SystemExit(
            "Не настроены SUPABASE_URL и SUPABASE_SERVICE_ROLE_KEY в .env — "
            "без них управлять аккаунтами нельзя."
        )
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


def _адрес(путь: str = "") -> str:
    return settings.supabase_url.rstrip("/") + "/auth/v1/admin/users" + путь


def показать() -> int:
    """Список аккаунтов кабинета. Пароли не хранятся в читаемом виде
    нигде — ни здесь, ни в Supabase, поэтому показать их невозможно."""
    with httpx.Client(timeout=20) as клиент:
        ответ = клиент.get(_адрес(), headers=_заголовки())
    ответ.raise_for_status()
    аккаунты = ответ.json().get("users", [])
    if not аккаунты:
        print("Аккаунтов нет.")
        return 0
    print(f"Аккаунтов: {len(аккаунты)}\n")
    for а in аккаунты:
        вход = а.get("last_sign_in_at") or "ни разу не входил"
        print(f"  {а['email']}\n    id:      {а['id']}\n    заведён: {а['created_at']}\n    вход:    {вход}\n")
    return 0


def завести(email: str) -> int:
    """Заводит аккаунт. Пароль спрашивается тут же и нигде не остаётся."""
    пароль = getpass.getpass(f"Пароль для {email} (не короче {MIN_PASSWORD_LENGTH} знаков, ввод не виден): ")
    повтор = getpass.getpass("Повторите пароль: ")
    if пароль != повтор:
        print("Пароли не совпали — ничего не сделано.", file=sys.stderr)
        return 1
    try:
        созданный = создать_подтверждённого(email, пароль)
    except UserCreateError as ошибка:
        print(str(ошибка), file=sys.stderr)
        return 1
    if созданный is None:
        print(f"Аккаунт {email} уже существует — новый не заводился, пароль не менялся.")
        print("Нужен другой пароль — удалите аккаунт и заведите заново.")
        return 0
    print(f"Готово. Аккаунт {email} заведён, почта сразу подтверждена — письма не будет.")
    print("Теперь войдите им на сайте.")
    return 0


def удалить(email: str) -> int:
    """Удаляет аккаунт по почте. Действие необратимое, поэтому спрашивает."""
    with httpx.Client(timeout=20) as клиент:
        список = клиент.get(_адрес(), headers=_заголовки())
        список.raise_for_status()
        найденные = [а for а in список.json().get("users", []) if а.get("email") == email.strip().lower()]
        if not найденные:
            print(f"Аккаунта {email} нет — удалять нечего.")
            return 0
        аккаунт = найденные[0]
        print(f"Будет удалён: {аккаунт['email']} (id {аккаунт['id']}, заведён {аккаунт['created_at']})")
        if input("Точно удалить? Напишите «да»: ").strip().lower() != "да":
            print("Отменено, ничего не удалено.")
            return 1
        ответ = клиент.delete(_адрес(f"/{аккаунт['id']}"), headers=_заголовки())
    if ответ.status_code not in (200, 204):
        print(f"Supabase отказал: {ответ.status_code} {ответ.text}", file=sys.stderr)
        return 1
    print(f"Аккаунт {email} удалён.")
    print(
        "Профиль педагога и его документы, если они были, это НЕ удаляет: "
        "у них своя привязка. Их убирает /delete_my_data в боте."
    )
    return 0


def main() -> int:
    разбор = argparse.ArgumentParser(description="Аккаунты кабинета: список, заведение, удаление.")
    команды = разбор.add_subparsers(dest="команда", required=True)
    команды.add_parser("list", help="показать все аккаунты")
    завести_разбор = команды.add_parser("create", help="завести аккаунт (пароль спросит отдельно)")
    завести_разбор.add_argument("email")
    удалить_разбор = команды.add_parser("delete", help="удалить аккаунт по почте")
    удалить_разбор.add_argument("email")
    аргументы = разбор.parse_args()

    if аргументы.команда == "list":
        return показать()
    if аргументы.команда == "create":
        return завести(аргументы.email)
    return удалить(аргументы.email)


if __name__ == "__main__":
    raise SystemExit(main())
