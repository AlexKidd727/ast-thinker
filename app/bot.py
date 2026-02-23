# -*- coding: utf-8 -*-
"""Aiogram 3.x бот: при сообщении выходит из режима саморазмышления и отвечает (без HTML-разметки)."""
import logging
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

from app import db
from app.llm import reply_to_user

logger = logging.getLogger(__name__)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def run_bot():
    if not TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN не задан, бот не запущен.")
        return
    bot = Bot(token=TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(msg: Message):
        await msg.answer("Я AST-Thinker. Напиши мне — учту свои размышления и память. Без HTML/разметки.")

    @dp.message(F.text)
    async def on_text(msg: Message):
        text = (msg.text or "").strip()
        if not text:
            return
        try:
            db.add_chat_message("user", text)
            reply = reply_to_user(text, None)
            db.add_chat_message("assistant", reply or "Нет ответа.")
            await msg.answer(reply or "Нет ответа.")
        except Exception as e:
            logger.exception("Ошибка обработки сообщения: %s", e)
            await msg.answer("Ошибка при обработке. Попробуй позже.")

    async def main():
        await dp.start_polling(bot)
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    from app.db import init_db
    init_db()
    run_bot()
