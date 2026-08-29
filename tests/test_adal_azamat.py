"""Проверки справочника проектов программы «Адал Азамат»."""

from core.adal_azamat import PROJECT_ORDER, find_project_key_by_name, get_project, list_projects


def test_list_projects_contains_exactly_six_projects_in_declared_order():
    projects = list_projects()

    assert [project["key"] for project in projects] == PROJECT_ORDER
    assert projects == [
        {"key": "kamkor", "name": "Қамқор", "direction": "волонтёрство и экология"},
        {
            "key": "enbegi_adal_zhas_oren",
            "name": "Еңбегі адал – жас өрен",
            "direction": "профориентация и мотивация к труду",
        },
        {"key": "smart_bala", "name": "Smart Bala", "direction": "развитие IT-компетенций"},
        {"key": "shabyt", "name": "Шабыт", "direction": "духовное развитие через искусство"},
        {"key": "ushkyr_oi_alany", "name": "Ұшқыр ой алаңы", "direction": "дебаты и интеллектуальные игры"},
        {"key": "balalar_kitapkhanasy", "name": "Балалар кітапханасы", "direction": "читательская грамотность"},
    ]


def test_project_lookup_and_name_search_do_not_invent_projects():
    assert get_project("smart_bala")["name"] == "Smart Bala"
    assert get_project("onegeli_15_minut") is None
    assert find_project_key_by_name("  ҰШҚЫР   ОЙ   АЛАҢЫ ") == "ushkyr_oi_alany"
    assert find_project_key_by_name("Өнегелі 15 минут") is None
