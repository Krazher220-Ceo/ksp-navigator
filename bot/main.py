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
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent

from bot import texts
from bot.handlers import make_generate_ksp_handler, make_parse_ksp_handler, router
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


async def run() -> None:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.error.register(_global_error_handler)

    worker = QueueWorker(
        handlers={
            "parse_ksp": make_parse_ksp_handler(bot),
            "generate_ksp": make_generate_ksp_handler(bot),
        },
        notify=lambda chat_id, text: _notify_user(bot, chat_id, text),
        failure_message=_failure_message,
    )

    worker_task = asyncio.create_task(worker.run_forever())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
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
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не шуметь HTTP-логами LLM-клиента поверх бота
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("бот остановлен (Ctrl+C)")


if __name__ == "__main__":
    main()
