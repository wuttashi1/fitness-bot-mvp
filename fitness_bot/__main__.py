import argparse
import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot
from dotenv import load_dotenv

from .bot import run
from .db import Database
from .service import Service


async def identify(token):
    # Bootstrap mode needs only BOT_TOKEN; it never initializes or assigns program ownership.
    from aiogram import Dispatcher, Router
    from aiogram.types import Message

    router = Router()

    @router.message()
    async def id_message(message: Message):
        if message.chat.type == "private" and message.from_user:
            await message.answer(
                f"Ваш Telegram ID: {message.from_user.id}\nУкажите его в OWNER_TELEGRAM_ID и перезапустите бот."
            )

    dp = Dispatcher()
    dp.include_router(router)
    async with Bot(token) as bot:
        await dp.start_polling(bot, close_bot_session=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--init-db", action="store_true", help="Initialize schema and Excel seed without Telegram"
    )
    parser.add_argument(
        "--identify", action="store_true", help="Token-only bootstrap: reply with your Telegram ID"
    )
    args = parser.parse_args()
    # The installed package lives in site-packages; configuration belongs to the working directory.
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("BOT_TOKEN", "")
    if args.identify:
        if not token:
            parser.error("Set BOT_TOKEN in .env")
        return asyncio.run(identify(token))
    try:
        owner_id = int(os.getenv("OWNER_TELEGRAM_ID", "0"))
        if owner_id <= 0:
            raise ValueError
    except ValueError:
        parser.error("Set YOUR numeric OWNER_TELEGRAM_ID. Use --identify with BOT_TOKEN to find it.")
    hour = int(os.getenv("CHECKIN_HOUR", "10"))
    interval = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
    if not 0 <= hour <= 23 or not 1 <= interval <= 60:
        parser.error("CHECKIN_HOUR: 0–23; POLL_INTERVAL_SECONDS: 1–60")
    if not args.init_db and not token:
        parser.error("Set BOT_TOKEN in .env")
    service = Service(
        Database(os.getenv("DATABASE_PATH", "./var/fitness.sqlite3")),
        owner_id,
        os.getenv("OWNER_DISPLAY_NAME", ""),
        os.getenv("DEFAULT_TIMEZONE", "Europe/Berlin"),
        hour,
    )
    if args.init_db:
        print("Database and Excel starter seed initialized.")
        return

    async def start():
        async with Bot(token) as bot:
            await run(bot, service, interval)

    asyncio.run(start())


if __name__ == "__main__":
    main()
