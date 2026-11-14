"""Server-authoritative, single-code discounts with transactional redemption."""
import re
import time


class PromoError(ValueError):
    pass


def normalize(value):
    if not isinstance(value, str):
        raise PromoError("Введите один промокод.")
    code = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
        raise PromoError("Промокод: от 3 до 32 латинских букв, цифр, дефисов или подчёркиваний.")
    return code


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS promotions (
        id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
        percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 100),
        max_uses INTEGER, expires_at INTEGER, user_id INTEGER,
        returning_only INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS promotion_uses (
        id INTEGER PRIMARY KEY, promo_id INTEGER NOT NULL, order_id INTEGER NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, released INTEGER NOT NULL DEFAULT 0)""")
    db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS promo_user_active
        ON promotion_uses(promo_id,user_id) WHERE released=0""")


def create(db, data, now=None):
    now = int(time.time()) if now is None else now
    code = normalize(data.get("code"))
    percent = data.get("percent")
    if type(percent) is not int or not 1 <= percent <= 100:
        raise PromoError("Укажите скидку от 1 до 100%.")
    for name, label in (("max_uses", "Лимит использований"), ("user_id", "Telegram ID")):
        value = data.get(name)
        if value is not None and (type(value) is not int or not 1 <= value <= 10**15):
            raise PromoError(label + " должен быть положительным целым числом.")
    expires = data.get("expires_at")
    if expires is not None and (type(expires) is not int or not now < expires <= now + 10*366*86400):
        raise PromoError("Укажите будущую дату окончания, не более чем через 10 лет.")
    returning = data.get("returning_only", False)
    if type(returning) is not bool:
        raise PromoError("Проверьте условие повторного заказа.")
    cursor = db.execute("""INSERT INTO promotions
        (code,percent,max_uses,expires_at,user_id,returning_only,created_at)
        VALUES (?,?,?,?,?,?,?)""", (code, percent, data.get("max_uses"), expires,
                                      data.get("user_id"), int(returning), now))
    return cursor.lastrowid


def validate(db, value, user_id, now=None):
    code = normalize(value)
    now = int(time.time()) if now is None else now
    row = db.execute("SELECT * FROM promotions WHERE code=?", (code,)).fetchone()
    if not row or not row["active"]:
        raise PromoError("Промокод не найден или отключён.")
    if row["expires_at"] is not None and row["expires_at"] <= now:
        raise PromoError("Срок действия промокода истёк.")
    if row["user_id"] is not None and row["user_id"] != user_id:
        raise PromoError("Этот промокод предназначен другому получателю.")
    if row["returning_only"] and not db.execute(
            "SELECT 1 FROM shop_orders WHERE user_id=? AND cancelled=0 LIMIT 1", (user_id,)).fetchone():
        raise PromoError("Этот промокод доступен после первого оформленного заказа.")
    if db.execute("SELECT 1 FROM promotion_uses WHERE promo_id=? AND user_id=? AND released=0",
                  (row["id"], user_id)).fetchone():
        raise PromoError("Вы уже использовали этот промокод.")
    count = db.execute("SELECT COUNT(*) FROM promotion_uses WHERE promo_id=? AND released=0",
                       (row["id"],)).fetchone()[0]
    if row["max_uses"] is not None and count >= row["max_uses"]:
        raise PromoError("Лимит использований промокода исчерпан.")
    return dict(row)


def discount(totals, promo):
    """Use the greater discount; do not stack codes or quantity discounts."""
    volume = totals["percent"]
    applied = promo["percent"] > volume
    percent = max(volume, promo["percent"])
    amount = round(totals["subtotal"] * percent / 100)
    return dict(totals, volume_percent=volume, percent=percent, discount_rub=amount,
                total=totals["subtotal"]-amount, promo_code=promo["code"],
                promo_percent=promo["percent"], promo_applied=applied,
                discount_source="promo" if applied else "quantity")
