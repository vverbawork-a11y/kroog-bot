"""
Сценарий оформления заказа (пошаговый диалог через FSM).

Шаги (состояния):
  цвет → тираж → конверт? [→ макет конверта] → яблоко? [→ макет яблока]
       → аудио → подтверждение → заявка
"""

import os

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

import calculator
import database
from config import ADMIN_ID, TEMPLATES_URL
from keyboards import back_to_menu, main_menu, order_colors, order_confirm, order_yes_no
from texts import WELCOME

router = Router()

# Папка с фото цветов пластинок (кладём картинки сюда)
PHOTOS_DIR = "photos"


def templates_note() -> str:
    """Приписка со ссылкой на макеты, если ссылка задана."""
    if TEMPLATES_URL:
        return f"\n\n📐 Скачать шаблоны макетов (конверт и яблоко): {TEMPLATES_URL}"
    return ""


class Order(StatesGroup):
    color = State()
    quantity = State()
    urgent = State()
    envelope = State()
    envelope_design = State()   # ждём макет конверта
    label = State()
    label_design_a = State()    # ждём макет яблока — сторона A
    label_design_b = State()    # ждём макет яблока — сторона B
    audio = State()
    confirm = State()


# ---------- ВСПОМОГАТЕЛЬНОЕ: извлечь присланный материал ----------

def extract_material(message: Message):
    """
    Достаёт из сообщения файл/фото/ссылку.
    Возвращает (type, value, name) или None, если ничего подходящего нет.
    """
    if message.photo:
        return "file_photo", message.photo[-1].file_id, "фото"
    if message.document:
        return "file_doc", message.document.file_id, message.document.file_name or "файл"
    if message.audio:
        return "file_audio", message.audio.file_id, message.audio.file_name or "аудио"
    if message.text and message.text.strip().startswith(("http://", "https://")):
        return "link", message.text.strip(), message.text.strip()
    return None


async def send_material(bot, chat_id: int, material_type: str, value: str, caption: str):
    """Пересылает материал менеджеру в зависимости от его типа."""
    if material_type == "file_photo":
        await bot.send_photo(chat_id, value, caption=caption)
    elif material_type == "file_audio":
        await bot.send_audio(chat_id, value, caption=caption)
    elif material_type == "file_doc":
        await bot.send_document(chat_id, value, caption=caption)
    else:  # link
        await bot.send_message(chat_id, f"{caption}: {value}")


# ---------- ШАГ 1: ВЫБОР ЦВЕТА ----------

@router.callback_query(F.data == "menu_order")
async def start_order(callback: CallbackQuery, state: FSMContext) -> None:
    colors = database.get_available_colors()
    if not colors:
        await callback.message.edit_text(
            "Сейчас нет цветов в наличии 😔 Загляните позже.",
            reply_markup=back_to_menu(),
        )
        await callback.answer()
        return
    await state.set_state(Order.color)
    await callback.message.edit_text(
        "🎨 <b>Шаг 1/6.</b> Выберите цвет пластинки:",
        reply_markup=order_colors(colors),
    )
    await callback.answer()


@router.callback_query(Order.color, F.data == "order_colors_photo")
async def show_color_photos(callback: CallbackQuery, state: FSMContext) -> None:
    """Присылает альбом с фото всех цветов, затем снова кнопки выбора."""
    photos = []
    if os.path.isdir(PHOTOS_DIR):
        for name in sorted(os.listdir(PHOTOS_DIR)):
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                photos.append(os.path.join(PHOTOS_DIR, name))
    if not photos:
        await callback.answer("Фото цветов скоро добавим 🙂", show_alert=True)
        return

    bot = callback.bot
    chat_id = callback.message.chat.id
    # Telegram разрешает до 10 фото в одном альбоме — шлём частями
    for i in range(0, len(photos), 10):
        chunk = photos[i:i + 10]
        media = [
            # Подпись под фото — имя файла без расширения (назови файлы по цвету)
            InputMediaPhoto(media=FSInputFile(p), caption=os.path.splitext(os.path.basename(p))[0])
            for p in chunk
        ]
        await bot.send_media_group(chat_id, media)

    # Снова показываем кнопки выбора цвета — прямо под фото
    colors = database.get_available_colors()
    await bot.send_message(
        chat_id, "🎨 Выберите цвет пластинки:", reply_markup=order_colors(colors)
    )
    await callback.answer()


