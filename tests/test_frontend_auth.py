"""
tests/test_frontend_auth.py — экраны входа и регистрации (блок Ф4).

Сторожит то, что на этих экранах ошибиться дороже всего: пароль в поле
пароля, согласие до формы, ученик без формы регистрации и служебный ключ
Supabase, которому во фронтенде не место.

Браузера здесь нет — проверяются исходники экранов.
"""

import re
from pathlib import Path

import pytest

from tests.test_frontend_landing import _без_комментариев

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = PROJECT_ROOT / "frontend"
ВХОД = FRONTEND / "app" / "(auth)" / "vhod" / "page.tsx"
РЕГИСТРАЦИЯ = FRONTEND / "app" / "(auth)" / "registraciya" / "page.tsx"
КЛАСС = FRONTEND / "app" / "(auth)" / "klass" / "page.tsx"
СОГЛАСИЕ = FRONTEND / "components" / "auth" / "Соглашение.tsx"
ПЕРЕКЛЮЧАТЕЛЬ = FRONTEND / "components" / "auth" / "RoleSwitch.tsx"


def код(путь: Path) -> str:
    return _без_комментариев(путь.read_text(encoding="utf-8"))


# --- пароль ---

@pytest.mark.parametrize("экран,ожидается", [
    (ВХОД, "current-password"),
    (РЕГИСТРАЦИЯ, "new-password"),
])
def test_пароль_идёт_полем_пароля_с_правильным_автозаполнением(экран, ожидается):
    текст = код(экран)
    поле = текст[текст.index('type="password"') - 300 : текст.index('type="password"') + 300]
    assert f'autoComplete="{ожидается}"' in поле


def test_кнопки_показать_пароль_по_умолчанию_нет():
    """Экран входа в школе открывают при классе — пароль не показываем."""
    for экран in (ВХОД, РЕГИСТРАЦИЯ):
        текст = код(экран)
        assert 'type="text"' not in текст or "password" not in текст.split('type="text"')[0][-200:]
        assert "показать пароль" not in текст.lower()


def test_пароль_не_короче_восьми_знаков():
    assert "minLength={8}" in код(РЕГИСТРАЦИЯ)


# --- согласие ---

def test_согласие_показывается_до_формы_регистрации():
    """
    Не галочка под кнопкой, а отдельный экран впереди: пока условия не
    приняты, формы регистрации на странице нет вовсе.
    """
    текст = код(РЕГИСТРАЦИЯ)
    assert "{!условияПриняты ? (" in текст
    assert "<Соглашение" in текст
    # Форма живёт в ветке «иначе» — то есть до принятия её не существует.
    ветка_иначе = текст.index(") : (")
    assert текст.index("<Соглашение") < ветка_иначе < текст.index("<form")


def test_у_ученика_своя_редакция_согласия():
    """С упоминанием законного представителя — ученик несовершеннолетний."""
    assert "STUDENT_CONSENT_TEXT" in код(СОГЛАСИЕ)
    assert 'роль="student"' in код(КЛАСС)


def test_согласие_на_экране_класса_идёт_первым_шагом():
    текст = код(КЛАСС)
    assert "useState<Шаг>('условия')" in текст
    assert текст.index("шаг === 'условия'") < текст.index("шаг === 'код'")


# --- роли ---

def test_ролей_ровно_две():
    """Третьей роли нет и не заводится."""
    текст = код(ПЕРЕКЛЮЧАТЕЛЬ)
    assert "export type Роль = 'teacher' | 'student';" in текст
    assert len(re.findall(r"пункт\('(teacher|student)'", текст)) == 2


def test_ученик_на_регистрацию_не_попадает():
    """У ученика формы регистрации нет: он вступает в класс по коду."""
    текст = код(РЕГИСТРАЦИЯ)
    assert "router.push('/klass')" in текст
    текст_класса = код(КЛАСС)
    assert "password" not in текст_класса, "на экране ученика не должно быть пароля"
    assert "email" not in текст_класса.lower(), "почту у ученика тоже не спрашиваем"


# --- код приглашения ---

def test_про_регистр_и_дефисы_на_экране_сказано():
    """Код читается вслух: человек должен видеть, что записать его можно как угодно."""
    текст = код(КЛАСС)
    assert "Регистр не важен" in текст
    assert 'autoCapitalize="characters"' in текст


def test_предпросмотр_класса_идёт_до_вступления():
    """Ребёнок сначала видит, куда вступает, и только потом решает."""
    текст = код(КЛАСС)
    assert текст.index("предпросмотрКласса") < текст.index("вступитьВКласс")


# --- ключи ---

def test_во_фронтенде_только_публичный_ключ_supabase():
    """
    SUPABASE_SERVICE_ROLE_KEY даёт полный доступ к базе мимо всех
    проверок. Во frontend/ его нет ни в коде, ни в примере окружения.
    """
    найдено = []
    for путь in sorted(FRONTEND.glob("**/*")):
        if путь.is_dir() or "node_modules" in путь.parts or ".next" in путь.parts:
            continue
        if путь.suffix not in {".ts", ".tsx", ".css", ".json", ".example", ""}:
            continue
        содержимое = путь.read_text(encoding="utf-8", errors="ignore")
        if "SERVICE_ROLE" in содержимое and "не попадает" not in содержимое:
            найдено.append(путь.name)
    assert найдено == [], f"служебный ключ упомянут во фронтенде: {найдено}"
    assert "NEXT_PUBLIC_SUPABASE_ANON_KEY" in (FRONTEND / ".env.example").read_text(encoding="utf-8")


# --- формул во фронтенде нет ---

def test_роль_приходит_с_сервера_а_не_вычисляется():
    """
    Логика не переезжает во фронтенд: кто перед нами — решает FastAPI,
    браузер только показывает. Роль в интерфейсе — это выбор экрана.
    """
    for экран in (ВХОД, РЕГИСТРАЦИЯ, КЛАСС):
        текст = код(экран)
        assert "resolve_role" not in текст
        assert "teachers" not in текст and "students" not in текст, \
            "во фронтенде не должно быть имён таблиц базы"
