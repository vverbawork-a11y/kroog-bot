"""Telegram Mini App: catalog, durable reservations and manager notifications."""
import asyncio
import hashlib
import hmac
import json
import re
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing, suppress
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

from aiohttp import web

import artwork
import calculator as prices
import colours
import config
import database
import promotions

ROOT = Path(__file__).resolve().parent
APP = ROOT / "miniapp"
PHOTOS = ROOT / "photos"
RELEASE = "2026-11-23-26"


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

        columns = {row[1] for row in db.execute("PRAGMA table_info(shop_orders)")}
        for name, declaration in (("paid_at", "INTEGER"), ("expires_at", "INTEGER"),
                                  ("cancel_reason", "TEXT")):
            if name not in columns:
                db.execute(f"ALTER TABLE shop_orders ADD COLUMN {name} {declaration}")
        # Existing orders deliberately retain NULL deadlines: payment history
        # was not tracked before this release, so auto-cancelling them is unsafe.
        db.execute("CREATE INDEX IF NOT EXISTS orders_expiry ON shop_orders(expires_at) "
                   "WHERE cancelled=0 AND paid_at IS NULL")
        promotions.setup(db)
        db.execute("""CREATE TABLE IF NOT EXISTS api_limits (
            user_id INTEGER NOT NULL, bucket TEXT NOT NULL, started INTEGER NOT NULL,
            count INTEGER NOT NULL, PRIMARY KEY(user_id,bucket))""")



def expire_orders(db, now=None):
    """Caller owns BEGIN IMMEDIATE; stock/promo release is exactly once."""
    now = int(time.time()) if now is None else now
    rows = db.execute("SELECT id,color_id,quantity FROM shop_orders "
                      "WHERE cancelled=0 AND paid_at IS NULL AND expires_at <= ?", (now,)).fetchall()
    for row in rows:
        db.execute("UPDATE colors SET stock=stock+? WHERE id=?", (row["quantity"], row["color_id"]))
        db.execute("UPDATE shop_orders SET cancelled=1,cancel_reason='expired' WHERE id=?", (row["id"],))
        db.execute("UPDATE promotion_uses SET released=1 WHERE order_id=?", (row["id"],))
    return len(rows)


def sweep_expired_orders():
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        return expire_orders(db)


async def reservation_worker(app):
    async def run():
        while True:
            try:
                sweep_expired_orders()
            except Exception:
                logging.exception("Could not expire reservations; retrying")
            await asyncio.sleep(30)
    task = asyncio.create_task(run())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


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


def limit_requests(user_id, bucket, maximum, seconds=60):
    # Shared across restarts/workers; identity comes only from verified Telegram data.
    now = int(time.time())
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM api_limits WHERE started < ?", (now - 86400,))
        row = db.execute("SELECT started,count FROM api_limits WHERE user_id=? AND bucket=?",
                         (user_id, bucket)).fetchone()
        if row and now - row["started"] < seconds:
            if row["count"] >= maximum:
                raise web.HTTPTooManyRequests(
                    text="Слишком много запросов. Попробуйте через минуту.",
                    headers={"Retry-After": str(max(1, seconds - (now - row["started"])))})
            db.execute("UPDATE api_limits SET count=count+1 WHERE user_id=? AND bucket=?",
                       (user_id, bucket))
        else:
            db.execute("INSERT OR REPLACE INTO api_limits VALUES(?,?,?,1)",
                       (user_id, bucket, now))


@web.middleware
async def api_security(request, handler):
    if request.path.startswith("/api/"):
        try:
            if request.path != "/api/release":
                user = identity(request)
                if request.method == "POST":
                    maximum = 10 if request.path in {"/api/orders", "/api/inquiries"} else 60
                    limit_requests(user["id"], request.path if request.path in {
                        "/api/orders", "/api/inquiries", "/api/quote"} else "admin", maximum)
                    if request.content_type == "multipart/form-data":
                        if request.path != "/api/orders":
                            raise web.HTTPUnsupportedMediaType()
                    else:
                        # Reading here enforces a small JSON/form limit even for chunked bodies.
                        request = request.clone(client_max_size=artwork.MAX_PAYLOAD)
                        await request.read()
            response = await handler(request)
        except web.HTTPException as error:
            error.headers["Cache-Control"] = "private, no-store"
            error.headers["X-Content-Type-Options"] = "nosniff"
            raise
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    return await handler(request)


