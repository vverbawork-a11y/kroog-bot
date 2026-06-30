"""
Админ-команды — работают ТОЛЬКО у владельца (ADMIN_ID из config.py).
Нужны, чтобы смотреть склад и докидывать заготовки, когда они приходят.

Команды:
  /sklad              — показать все цвета, их номера и остатки
  /add <номер> <кол>  — ДОБАВИТЬ заготовки к цвету (например: /add 1 20)
  /set <номер> <кол>  — задать ТОЧНЫЙ остаток (например: /set 1 60)
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import database
from config import ADMIN_ID

# Этот роутер реагирует только на сообщения от админа —
# фильтр F.from_user.id == ADMIN_ID стоит на всём роутере сразу.
router = Router()
router.message.filter(F.from_user.id == ADMIN_ID)


def colors_overview() -> str:
    """Список всех цветов с номерами и остатками."""
    lines = ["📦 <b>Склад</b>\n"]
    for color in database.get_all_colors():
        emoji = color["emoji"] + " " if color["emoji"] else ""
        lines.append(f"<b>{color['id']}</b>. {emoji}{color['name']} — {color['stock']} шт")
    lines.append(
        "\nПополнить: <code>/add номер количество</code>\n"
        "Задать точно: <code>/set номер количество</code>\n"
        "Пример: <code>/add 1 20</code> — добавит 20 шт цвету №1"
    )
    return "\n".join(lines)


@router.message(Command("sklad"))
async def show_stock(message: Message) -> None:
    await message.answer(colors_overview())


@router.message(Command("add"))
async def add_stock_cmd(message: Message, command: CommandObject) -> None:
    parsed = _parse_two_numbers(command.args)
    if parsed is None:
        await message.answer("Формат: <code>/add номер количество</code>\nНапример: /add 1 20")
        return
    color_id, quantity = parsed
    color = database.get_color(color_id)
    if color is None:
        await message.answer(f"Цвета с номером {color_id} нет. Посмотрите /sklad")
        return
    database.add_stock(color_id, quantity)
    new_color = database.get_color(color_id)
    await message.answer(
        f"✅ Добавлено {quantity} шт к «{color['name']}».\n"
        f"Было {color['stock']} → стало <b>{new_color['stock']} шт</b>."
    )


@router.message(Command("set"))
async def set_stock_cmd(message: Message, command: CommandObject) -> None:
    parsed = _parse_two_numbers(command.args)
    if parsed is None:
        await message.answer("Формат: <code>/set номер количество</code>\nНапример: /set 1 60")
        return
    color_id, quantity = parsed
    color = database.get_color(color_id)
    if color is None:
        await message.answer(f"Цвета с номером {color_id} нет. Посмотрите /sklad")
        return
    database.set_stock(color_id, quantity)
    await message.answer(
        f"✅ Остаток «{color['name']}» теперь <b>{quantity} шт</b> "
        f"(было {color['stock']})."
    )


def _parse_two_numbers(args: str | None):
    """Разбирает строку вида «1 20» в (1, 20). Возвращает None, если не вышло."""
    if not args:
        return None
    parts = args.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]), int(parts[1])
