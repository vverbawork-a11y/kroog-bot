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
from urllib.parse import parse_qsl, quote, urlsplit

from aiohttp import web

import artwork
import calculator as prices
import colours
import config
import database

ROOT = Path(__file__).resolve().parent
APP = ROOT / "miniapp"
PHOTOS = ROOT / "photos"
RELEASE = "2026-11-08-12"


def setup():
    with closing(database.get_connection()) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS shop_orders (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
            color_id INTEGER NOT NULL, quantity INTEGER NOT NULL, details TEXT NOT NULL,
            cancelled INTEGER NOT NULL DEFAULT 0, notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, request_id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS shop_inquiries (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, color_id INTEGER NOT NULL,
            contact TEXT NOT NULL, request_id TEXT NOT NULL,
            notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, request_id))""")


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
    rows = database.get_all_colors()
    products = []
    for row in rows:
        name = row["name"]
        # Legacy duplicate of Ночной переливающийся. Preserve the existing DB
        # entry for historical order ids, but never list it as a product.
        if name == "Русалка":
            continue
        standard = name in colours.BY_NAME
        # Older colours stay available if present, but unknown old zero-stock
        # placeholder entries do not clutter the supplier catalogue.
        if not standard and not (owner or row["stock"] > 0):
            continue
        meta = colours.record(name, bool(row["is_black"]))
        products.append(dict(
            row, photo=photo(name) if meta["atlas"] is None else None,
            atlas=meta["atlas"], price=meta["price"]
        ))
    products.sort(key=lambda row: (
        0 if row["stock"] > 0 else 1 if row["incoming"] else 2,
        row["id"]
    ))
    return web.json_response({
        "admin": owner, "channel": config.CHANNEL_URL,
        "contact": "@"+user["username"] if user.get("username") else "",
        "colors": products,
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
        # Music plan is optional for compatibility with existing orders.
        rpm = data.get("rpm", 33)
        if type(rpm) is not int or rpm not in (33, 45):
            raise web.HTTPBadRequest(text="Выберите скорость 33⅓ или 45 RPM.")
        tracklists = {}
        for side in ("a", "b"):
            raw = data.get("tracks_" + side, "")
            if not isinstance(raw, str) or len(raw) > 1000:
                raise web.HTTPBadRequest(text="Слишком длинный треклист.")
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if len(lines) > 30:
                raise web.HTTPBadRequest(text="Не больше 30 треков на сторону.")
            tracklists[side] = [
                f"{side.upper()}{i+1} — {line}" for i, line in enumerate(lines)
            ]
        audio_link = data.get("audio_link", "")
        if not isinstance(audio_link, str) or len(audio_link) > 800:
            raise web.HTTPBadRequest(text="Некорректная ссылка на аудио.")
        audio_link = audio_link.strip()
        if audio_link:
            parsed = urlsplit(audio_link)
            if parsed.scheme != "https" or not parsed.hostname:
                raise web.HTTPBadRequest(text="Ссылка на аудио должна начинаться с https://")
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
                totals = prices.calculate(
            color["is_black"], qty, data["sleeve"], data["label"], data["rush"],
            unit_price_override=colours.record(color["name"], bool(color["is_black"]))["price"]
        )
                details = dict(
                    color=color["name"], contact=contact.strip(), user_id=user["id"],
                    name=" ".join(filter(None, (user.get("first_name"), user.get("last_name")))),
                    sleeve=data["sleeve"], label=data["label"], rush=data["rush"],
                    files=files, rpm=rpm, tracklist_a=tracklists["a"],
                    tracklist_b=tracklists["b"], audio_link=audio_link, **totals
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


async def inquiry(request):
    """Interest in unavailable colours: no stock reservation or payment."""
    user = identity(request)
    data = await payload(request)
    color_id = number(data, "color", 1)
    contact = data.get("contact")
    if not isinstance(contact, str) or not 2 <= len(contact.strip()) <= 120:
        raise web.HTTPBadRequest(text="Оставьте контакт, чтобы ответить о наличии.")
    try:
        request_id = str(uuid.UUID(str(data.get("request_id"))))
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text="Откройте форму заново.")
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT id FROM shop_inquiries WHERE user_id=? AND request_id=?",
            (user["id"], request_id)).fetchone()
        if existing:
            return web.json_response({"id": existing["id"]})
        color = db.execute("SELECT * FROM colors WHERE id=?", (color_id,)).fetchone()
        if not color:
            raise web.HTTPNotFound(text="Такого цвета нет в каталоге.")
        if color["stock"] > 0:
            raise web.HTTPConflict(text="Цвет появился в наличии — можно оформить заказ.")
        count = db.execute(
            "SELECT COUNT(*) FROM shop_inquiries WHERE user_id=? "
            "AND created_at > datetime('now', '-1 day')", (user["id"],)
        ).fetchone()[0]
        if count >= 5:
            raise web.HTTPTooManyRequests(text="Слишком много запросов за день. Напиши менеджеру напрямую.")
        result = db.execute(
            "INSERT INTO shop_inquiries(user_id,color_id,contact,request_id) VALUES(?,?,?,?)",
            (user["id"], color_id, contact.strip(), request_id)
        )
    return web.json_response({"id": result.lastrowid}, status=201)


async def stock(request):
    """Manager can set physical stock and toggle in-transit separately."""
    admin(request)
    data = await payload(request)
    color_id = number(data, "color", 1)
    stock_qty = number(data, "stock", 0)
    old_stock = number(data, "previous", 0)
    arriving = data.get("incoming", False)
    old_arriving = data.get("previous_incoming", False)
    if type(arriving) is not bool or type(old_arriving) is not bool:
        raise web.HTTPBadRequest(text="Неверный статус поставки.")
    if stock_qty > 0:
        arriving = False
    with closing(database.get_connection()) as db, db:
        result = db.execute(
            "UPDATE colors SET stock=?, incoming=? WHERE id=? AND stock=? AND incoming=?",
            (stock_qty, int(arriving), color_id, old_stock, int(old_arriving))
        )
        if not result.rowcount:
            raise web.HTTPConflict(text="Остаток или статус уже изменился. Обнови страницу.")
    return web.json_response({"ok": True})


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
            options = ", ".join(title for k,title in (("sleeve","конверт"),("label","яблоко"),("rush","вне очереди")) if o[k]) or "без доплат"
            message = (f"Заказ №{ref}\n{o['color']} × {o['quantity']}\n{options}\n"
                       f"Итого {o['total']} ₽\n{o['name']} · {o['contact']}")
            message += f"\nМакеты прикреплены: {len(o.get('files', {}))}" if o["sleeve"] or o["label"] else ""
            message += f"\nСкорость: {o.get('rpm', 33)} RPM"
            for side in ("a", "b"):
                tracks = o.get("tracklist_" + side) or []
                if tracks:
                    message += "\n" + side.upper() + ": " + "; ".join(tracks)[:600]
            if o.get("audio_link"):
                message += "\nАудио: " + o["audio_link"][:800]
            link = WebAppInfo(url=f"{config.MINIAPP_URL}/?order={ref}")
            try:
                await bot.send_message(config.ADMIN_ID, message, parse_mode=None,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть заказ",web_app=link)]]))
            except Exception as exc:
                logging.warning("Manager notification %s pending (%s)",ref,type(exc).__name__)
                break
            with closing(database.get_connection()) as db, db:
                db.execute("UPDATE shop_orders SET notified=1 WHERE id=?", (ref,))
        with closing(database.get_connection()) as db:
            requests = db.execute(
                "SELECT q.id, q.user_id, q.contact, c.name, c.incoming FROM shop_inquiries q "
                "JOIN colors c ON c.id=q.color_id "
                "WHERE q.notified=0 ORDER BY q.id LIMIT 20"
            ).fetchall()
        for inquiry_row in requests:
            try:
                await bot.send_message(
                    config.ADMIN_ID,
                    f"Запрос цвета №{inquiry_row['id']}\n"
                    f"Хочу: {inquiry_row['name']} ({'в пути' if inquiry_row['incoming'] else 'нет в наличии'})\n"
                    f"Telegram ID: {inquiry_row['user_id']}\n"
                    f"Контакт: {inquiry_row['contact']}",
                    parse_mode=None
                )
            except Exception as exc:
                logging.warning("Colour inquiry %s pending (%s)",
                                inquiry_row["id"], type(exc).__name__)
                break
            with closing(database.get_connection()) as db, db:
                db.execute("UPDATE shop_inquiries SET notified=1 WHERE id=?",
                           (inquiry_row["id"],))
        await asyncio.sleep(5)


def application():
    app = web.Application(client_max_size=90 * 1024 * 1024)
    async def home(request):
        # Telegram WebView and proxy caches must not keep obsolete app HTML.
        return web.FileResponse(APP / "index.html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
    async def release(request):
        return web.json_response({"release": RELEASE}, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
        })
    app.router.add_get("/", home)
    app.router.add_get("/api/release", release)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_post("/api/orders", place)
    app.router.add_post("/api/inquiries", inquiry)
    app.router.add_post("/api/stock", stock)
    app.router.add_get(r"/api/orders/{id:\d+}", manager_order)
    app.router.add_post(r"/api/orders/{id:\d+}/cancel", manager_order)
    app.router.add_get(r"/api/orders/{id:\d+}/artwork/{field}", download_artwork)
    app.router.add_static("/assets", APP / "assets")
    app.router.add_static("/photos", PHOTOS)
    app.router.add_static("/templates", ROOT / "templates")
    return app