def photo(name):
    key = name.lower().replace("ё","е").replace("«","").replace("»","").replace(" ","_")
    for path in PHOTOS.glob("*.jpg"):
        if path.stem.lower() == key:
            return "/photos/" + quote(path.name)
    return None


def manager_telegram_url():
    """Use configured manager handle, or link to that same admin by Telegram ID."""
    handle = (config.MANAGER_USERNAME or "").strip().removeprefix("@")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", handle):
        return "https://t.me/" + handle
    return f"tg://user?id={config.ADMIN_ID}" if config.ADMIN_ID > 0 else None


async def catalog(request):
    user = identity(request)
    owner = user["id"] == config.ADMIN_ID
    sweep_expired_orders()
    rows = database.get_all_colors()
    products = []
    for row in rows:
        name = row["name"]
        # Legacy duplicate of Ночной переливающийся. Preserve the existing DB
        # entry for historical order ids, but never list it as a product.
        if name in {"Русалка", "Фиолетовый дымчатый", "Лиловый марбл"}:
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
        "manager_url": manager_telegram_url(),
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


def order_totals(db, data, user_id):
    qty = number(data, "quantity", 1)
    color_id = number(data, "color", 1)
    if any(type(data.get(k)) is not bool for k in ("sleeve", "label", "rush")):
        raise web.HTTPBadRequest(text="Проверьте параметры заказа.")
    color = db.execute("SELECT * FROM colors WHERE id=?", (color_id,)).fetchone()
    if not color or color["stock"] < qty:
        raise web.HTTPConflict(text="Остаток изменился. Выберите доступное количество.")
    totals = prices.calculate(color["is_black"], qty, data["sleeve"], data["label"], data["rush"],
                              unit_price_override=colours.record(color["name"], bool(color["is_black"]))["price"])
    promo = None
    value = data.get("promo_code", "")
    if not isinstance(value, str):
        raise web.HTTPBadRequest(text="Можно применить только один промокод.")
    if value.strip():
        try:
            promo = promotions.validate(db, value, user_id)
        except promotions.PromoError as error:
            raise web.HTTPBadRequest(text=str(error))
        totals = promotions.discount(totals, promo)
    return color, totals, promo


async def quote_order(request):
    user = identity(request)
    data = await payload(request)
    sweep_expired_orders()
    with closing(database.get_connection()) as db:
        _, totals, _ = order_totals(db, data, user["id"])
    return web.json_response(totals, headers={"Cache-Control": "no-store"})


