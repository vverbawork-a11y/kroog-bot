"""Telegram Mini App: catalog, durable reservations and manager notifications."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qsl, quote

from aiohttp import web

import artwork
import calculator as prices
import config
import database

ROOT = Path(__file__).resolve().parent
APP = ROOT / "miniapp"
PHOTOS = ROOT / "photos"


def setup():
    with closing(database.get_connection()) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS shop_orders (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
            color_id INTEGER NOT NULL, quantity INTEGER NOT NULL, details TEXT NOT NULL,
            cancelled INTEGER NOT NULL DEFAULT 0, notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, request_id))""")


def identity(request):
    try:
        pairs = parse_qsl(request.headers.get("X-Telegram-Init-Data", ""), strict_parsing=True)
        fields = dict(pairs)
        if len(pairs) != len(fields) or not config.BOT_TOKEN:
            raise ValueError
        given = fields.pop("hash")
        signed = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        key = hmac.digest(b"WebAppData", config.BOT_TOKEN.encode(), "sha256")
        expected = hmac.new(key, signed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, given) or not -30 <= time.time()-int(fields["auth_date"]) <= 86400:
            raise ValueError
        user = json.loads(fields["user"])
        if type(user["id"]) is not int or user["id"] <= 0:
            raise ValueError
        return user
    except (ValueError, KeyError, TypeError):
        raise web.HTTPUnauthorized(text="Откройте приложение заново из Telegram.")


def admin(request):
    if identity(request)["id"] != config.ADMIN_ID:
        raise web.HTTPForbidden(text="Только для менеджера.")


def photo(name):
    key = name.lower().replace("ё","е").replace("«","").replace("»","").replace(" ","_")
    for path in PHOTOS.glob("*.jpg"):
        if path.stem.lower() == key:
            return "/photos/" + quote(path.name)
    return None


async def catalog(request):
    user = identity(request)
    owner = user["id"] == config.ADMIN_ID
    rows = database.get_all_colors() if owner else database.get_available_colors()
    return web.json_response({
        "admin": owner, "channel": config.CHANNEL_URL,
        "contact": "@"+user["username"] if user.get("username") else "",
        "colors": [dict(row, photo=photo(row["name"]),
                        price=prices.PRICE_BLACK if row["is_black"] else prices.PRICE_COLOR) for row in rows],
        "sleeve": prices.PRICE_ENVELOPE, "label": prices.PRICE_LABEL,
        "rush": prices.URGENT_SURCHARGE_PERCENT,
        "discounts": [[n, prices.discount_percent(n)] for n in (50,25,10,5)]
    })


async def payload(request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text="Не удалось прочитать заявку.")


def number(data, key, minimum):
    n = data.get(key)
    if type(n) is not int or not minimum <= n <= 1000000:
        raise web.HTTPBadRequest(text="Проверьте количество.")
    return n