# ---------- ШАГ 2: ТИРАЖ ----------

@router.callback_query(Order.color, F.data.startswith("order_color_"))
async def choose_color(callback: CallbackQuery, state: FSMContext) -> None:
    color_id = int(callback.data.removeprefix("order_color_"))
    color = database.get_color(color_id)
    if color is None or color["stock"] <= 0:
        await callback.answer("Этот цвет закончился, выберите другой.", show_alert=True)
        return
    await state.update_data(
        color_id=color["id"],
        color_name=color["name"],
        is_black=bool(color["is_black"]),
        stock=color["stock"],
    )
    await state.set_state(Order.quantity)
    await callback.message.edit_text(
        f"Выбран цвет: <b>{color['name']}</b>\n"
        f"На складе: <b>{color['stock']} шт</b>\n\n"
        f"🔢 <b>Шаг 2/6.</b> Напишите нужный тираж (числом, например 10).\n\n"
        f"💰 <b>Скидки за тираж:</b>\n"
        f"• от 5 шт — 5%\n"
        f"• от 10 шт — 10%\n"
        f"• от 25 шт — 15%\n"
        f"• от 50 шт — 20%"
    )
    await callback.answer()


@router.message(Order.quantity)
async def enter_quantity(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Введите тираж числом больше нуля, например <b>10</b>.")
        return
    quantity = int(text)
    data = await state.get_data()
    if quantity > data["stock"]:
        await message.answer(
            f"Столько нет в наличии. Доступно <b>{data['stock']} шт</b>.\n"
            f"Введите число не больше {data['stock']}."
        )
        return
    await state.update_data(quantity=quantity)
    await ask_urgent(message, state)


# ---------- ШАГ 3: СРОЧНОСТЬ ----------

async def ask_urgent(target: Message, state: FSMContext) -> None:
    await state.set_state(Order.urgent)
    await target.answer(
        f"⏱ <b>Шаг 3/6.</b> Обычный срок изготовления — <b>1–2 недели</b> "
        f"(зависит от загрузки).\n\n"
        f"Нужен срочный заказ вне очереди? "
        f"(+{calculator.URGENT_SURCHARGE_PERCENT}% к стоимости)",
        reply_markup=order_yes_no("order_urgent"),
    )


@router.callback_query(Order.urgent, F.data.in_({"order_urgent_yes", "order_urgent_no"}))
async def choose_urgent(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(urgent=callback.data.endswith("_yes"))
    await ask_envelope(callback.message, state)
    await callback.answer()


# ---------- ШАГ 4: КОНВЕРТ ----------

async def ask_envelope(target: Message, state: FSMContext) -> None:
    await state.set_state(Order.envelope)
    await target.answer(
        f"🖼 <b>Шаг 4/6.</b> Добавить печать конверта с вашим дизайном? "
        f"(+{calculator.PRICE_ENVELOPE} ₽ за штуку)",
        reply_markup=order_yes_no("order_env"),
    )


@router.callback_query(Order.envelope, F.data == "order_env_yes")
async def envelope_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(print_envelope=True)
    await state.set_state(Order.envelope_design)
    await callback.message.edit_text(
        "📎 Пришлите макет конверта — файлом в формате <b>JPEG (300 dpi)</b> "
        "или ссылкой на материалы." + templates_note()
    )
    await callback.answer()


@router.callback_query(Order.envelope, F.data == "order_env_no")
async def envelope_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(print_envelope=False, envelope_design=None)
    await ask_label(callback.message, state)
    await callback.answer()


@router.message(Order.envelope_design)
async def receive_envelope_design(message: Message, state: FSMContext) -> None:
    material = extract_material(message)
    if material is None:
        await message.answer(
            "Не вижу макета. Пришлите изображение/файл конверта или ссылку (http…)."
        )
        return
    m_type, m_value, m_name = material
    await state.update_data(envelope_design={"type": m_type, "value": m_value, "name": m_name})
    await ask_label(message, state)


# ---------- ШАГ 5: ЯБЛОКО (ЛЕЙБЛ) ----------

async def ask_label(target: Message, state: FSMContext) -> None:
    await state.set_state(Order.label)
    await target.answer(
        f"💿 <b>Шаг 5/6.</b> Добавить печать яблока (лейбла) с вашим дизайном?\n"
        f"<b>{calculator.PRICE_LABEL} ₽ за штуку.</b>\n"
        f"Нужно прислать <b>2 макета</b>: отдельно для стороны A и стороны B.",
        reply_markup=order_yes_no("order_lbl"),
    )


@router.callback_query(Order.label, F.data == "order_lbl_yes")
async def label_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(print_label=True)
    await state.set_state(Order.label_design_a)
    await callback.message.edit_text(
        "📎 Пришлите макет яблока для <b>стороны A</b> — "
        "файлом <b>JPEG (300 dpi)</b> или ссылкой." + templates_note()
    )
    await callback.answer()


@router.callback_query(Order.label, F.data == "order_lbl_no")
async def label_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(print_label=False, label_design_a=None, label_design_b=None)
    await ask_audio(callback.message, state)
    await callback.answer()


@router.message(Order.label_design_a)
async def receive_label_a(message: Message, state: FSMContext) -> None:
    material = extract_material(message)
    if material is None:
        await message.answer("Не вижу макета стороны A. Пришлите файл (JPEG) или ссылку.")
        return
    m_type, m_value, m_name = material
    await state.update_data(label_design_a={"type": m_type, "value": m_value, "name": m_name})
    await state.set_state(Order.label_design_b)
    await message.answer(
        "📎 Теперь макет яблока для <b>стороны B</b> — файлом <b>JPEG (300 dpi)</b> или ссылкой."
    )


@router.message(Order.label_design_b)
async def receive_label_b(message: Message, state: FSMContext) -> None:
    material = extract_material(message)
    if material is None:
        await message.answer("Не вижу макета стороны B. Пришлите файл (JPEG) или ссылку.")
        return
    m_type, m_value, m_name = material
    await state.update_data(label_design_b={"type": m_type, "value": m_value, "name": m_name})
    await ask_audio(message, state)


# ---------- ШАГ 5: АУДИО ----------

async def ask_audio(target: Message, state: FSMContext) -> None:
    await state.set_state(Order.audio)
    await target.answer(
        "🎧 <b>Шаг 6/6.</b> Пришлите ваше аудио — "
        "файлом (WAV/FLAC/AIFF/MP3) <b>или</b> ссылкой (Google Drive, Dropbox и т.п.)."
    )


@router.message(Order.audio)
async def receive_audio(message: Message, state: FSMContext) -> None:
    material = extract_material(message)
    if material is None:
        await message.answer(
            "Не вижу аудио. Пришлите файл (WAV/FLAC/AIFF/MP3) или ссылку (http…)."
        )
        return
    m_type, m_value, m_name = material
    await state.update_data(audio={"type": m_type, "value": m_value, "name": m_name})
    await show_summary(message, state)


# ---------- ИТОГ И ПОДТВЕРЖДЕНИЕ ----------

async def show_summary(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    price = calculator.calculate(
        is_black=data["is_black"],
        quantity=data["quantity"],
        print_envelope=data["print_envelope"],
        print_label=data["print_label"],
        urgent=data["urgent"],
    )
    await state.update_data(total=price["total"])

    envelope = "да (макет приложен)" if data["print_envelope"] else "нет"
    label = "да (2 макета приложены)" if data["print_label"] else "нет"
    urgent = (
        f"да (+{calculator.URGENT_SURCHARGE_PERCENT}%)" if data["urgent"]
        else "нет (1–2 недели)"
    )
    audio = "ссылка" if data["audio"]["type"] == "link" else f"файл ({data['audio']['name']})"
    discount_line = (
        f"Скидка: <b>{price['percent']}%</b> (−{price['discount_rub']} ₽)\n"
        if price["percent"] else ""
    )

    await state.set_state(Order.confirm)
    await message.answer(
        "🧾 <b>Ваш заказ</b>\n\n"
        f"Цвет: <b>{data['color_name']}</b>\n"
        f"Тираж: <b>{data['quantity']} шт</b>\n"
        f"Срочность: {urgent}\n"
        f"Печать конверта: {envelope}\n"
        f"Печать яблока: {label}\n"
        f"Аудио: {audio}\n\n"
        f"Цена за штуку: {price['per_piece']} ₽\n"
        f"Без скидки: {price['subtotal']} ₽\n"
        f"{discount_line}"
        f"<b>Итого к оплате: {price['total']} ₽</b>",
        reply_markup=order_confirm(),
    )


@router.callback_query(Order.confirm, F.data == "order_confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()

    color = database.get_color(data["color_id"])
    if color is None or color["stock"] < data["quantity"]:
        available = color["stock"] if color else 0
        await callback.message.edit_text(
            f"К сожалению, цвет «{data['color_name']}» уже разобрали — "
            f"осталось {available} шт. Оформите заказ заново.",
            reply_markup=back_to_menu(),
        )
        await state.clear()
        await callback.answer()
        return

    database.decrease_stock(data["color_id"], data["quantity"])
    await send_application_to_admin(callback, data)

    await callback.message.edit_text(
        "✅ <b>Заявка принята!</b>\n\n"
        "Менеджер свяжется с вами в личных сообщениях и отправит данные для оплаты.\n\n"
        "Спасибо, что выбрали KROOG 🎶",
        reply_markup=back_to_menu(),
    )
    await state.clear()
    await callback.answer()


async def send_application_to_admin(callback: CallbackQuery, data: dict) -> None:
    """Отправляет менеджеру заявку: текст + макеты + аудио."""
    if not ADMIN_ID:
        return
    user = callback.from_user
    username = f"@{user.username}" if user.username else "username не указан"
    envelope = "да" if data["print_envelope"] else "нет"
    label = "да" if data["print_label"] else "нет"
    urgent = "🔥 ДА" if data["urgent"] else "нет"

    text = (
        "🔔 <b>Новая заявка KROOG</b>\n\n"
        f"Клиент: {user.full_name} ({username})\n"
        f"ID клиента: <code>{user.id}</code>\n\n"
        f"Цвет: <b>{data['color_name']}</b>\n"
        f"Тираж: <b>{data['quantity']} шт</b>\n"
        f"Срочный: {urgent}\n"
        f"Конверт: {envelope}\n"
        f"Яблоко: {label}\n"
        f"<b>Сумма: {data['total']} ₽</b>"
    )
    bot = callback.bot
    await bot.send_message(ADMIN_ID, text)

    # Макеты и аудио — отдельными сообщениями
    if data.get("envelope_design"):
        d = data["envelope_design"]
        await send_material(bot, ADMIN_ID, d["type"], d["value"], "🖼 Макет конверта")
    if data.get("label_design_a"):
        d = data["label_design_a"]
        await send_material(bot, ADMIN_ID, d["type"], d["value"], "💿 Макет яблока — сторона A")
    if data.get("label_design_b"):
        d = data["label_design_b"]
        await send_material(bot, ADMIN_ID, d["type"], d["value"], "💿 Макет яблока — сторона B")
    a = data["audio"]
    await send_material(bot, ADMIN_ID, a["type"], a["value"], "🎧 Аудио клиента")


# ---------- ОТМЕНА ----------

@router.callback_query(F.data == "order_cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Заказ отменён.\n\n" + WELCOME, reply_markup=main_menu()
    )
    await callback.answer()