async def manage_promos(request):
    admin(request)
    with closing(database.get_connection()) as db, db:
        if request.method == "POST":
            data = await payload(request)
            if "id" in request.match_info:
                promo_id = int(request.match_info["id"])
                if data == {"delete": True}:
                    # Hide the removed code permanently without deleting usage
                    # records or altering historical order totals.
                    if not promotions.delete(db, promo_id):
                        raise web.HTTPNotFound(text="Промокод не найден.")
                elif "delete" not in data and type(data.get("active")) is bool and len(data) == 1:
                    result = db.execute(
                        "UPDATE promotions SET active=? WHERE id=? AND deleted_at IS NULL",
                        (int(data["active"]), promo_id))
                    if not result.rowcount:
                        raise web.HTTPNotFound(text="Промокод не найден.")
                else:
                    raise web.HTTPBadRequest(text="Укажите статус промокода или его удаление.")
            else:
                try:
                    promotions.create(db, data)
                except promotions.PromoError as error:
                    raise web.HTTPBadRequest(text=str(error))
                except sqlite3.IntegrityError:
                    raise web.HTTPConflict(text="Промокод с таким названием уже существует.")
        rows = db.execute("""SELECT p.*, (SELECT COUNT(*) FROM promotion_uses u
            WHERE u.promo_id=p.id AND u.released=0) AS used
            FROM promotions p WHERE p.deleted_at IS NULL ORDER BY p.id DESC""").fetchall()
        return web.json_response({"promos": [dict(row) for row in rows]},
                                 headers={"Cache-Control": "private, no-store"})


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
                expire_orders(db)
                old = db.execute(
                    "SELECT id FROM shop_orders WHERE user_id=? AND request_id=?",
                    (user["id"], request_id)
                ).fetchone()
                if old:
                    db.rollback()
                    return web.json_response({"id": old["id"]})
                color, totals, promo = order_totals(db, data, user["id"])
                details = dict(
                    color=color["name"], contact=contact.strip(), user_id=user["id"],
                    name=" ".join(filter(None, (user.get("first_name"), user.get("last_name")))),
                    sleeve=data["sleeve"], label=data["label"], rush=data["rush"],
                    files=files, rpm=rpm, tracklist_a=tracklists["a"],
                    tracklist_b=tracklists["b"], audio_link=audio_link, **totals
                )
                db.execute("UPDATE colors SET stock=stock-? WHERE id=?", (qty, color_id))
                order = db.execute(
                    "INSERT INTO shop_orders(user_id,request_id,color_id,quantity,details,expires_at) VALUES(?,?,?,?,?,?)",
                    (user["id"], request_id, color_id, qty, json.dumps(details, ensure_ascii=False),
                     int(time.time()) + 24 * 3600)
                )
                if promo and totals.get("promo_applied"):
                    db.execute("INSERT INTO promotion_uses(promo_id,order_id,user_id) VALUES(?,?,?)",
                               (promo["id"], order.lastrowid, user["id"]))
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
            raise web.HTTPTooManyRequests(text="Слишком много запросов за день. Напишите менеджеру напрямую.")
        result = db.execute(
            "INSERT INTO shop_inquiries(user_id,color_id,contact,request_id) VALUES(?,?,?,?)",
            (user["id"], color_id, contact.strip(), request_id)
        )
    return web.json_response({"id": result.lastrowid}, status=201)


async def stock(request):
    """Manager can set physical stock and toggle in-transit separately."""
    admin(request)
    sweep_expired_orders()
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
            raise web.HTTPConflict(text="Остаток или статус уже изменился. Обновите страницу.")
    return web.json_response({"ok": True})


async def manager_order(request):
    admin(request)
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        expire_orders(db)
        row = db.execute("SELECT * FROM shop_orders WHERE id=?", (request.match_info["id"],)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="Заказ не найден.")
        cancelled = bool(row["cancelled"])
        if request.method == "POST" and not cancelled:
            db.execute("UPDATE colors SET stock=stock+? WHERE id=?", (row["quantity"],row["color_id"]))
            db.execute("UPDATE shop_orders SET cancelled=1,cancel_reason='manager' WHERE id=?", (row["id"],))
            db.execute("UPDATE promotion_uses SET released=1 WHERE order_id=?", (row["id"],))
            cancelled = True
        return web.json_response(dict(json.loads(row["details"]), id=row["id"], cancelled=cancelled,
                                      paid=bool(row["paid_at"]), paid_at=row["paid_at"],
                                      expires_at=row["expires_at"], cancel_reason=row["cancel_reason"]))


