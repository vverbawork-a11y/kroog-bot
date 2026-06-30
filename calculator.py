"""
Расчёт цены заказа. Вся «математика денег» и цены — здесь.
Если поменяются цены — правишь только этот файл.
"""

# --- Цены за ОДНУ пластинку, в рублях ---
PRICE_BLACK = 3500       # чёрная пластинка
PRICE_COLOR = 4500       # цветная пластинка
PRICE_ENVELOPE = 700     # печать конверта (за штуку)
PRICE_LABEL = 300        # печать яблока/лейбла (150 × 2 стороны, за штуку)

# Наценка за срочный заказ, % к цене каждой пластинки.
# Хочешь фиксированную сумму вместо процента — скажи, переделаю.
URGENT_SURCHARGE_PERCENT = 20


def discount_percent(quantity: int) -> int:
    """Процент скидки в зависимости от тиража (пороговый)."""
    if quantity >= 50:
        return 20
    if quantity >= 25:
        return 15
    if quantity >= 10:
        return 10
    if quantity >= 5:
        return 5
    return 0


def calculate(
    is_black: bool,
    quantity: int,
    print_envelope: bool,
    print_label: bool,
    urgent: bool = False,
) -> dict:
    """
    Считает цену заказа и возвращает подробную разбивку (словарь),
    чтобы потом красиво показать пользователю.
    """
    # Цена одной пластинки + выбранная печать
    unit_price = PRICE_BLACK if is_black else PRICE_COLOR
    per_piece = unit_price
    if print_envelope:
        per_piece += PRICE_ENVELOPE
    if print_label:
        per_piece += PRICE_LABEL
    # Срочность — наценка на цену каждой пластинки
    if urgent:
        per_piece = round(per_piece * (1 + URGENT_SURCHARGE_PERCENT / 100))

    subtotal = per_piece * quantity            # сумма без скидки
    percent = discount_percent(quantity)       # процент скидки
    discount_rub = round(subtotal * percent / 100)
    total = subtotal - discount_rub            # итог к оплате

    return {
        "unit_price": unit_price,       # цена самой пластинки
        "per_piece": per_piece,         # цена за штуку (с печатью и срочностью)
        "quantity": quantity,
        "urgent": urgent,
        "subtotal": subtotal,
        "percent": percent,
        "discount_rub": discount_rub,
        "total": total,
    }


# Быстрая самопроверка: запусти `python calculator.py`
if __name__ == "__main__":
    examples = [
        (True, 1, False, False),
        (False, 10, True, True),
        (False, 50, True, False),
    ]
    for is_black, qty, env, lbl in examples:
        r = calculate(is_black, qty, env, lbl)
        kind = "чёрная" if is_black else "цветная"
        print(
            f"{kind}, {qty} шт, конверт={env}, яблоко={lbl}: "
            f"за штуку {r['per_piece']}₽, "
            f"без скидки {r['subtotal']}₽, "
            f"скидка {r['percent']}% (−{r['discount_rub']}₽), "
            f"итого {r['total']}₽"
        )
