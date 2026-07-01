"""
Здесь живут все кнопки (клавиатуры) бота.
Держим их отдельно, чтобы main.py не разрастался.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNEL_URL, MANAGER_USERNAME


def main_menu() -> InlineKeyboardMarkup:
    """Главное меню KROOG."""
    builder = InlineKeyboardBuilder()
    # Каждая кнопка: текст для человека + callback_data (скрытая метка для бота)
    builder.button(text="🎶 Заказать пластинку", callback_data="menu_order")
    builder.button(text="ℹ️ О компании", callback_data="menu_about")
    builder.button(text="🛠 Техрекомендации", callback_data="menu_tech")
    # Связь с менеджером (перед Instagram):
    # если задан username — ведём прямо в чат менеджера;
    # если нет — клиент задаёт вопрос через бота (menu_contact).
    if MANAGER_USERNAME:
        builder.button(text="💬 Связь с менеджером", url=f"https://t.me/{MANAGER_USERNAME}")
    else:
        builder.button(text="💬 Связь с менеджером", callback_data="menu_contact")
    # Наш Telegram-канал (над Instagram). Показываем, если ссылка задана.
    if CHANNEL_URL:
        builder.button(text="📣 Наш канал", url=CHANNEL_URL)
    # Кнопка-ссылка: открывает Instagram (url вместо callback_data)
    builder.button(text="📷 Наш Instagram", url="https://instagram.com/kroogvinyl")
    # adjust(1) — по одной кнопке в ряд (каждая на своей строке)
    builder.adjust(1)
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    """Одна кнопка «Назад» — вернуться в главное меню."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В меню", callback_data="menu_main")
    return builder.as_markup()


# ---------- КНОПКИ ЗАКАЗА ----------

def order_colors(colors) -> InlineKeyboardMarkup:
    """Кнопки выбора цвета — строятся из тех цветов, что есть на складе."""
    builder = InlineKeyboardBuilder()
    # Первой — кнопка с фото всех цветов, чтобы выбирать не вслепую
    builder.button(text="📸 Фото цветов", callback_data="order_colors_photo")
    for color in colors:
        emoji = color["emoji"] + " " if color["emoji"] else ""
        builder.button(
            text=f"{emoji}{color['name']} ({color['stock']} шт)",
            callback_data=f"order_color_{color['id']}",
        )
    builder.button(text="✖️ Отмена", callback_data="order_cancel")
    builder.adjust(1)  # по одной кнопке в ряд
    return builder.as_markup()


def order_yes_no(prefix: str) -> InlineKeyboardMarkup:
    """Универсальные кнопки Да/Нет. prefix задаёт, к какому шагу они относятся."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=f"{prefix}_yes")
    builder.button(text="❌ Нет", callback_data=f"{prefix}_no")
    builder.button(text="✖️ Отмена", callback_data="order_cancel")
    builder.adjust(2, 1)
    return builder.as_markup()


def order_confirm() -> InlineKeyboardMarkup:
    """Кнопки финального подтверждения заказа."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📨 Оформить заказ", callback_data="order_confirm")
    builder.button(text="✖️ Отмена", callback_data="order_cancel")
    builder.adjust(1)
    return builder.as_markup()
