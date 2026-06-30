"""
KROOG bot — точка входа.
Сейчас умеет: показывать главное меню, разделы «О компании» и «Техрекомендации».
Заказ и цвета подключим следующими шагами.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message

import admin
import database
import order
import texts
from config import BOT_TOKEN
from keyboards import back_to_menu, main_menu

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
dp.include_router(admin.router)  # админ-команды склада (только для владельца)
dp.include_router(order.router)  # подключаем сценарий заказа


# ---------- ГЛАВНОЕ МЕНЮ ----------

@dp.message(CommandStart())
async def start_handler(message: Message) -> None:
    """Команда /start — показываем приветствие и меню."""
    await message.answer(texts.WELCOME, reply_markup=main_menu())


@dp.callback_query(F.data == "menu_main")
async def back_to_main_handler(callback: CallbackQuery) -> None:
    """Кнопка «В меню» — возвращаемся в главное меню."""
    # edit_text меняет уже отправленное сообщение, а не шлёт новое
    await callback.message.edit_text(texts.WELCOME, reply_markup=main_menu())
    await callback.answer()  # убирает «часики» на кнопке


# ---------- РАЗДЕЛЫ МЕНЮ ----------

@dp.callback_query(F.data == "menu_about")
async def about_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(texts.ABOUT, reply_markup=back_to_menu())
    await callback.answer()


@dp.callback_query(F.data == "menu_tech")
async def tech_handler(callback: CallbackQuery) -> None:
    await callback.message.edit_text(texts.TECH, reply_markup=back_to_menu())
    await callback.answer()


@dp.message(Command("id"))
async def id_handler(message: Message) -> None:
    """Показывает Telegram ID — нужно, чтобы вписать ADMIN_ID в config.py."""
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "Перешлите это число разработчику для настройки заявок."
    )


# ---------- ЗАПУСК ----------

async def setup_bot_profile(bot: Bot) -> None:
    """Описание бота (видно до «Запустить») и меню команд."""
    # Команда /start в синем меню рядом с полем ввода. Админские команды
    # сюда НЕ добавляем — чтобы клиенты их не видели (они и так работают).
    await bot.set_my_commands([
        BotCommand(command="start", description="Открыть меню KROOG"),
    ])
    # Короткое описание — в профиле бота
    await bot.set_my_short_description(
        "Заказ виниловых пластинок 12\" с вашей музыкой."
    )
    # Полное описание — на пустом экране чата, рядом с кнопкой «Запустить»
    await bot.set_my_description(
        "Привет! Это KROOG 🎶\n"
        "Здесь можно заказать виниловую пластинку 12\" с вашей музыкой.\n\n"
        "Нажмите «Запустить», чтобы открыть меню."
    )


async def main() -> None:
    database.init_db()  # создаём таблицу и стартовые цвета при запуске
    bot = Bot(
        token=BOT_TOKEN,
        # Чтобы <b>жирный</b> и <i>курсив</i> в текстах работали автоматически
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await setup_bot_profile(bot)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
