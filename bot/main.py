"""
bot/main.py — точка входа Telegram-бота (блок Б8).

Зачем модуль: единственное место, где создаются Bot/Dispatcher, где
воркер очереди (core.queue.QueueWorker, блок Б7) запускается в том же
процессе через asyncio.create_task, и где стоит глобальный обработчик
ошибок — падение одного хендлера не должно ронять процесс бота.

Что осознанно не делает: не содержит логики команд (bot/handlers.py) и
не решает, что писать пользователю (bot/texts.py) — здесь только
сборка процесса: бот + воркер + обработка ошибок + long polling.

На что опирается: aiogram 3 (long polling, без вебхуков — так проще
для одного Mac без белого IP), core.config.settings, core.queue.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from bot import texts
from bot.handlers import (
    make_generate_ksp_handler,
    make_generate_ktp_handler,
    make_parse_ksp_handler,
    router,
)
from core.config import settings
from core.queue import QueueWorker

logger = logging.getLogger(__name__)


async def _notify_user(bot: Bot, chat_id: int, text: str) -> None:
    """Callback для QueueWorker (блок Б7) — гарантированное уведомление
    о задаче, окончательно ушедшей в failed (KPI, MASTER.md п.1.7)."""
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("не удалось отправить уведомление о провале задачи в чат %s", chat_id)


async def _register_bot_commands(bot: Bot) -> None:
    """М1.1 (PLAN_STAGE2.md): без этого вызова команды не появляются в
    подсказках Telegram — пользователь обязан помнить их наизусть. Список
    берётся из texts.BOT_COMMANDS (единственное место с текстом команд,
    PLAN_STAGE1.md Б8.1) и передаётся одним вызовом: set_my_commands
    перезаписывает список целиком, а не дополняет уже выставленный."""
    await bot.set_my_commands(
        [BotCommand(command=name, description=description) for name, description in texts.BOT_COMMANDS]
    )


def _failure_message(task: dict, error: str) -> str:
    type_label = texts.TASK_TYPE_LABELS.get(task.get("type"), task.get("type"))
    return texts.ERROR_QUEUE_TASK_FAILED.format(type_label=type_label, error=error)


async def _global_error_handler(event: ErrorEvent, bot: Bot) -> bool:
    """Б8.1, КГ: падение любого хендлера не должно уронить процесс, и
    пользователь не должен видеть трейсбек в чате. aiogram сам ловит
    исключения хендлеров и присылает их сюда — если бы этого
    обработчика не было, aiogram бы просто залогировал и продолжил
    работу, но пользователь не получил бы вообще никакого ответа.

    bot: Bot — не событие, а внедряемая зависимость: aiogram передаёт
    её в контексте каждого вызова (тот же Bot, что и в dp.start_polling),
    ErrorEvent сам по себе (update + exception) ссылки на Bot не несёт."""
    logger.exception(
        "необработанное исключение при обработке update %s: %s",
        event.update.update_id,
        event.exception,
        exc_info=event.exception,
    )

    chat_id = None
    update = event.update
    if update.message:
        chat_id = update.message.chat.id
    elif update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id

    if chat_id is not None:
        try:
            await bot.send_message(chat_id, texts.ERROR_UNEXPECTED)
        except Exception:
            logger.exception("не удалось сообщить пользователю об ошибке в чат %s", chat_id)

    return True  # исключение обработано, aiogram не должен пробрасывать его дальше


def _log_worker_death(worker_task: asyncio.Task) -> None:
    """Воркер очереди живёт в отдельной asyncio-задаче. Если она умрёт,
    исключение осядет внутри Task и не всплывёт никуда: бот продолжит
    отвечать на команды и класть задачи в очередь, которую уже некому
    разбирать. Снаружи это выглядит как «бот работает, но /generate
    ничего не присылает», а watchdog (scripts/watchdog.sh) такого не
    видит — процесс-то жив.

    Сам цикл воркера теперь ловит свои ошибки и не умирает (core/queue.py),
    так что сюда мы попадаем либо при штатной остановке, либо при чём-то
    совсем неожиданном. Во втором случае это должно быть видно в логе
    сразу, а не выясняться через неделю по молчащей очереди.
    """
    if worker_task.cancelled():
        return
    exception = worker_task.exception()
    if exception is not None:
        logger.critical(
            "воркер очереди остановился с ошибкой — очередь больше не разбирается, "
            "нужен перезапуск бота",
            exc_info=exception,
        )


async def run() -> None:
    # parse_mode=None — намеренно, не забытая настройка. С Markdown (как
    # было раньше) любое сообщение с сырым текстом пользователя —
    # темой урока, разделом, классом, именем файла, текстом исключения —
    # падало с TelegramBadRequest "can't find end of the entity" на
    # первом же непарном _, *, ` или [ в этом тексте (обнаружено вживую
    # на реальном прогоне: сообщение с подтверждением /generate не
    # уходило вообще). Ни один текст в bot/texts.py фактически не
    # использует bold/code-разметку — Markdown-парсинг был чистым риском
    # без пользы, отключён целиком, а не заэкранирован по каждому месту
    # подстановки (мест много, и одно из них уже пропустили).
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.error.register(_global_error_handler)

    worker = QueueWorker(
        handlers={
            "parse_ksp": make_parse_ksp_handler(bot),
            "generate_ksp": make_generate_ksp_handler(bot),
            "generate_ktp": make_generate_ktp_handler(bot),
        },
        notify=lambda chat_id, text: _notify_user(bot, chat_id, text),
        failure_message=_failure_message,
    )

    worker_task = asyncio.create_task(worker.run_forever())
    worker_task.add_done_callback(_log_worker_death)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await _register_bot_commands(bot)
        await dp.start_polling(bot)
    finally:
        worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


def main() -> None:
    # М0.2 (PLAN_STAGE2.md): понижение уровня логгера httpx переехало в
    # core.config._setup_logging — оно выполняется при импорте core.config
    # для всех точек входа (бот, веб-API, скрипты), а не только здесь.
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("бот остановлен (Ctrl+C)")


if __name__ == "__main__":
    main()
