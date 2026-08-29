"""
core/adal_azamat.py — справочник шести проектов программы «Адал Азамат».

Зачем модуль: хранит дословные названия проектов и их направления в
одном месте, чтобы диалог и генератор документа использовали одинаковые
данные. Что осознанно не делает: не подбирает проект к теме урока,
не создаёт связь с уроком и не включает ежедневные практики программы.
На что опирается: приказ Министерства просвещения РК от 26.05.2025 №123.
"""


PROJECTS = {
    "kamkor": {
        "name": "Қамқор",
        "direction": "волонтёрство и экология",
    },
    "enbegi_adal_zhas_oren": {
        "name": "Еңбегі адал – жас өрен",
        "direction": "профориентация и мотивация к труду",
    },
    "smart_bala": {
        "name": "Smart Bala",
        "direction": "развитие IT-компетенций",
    },
    "shabyt": {
        "name": "Шабыт",
        "direction": "духовное развитие через искусство",
    },
    "ushkyr_oi_alany": {
        "name": "Ұшқыр ой алаңы",
        "direction": "дебаты и интеллектуальные игры",
    },
    "balalar_kitapkhanasy": {
        "name": "Балалар кітапханасы",
        "direction": "читательская грамотность",
    },
}

PROJECT_ORDER = list(PROJECTS.keys())


def get_project(key: str) -> dict | None:
    """Проект по внутреннему ключу или None, если такого нет."""
    return PROJECTS.get(key)


def list_projects() -> list[dict]:
    """Проекты в утверждённом порядке, с ключом для кнопок и хранения."""
    return [{"key": key, **project} for key, project in PROJECTS.items()]


def find_project_key_by_name(name: str) -> str | None:
    """Ключ проекта по отображаемому названию без учёта регистра и пробелов."""
    normalized = " ".join(name.strip().lower().split())
    for key, project in PROJECTS.items():
        if project["name"].lower() == normalized:
            return key
    return None