async def mark_paid(request):
    admin(request)
    with closing(database.get_connection()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        expire_orders(db)
        row = db.execute("SELECT * FROM shop_orders WHERE id=?", (request.match_info["id"],)).fetchone()
        if not row:
            raise web.HTTPNotFound(text="Заказ не найден.")
        if row["cancelled"]:
            # Commit expiry before rejecting, so released stock is not rolled back.
            db.commit()
            raise web.HTTPConflict(text="Заказ уже отменён. Оформите новый заказ перед оплатой.")
        if row["paid_at"] is None:
            db.execute("UPDATE shop_orders SET paid_at=? WHERE id=?", (int(time.time()), row["id"]))
    return web.json_response({"ok": True})


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
            orders = db.execute("SELECT id,details,expires_at FROM shop_orders WHERE notified=0 AND cancelled=0 ORDER BY id LIMIT 20").fetchall()
        for order in orders:
            o = json.loads(order["details"])
            ref = order["id"]
            options = ", ".join(title for k,title in (("sleeve","конверт"),("label","яблоко"),("rush","вне очереди")) if o[k]) or "без доплат"
            message = (f"Заказ №{ref}\n{o['color']} × {o['quantity']}\n{options}\n"
                       f"Итого {o['total']} ₽\n{o['name']} · {o['contact']}")
            message += f"\nМакеты прикреплены: {len(o.get('files', {}))}" if o["sleeve"] or o["label"] else ""
            if o.get("promo_applied"):
                message += f"\nПромокод: {o['promo_code']} ({o['promo_percent']}%)"
            message += f"\nСкорость: {o.get('rpm', 33)} RPM"
            for side in ("a", "b"):
                tracks = o.get("tracklist_" + side) or []
                if tracks:
                    message += "\n" + side.upper() + ": " + "; ".join(tracks)[:600]
            if o.get("audio_link"):
                message += "\nАудио: " + o["audio_link"][:800]
            if order["expires_at"]:
                message += "\nРезерв на 24 часа. После получения оплаты отметьте её в карточке заказа."
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
    app = web.Application(client_max_size=90 * 1024 * 1024, middlewares=[api_security])
    app.cleanup_ctx.append(reservation_worker)
    async def home(request):
        # Telegram WebView and proxy caches must not keep obsolete app HTML.
        html = (APP / "index.html").read_text(encoding="utf-8")
        commit = os.getenv("KROOG_COMMIT", "")
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            html = re.sub(r'(/assets/[^"<>?]+\.(?:css|js))(?:\?v=[^"<>]*)?',
                          lambda match: match[1] + "?v=" + commit, html)
        return web.Response(text=html, content_type="text/html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })
    async def release(request):
        status = {"enabled": False}
        status_path = os.getenv("KROOG_DEPLOY_STATUS")
        if status_path:
            try:
                saved = json.loads(Path(status_path).read_text(encoding="utf-8"))
                status = {key: saved[key] for key in (
                    "enabled", "interval_seconds", "phase", "current_sha",
                    "candidate_sha", "last_checked", "error") if key in saved}
            except (OSError, ValueError, TypeError):
                status = {"enabled": True, "phase": "status_unavailable"}
        return web.json_response({"release": RELEASE,
                                  "commit": status.get("current_sha", os.getenv("KROOG_COMMIT")),
                                  "autodeploy": status}, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
        })
    app.router.add_get("/", home)
    app.router.add_get("/api/release", release)
    app.router.add_get("/api/catalog", catalog)
    app.router.add_post("/api/orders", place)
    app.router.add_post("/api/quote", quote_order)
    app.router.add_get("/api/promos", manage_promos)
    app.router.add_post("/api/promos", manage_promos)
    app.router.add_post(r"/api/promos/{id:\d+}", manage_promos)
    app.router.add_post("/api/inquiries", inquiry)
    app.router.add_post("/api/stock", stock)
    app.router.add_get(r"/api/orders/{id:\d+}", manager_order)
    app.router.add_post(r"/api/orders/{id:\d+}/cancel", manager_order)
    app.router.add_post(r"/api/orders/{id:\d+}/paid", mark_paid)
    app.router.add_get(r"/api/orders/{id:\d+}/artwork/{field}", download_artwork)
    app.router.add_static("/assets", APP / "assets")
    app.router.add_static("/photos", PHOTOS)
    app.router.add_static("/templates", ROOT / "templates")
    return app