async def place(request):
    user = identity(request)
    if not config.ADMIN_ID:
        raise web.HTTPServiceUnavailable(text="Приём заказов временно закрыт.")
    data, files, stage = await artwork.receive(request)
    saved = None
    committed = False
    try:
        qty = number(data, "quantity", 1)
        color_id = number(data, "color", 1)
        if any(type(data.get(k)) is not bool for k in ("sleeve", "label", "rush")):
            raise web.HTTPBadRequest(text="Проверьте параметры заказа.")
        if any(files.get(k) for k in ("sleeve_front", "sleeve_back")) and not data["sleeve"]:
            raise web.HTTPBadRequest(text="Включите печать конверта для приложенных макетов.")
        if any(files.get(k) for k in ("label_a", "label_b")) and not data["label"]:
            raise web.HTTPBadRequest(text="Включите печать яблока для приложенных макетов.")
        contact = data.get("contact")
        if not isinstance(contact, str) or not 2 <= len(contact.strip()) <= 120:
            raise web.HTTPBadRequest(text="Укажите контакт для менеджера.")
        try:
            request_id = str(uuid.UUID(str(data.get("request_id"))))
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Откройте форму заново.")
        with closing(database.get_connection()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute(
                    "SELECT id FROM shop_orders WHERE user_id=? AND request_id=?",
                    (user["id"], request_id)
                ).fetchone()
                if old:
                    db.rollback()
                    return web.json_response({"id": old["id"]})
                color = db.execute("SELECT * FROM colors WHERE id=?", (color_id,)).fetchone()
                if not color or color["stock"] < qty:
                    raise web.HTTPConflict(text="Остаток изменился. Выберите доступное количество.")
                totals = prices.calculate(color["is_black"], qty, data["sleeve"], data["label"], data["rush"])
                details = dict(
                    color=color["name"], contact=contact.strip(), user_id=user["id"],
                    name=" ".join(filter(None, (user.get("first_name"), user.get("last_name")))),
                    sleeve=data["sleeve"], label=data["label"], rush=data["rush"],
                    files=files, **totals
                )
                db.execute("UPDATE colors SET stock=stock-? WHERE id=?", (qty, color_id))
                order = db.execute(
                    "INSERT INTO shop_orders(user_id,request_id,color_id,quantity,details) VALUES(?,?,?,?,?)",
                    (user["id"], request_id, color_id, qty, json.dumps(details, ensure_ascii=False))
                )
                saved = artwork.finalize(stage, order.lastrowid, files)
                db.commit()
                committed = True
                return web.json_response({"id": order.lastrowid}, status=201)
            except Exception:
                db.rollback()
                raise
    finally:
        artwork.discard(stage)
        if not committed and saved:
            artwork.discard(saved)


async def stock(request):
    admin(request)
    data = await payload(request)
    with closing(database.get_connection()) as db, db:
        result = db.execute("UPDATE colors SET stock=? WHERE id=? AND stock=?",
                            (number(data,"stock",0), number(data,"color",1), number(data,"previous",0)))
        if not result.rowcount:
            raise web.HTTPConflict(text="Остаток уже изменился. Проверьте новое значение.")
    return web.json_response({"ok":True})


async def manager_order(request):
    admin(request)
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM shop_orders WHERE id=?", (request.match_info["id"],)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="Заказ не найден.")
        cancelled = bool(row["cancelled"])
        if request.method == "POST" and not cancelled:
            db.execute("UPDATE colors SET stock=stock+? WHERE id=?", (row["quantity"],row["color_id"]))
            db.execute("UPDATE shop_orders SET cancelled=1 WHERE id=?", (row["id"],))
            cancelled = True
        return web.json_response(dict(json.loads(row["details"]), id=row["id"], cancelled=cancelled))


async def download_artwork(request):
    admin(request)
    order_id = int(request.match_info["id"])
    field = request.match_info["field"]
    with closing(database.get_connection()) as db:
        row = db.execute("SELECT details FROM shop_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise web.HTTPNotFound(text="Заказ не найден.")
    details = json.loads(row["details"])
    path, original = artwork.stored_file(order_id, details.get("files", {}), field)
    return web.FileResponse(path, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(original),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


async def notify_orders(bot):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    while True:
        with closing(database.get_connection()) as db:
            orders = db.execute("SELECT id,details FROM shop_orders WHERE notified=0 AND cancelled=0 ORDER BY id LIMIT 20").fetchall()
        for order in orders:
            o = json.loads(order["details"])
            ref = order["id"]
            options = ", ".join(title for k,title in (("sleeve","конверт"),("label","яблоко"),("rush","ускорение")) if o[k]) or "без доплат"
            message = (f"Заказ №{ref}\n{o['color']} × {o['quantity']}\n{options}\n"
                       f"Итого {o['total']} ₽\n{o['name']} · {o['contact']}")
            message += f"\nМакеты прикреплены: {len(o.get('files', {}))}" if o["sleeve"] or o["label"] else ""
            link = WebAppInfo(url=f"{config.MINIAPP_URL}/?order={ref}")
            try:
                await bot.send_message(config.ADMIN_ID, message, parse_mode=None,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть заказ",web_app=link)]]))
            except Exception as exc:
                logging.warning("Manager notification %s pending (%s)",ref,type(exc).__name__)
                break
            with closing(database.get_connection()) as db, db:
                db.execute("UPDATE shop_orders SET notified=1 WHERE id=?", (ref,))
        await asyncio.sleep(5)


def application():
    app = web.Application(client_max_size=90 * 1024 * 1024)
    async def home(request):
        return web.FileResponse(APP / "index.html")
    app.router.add_get("/", home)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_post("/api/orders", place)
    app.router.add_post("/api/stock", stock)
    app.router.add_get(r"/api/orders/{id:\d+}", manager_order)
    app.router.add_post(r"/api/orders/{id:\d+}/cancel", manager_order)
    app.router.add_get(r"/api/orders/{id:\d+}/artwork/{field}", download_artwork)
    app.router.add_static("/assets", APP / "assets")
    app.router.add_static("/photos", PHOTOS)
    app.router.add_static("/templates", ROOT / "templates")
    return app
