"""Public colour catalogue, retail prices and photo atlas indices.

Wholesale costs stay in private accounting, never in the public source code.
Always preserve existing stock counts in SQLite during migrations.
Atlas cells: 4 columns, 8 rows; sorted supplier ZIP images.
"""
# Every retail price includes the basic sleeve/protection/white-label package.
from calculator import PRICE_BLACK, PRICE_COLOR

# (public name, retail RUB including packaging, atlas cell)
COLOURS = [
    ("Розовый «Барби»", 6000, 0),
    ("Белый", 5700, 1),
    ("Бирюзовый", 6000, 2),
    ("Бирюзовый прозрачный", 5700, 3),
    ("Голубой марбл", 6000, 4),
    ("Голубой пастельный", 6400, 5),
    ("Грэнни Смит", 6400, 6),
    ("Древесный", 6200, 7),
    ("Жёлтый", 6000, 8),
    ("Жёлтый марбл", 6400, 9),
    ("Жёлтый прозрачный", 6000, 10),
    ("Зелёный марбл", 6400, 11),
    ("Зелёный прозрачный", 6000, 12),
    ("Красный", 6000, 13),
    ("Красный марбл", 6400, 14),
    ("Красный прозрачный", 6000, 15),
    ("Лаймовый марбл", 6400, 16),
    ("Лиловый", 6000, 17),
    ("Розовый марбл", 6000, 18),
    ("Лунный", 6000, 19),
    ("Мятный пастельный", 6400, 20),
    ("Ночной переливающийся", 7000, 21),
    ("Оранжевый", 6000, 22),
    ("Оранжевый пастельный", 6400, 23),
    ("Оранжевый прозрачный", 6000, 24),
    ("Персиковый", 6000, 25),
    ("Прозрачный", 4400, 26),
    ("Розовый", 6000, 27),
    ("Розовый пастельный", 6400, 28),
    ("Синий прозрачный", 6000, 29),
    ("Фиолетовый марбл", 6200, 30),
    ("Чёрный", 5000, 31),
]

BY_NAME = {name: {"price": price, "atlas": atlas}
           for name, price, atlas in COLOURS}

# Some earlier workshop colours have no match in the supplier ZIP.
LEGACY = {
    "Фиолетовый дымчатый": {"price": PRICE_COLOR, "atlas": None},
    "Голубой": {"price": PRICE_COLOR, "atlas": None},
}

def record(name, is_black=False):
    return BY_NAME.get(name) or LEGACY.get(name) or {
        "price": PRICE_BLACK if is_black else PRICE_COLOR,
        "atlas": None,
    }
