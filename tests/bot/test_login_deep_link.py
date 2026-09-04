"""
tests/bot/test_login_deep_link.py — вход в кабинет подтверждается в боте.

Главный тест файла — про порядок регистрации хендлеров (грабля 2.4).
aiogram матчит хендлеры сверху вниз, и обычный /start, зарегистрированный
раньше, съел бы «/start vhod_КОД» целиком. Ошибка при этом ничего не
роняет: бот вежливо отвечает приветствием, а вход на сайте просто никогда
не подтверждается. Ровно этот класс ошибки в проекте уже срабатывал
дважды, поэтому проверяется он не глазами.
"""

from core.login_codes import DEEP_LINK_PREFIX, выдать_код, забрать_подтверждённый, подтвердить
from core.db import query


def _позиция(имя: str) -> int:
    """Индекс хендлера в router.message — то, в каком порядке их смотрит aiogram."""
    from bot.handlers import router

    for индекс, хендлер in enumerate(router.message.handlers):
        if хендлер.callback.__name__ == имя:
            return индекс
    raise AssertionError(f"хендлер {имя} не зарегистрирован вовсе")


def test_вход_по_ссылке_разбирается_раньше_обычного_start():
    """Грабля 2.4. Перепутать порядок — значит молча выключить вход."""
    assert _позиция("cmd_start_login") < _позиция("cmd_start"), (
        "«/start vhod_КОД» перехватит обычный /start — подтверждение входа "
        "перестанет доходить, и никакой ошибки при этом не будет"
    )


def test_ссылка_собирается_с_тем_же_префиксом_что_разбирает_бот():
    """Префикс задан один раз в core/login_codes и используется обеими
    сторонами. Разъехавшись, они дали бы вход, который не подтверждается."""
    import inspect

    from bot import handlers

    исходник = inspect.getsource(handlers.cmd_start_login)
    assert "DEEP_LINK_PREFIX" in исходник, "префикс в боте зашит строкой, а не взят из модуля"
    assert DEEP_LINK_PREFIX == "vhod_"


async def test_подтверждение_кнопкой_отмечает_код():
    """Подтверждает именно тот аккаунт, от которого пришло нажатие.

    Личность здесь не проверяется и проверять её нечем — её уже
    подтвердил Telegram: сообщение пришло от конкретного аккаунта.
    """
    from bot.handlers import login_confirmed
    from tests.bot.test_bot_handlers import FakeCallbackQuery, FakeMessage

    код = выдать_код()["код"]
    callback = FakeCallbackQuery(data=f"login_ok:{код}", message=FakeMessage(), user_id=5150)
    await login_confirmed(callback)

    строка = забрать_подтверждённый(код)
    assert строка is not None, "нажатие «Это я» не подтвердило код"
    assert строка["telegram_user_id"] == 5150


async def test_отказ_не_подтверждает_и_не_гасит_код():
    """«Это не я» не должно ни пускать, ни давать сигнал отправителю.

    Гасить код здесь было бы удобно, но тогда тот, кто прислал чужую
    ссылку, мгновенно узнаёт, что человек нажал «это не я». Пусть
    протухнет сам, через пять минут.
    """
    from bot.handlers import login_rejected
    from tests.bot.test_bot_handlers import FakeCallbackQuery, FakeMessage

    код = выдать_код()["код"]
    callback = FakeCallbackQuery(data=f"login_no:{код}", message=FakeMessage(), user_id=5151)
    await login_rejected(callback)

    assert забрать_подтверждённый(код) is None, "отказ подтвердил вход"
    assert query("SELECT status FROM login_codes WHERE code = ?", (код,))[0]["status"] == "pending"


async def test_мёртвая_ссылка_отвечает_понятно_и_одинаково():
    """Причину не называем: «просрочен», «использован» и «не найден»
    человеку одинаково означают «начните заново», а перебирающему коды
    рассказывали бы, какой из них существовал."""
    from bot import texts
    from bot.handlers import cmd_start_login
    from tests.bot.test_bot_handlers import FakeMessage, _state

    код = выдать_код()["код"]
    подтвердить(код, 1)
    забрать_подтверждённый(код)  # код стал использованным

    message = FakeMessage(text=f"/start {DEEP_LINK_PREFIX}{код}", user_id=5152)
    await cmd_start_login(message, _state())
    assert message.sent[-1]["text"] == texts.LOGIN_CODE_INVALID

    несуществующий = FakeMessage(text=f"/start {DEEP_LINK_PREFIX}нетакого", user_id=5152)
    await cmd_start_login(несуществующий, _state())
    assert несуществующий.sent[-1]["text"] == texts.LOGIN_CODE_INVALID


async def test_в_подтверждении_показаны_контрольные_знаки():
    """Без них человеку нечего сверять с сайтом, и защита от чужой
    ссылки превращается в «нажмите ок»."""
    from bot.handlers import cmd_start_login
    from core.login_codes import контрольные_знаки
    from tests.bot.test_bot_handlers import FakeMessage, _state

    код = выдать_код()["код"]
    message = FakeMessage(text=f"/start {DEEP_LINK_PREFIX}{код}", user_id=5153)
    await cmd_start_login(message, _state())

    текст = message.sent[-1]["text"]
    assert контрольные_знаки(код) in текст
    assert "телефон" not in текст.lower() or "не понадоб" in текст.lower()
