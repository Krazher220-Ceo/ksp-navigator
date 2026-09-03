"""
web/supabase_users.py — заведение аккаунта силами сервера.

Зачем модуль: обычная регистрация Supabase присылает письмо со ссылкой,
и до перехода по ней войти нельзя. На живом продукте это ломалось трижды
подряд: встроенная почта Supabase ограничена несколькими письмами в час
(429 на /recover в логах 02.09.2026), ссылка одноразовая, а второй клик
по ней отвечает «Email link is invalid or has expired». Педагог на
пилоте не должен упираться в почтовый ящик.

Решение автора от 02.09.2026: аккаунт заводит наш сервер служебным
ключом и сразу помечает почту подтверждённой. Человек вводит пароль и
входит — письма в этом пути нет вовсе.

Что осознанно не делает: не входит за человека и не выдаёт токен. Пароль
после создания аккаунта проверяет сам Supabase, из браузера. Сервер не
должен становиться посредником, через которого проходят сессии.

Не сообщает, занята ли почта: одинаковый ответ на «создал» и «уже есть»
— иначе форма регистрации превращается в способ проверять, кто
зарегистрирован. Разбирается это на стороне браузера обычным входом.

На что опирается: httpx (уже в зависимостях) и SUPABASE_SERVICE_ROLE_KEY.
Ключ живёт только на сервере и во фронтенд не попадает никогда.
"""

import re

import httpx

from core.config import settings

# Та же длина, что требует форма регистрации. Два разных требования к
# одному паролю сбивают с толку сильнее, чем одно строгое.
MIN_PASSWORD_LENGTH = 8

# Не «полная» проверка адреса — её не существует, — а отсев очевидного
# мусора до обращения к Supabase.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


class UserCreateError(Exception):
    """Аккаунт не заведён. Текст уже пригоден для показа человеку."""


def создать_подтверждённого(email: str, password: str) -> str | None:
    """Заводит аккаунт с уже подтверждённой почтой.

    Возвращает id нового аккаунта либо None, если такая почта уже занята
    — это не ошибка, а обычный случай «человек регистрируется второй
    раз». Браузер после этого просто входит паролем.
    """
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise UserCreateError("Проверьте адрес почты — он выглядит неполным.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise UserCreateError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} знаков.")
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise UserCreateError("Регистрация по почте на этом сервере не настроена.")

    адрес = settings.supabase_url.rstrip("/") + "/auth/v1/admin/users"
    try:
        with httpx.Client(timeout=20) as клиент:
            ответ = клиент.post(
                адрес,
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    "Content-Type": "application/json",
                },
                json={"email": email, "password": password, "email_confirm": True},
            )
    except httpx.HTTPError as ошибка:
        raise UserCreateError("Сервер регистрации сейчас недоступен. Попробуйте через минуту.") from ошибка

    if ответ.status_code in (200, 201):
        try:
            return ответ.json()["id"]
        except (ValueError, KeyError, TypeError) as ошибка:
            raise UserCreateError("Сервер регистрации ответил не тем, что ожидалось.") from ошибка

    # 422 с этим кодом — «почта уже занята». Отвечаем так же, как на
    # успех: занятость чужой почты не наше дело сообщать.
    текст = ответ.text or ""
    if ответ.status_code in (400, 422) and (
        "already been registered" in текст or "already registered" in текст or "email_exists" in текст
    ):
        return None

    if ответ.status_code in (400, 422) and "password" in текст.lower():
        raise UserCreateError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} знаков.")

    raise UserCreateError("Не удалось завести аккаунт. Попробуйте ещё раз или напишите нам.")
