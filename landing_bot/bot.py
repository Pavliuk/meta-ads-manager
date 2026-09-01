"""Лендинг-бот: приймає трафік з реклами (?start=fb_ads / ?start=ig_ads) і показує конверсію.

Замикає цикл перевірки в межах цього ж проєкту: кампанія в Meta Ads Manager
(створена через CLI `meta_ads`) веде на посилання виду
`https://t.me/<бот>?start=fb_ads`; цей бот фіксує джерело кожного нового
користувача, а /traffic показує розподіл.
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from tabulate import tabulate

from landing_bot import db
from landing_bot.config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    source = command.args[:64] if command.args else None
    is_new = db.save_user_if_new(message.from_user.id, message.from_user.username, source)

    if is_new and source:
        await message.answer(f"👋 Привіт! Бачу, що ти прийшов з реклами: <b>{source}</b>.")
    else:
        await message.answer("👋 Привіт! Дякую, що завітав.")


@dp.message(Command("traffic"))
async def cmd_traffic(message: Message, admin_ids: list[int]):
    if message.from_user is None or message.from_user.id not in admin_ids:
        return
    stats = db.acquisition_stats()
    if not stats:
        await message.answer("Поки що немає жодного користувача.")
        return
    table = tabulate(stats, headers=["джерело", "користувачів"])
    await message.answer(f"<b>Джерела трафіку:</b>\n<pre>{table}</pre>")


async def main() -> None:
    config = load_config()
    db.init_db()
    dp["admin_ids"] = config.admin_ids

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    logger.info("Лендинг-бот запускається...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
