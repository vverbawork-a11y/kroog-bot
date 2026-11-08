"""Public colour catalogue, retail prices and photo atlas indices.

Wholesale costs stay in private accounting, never in the public source code.
Always preserve existing stock counts in SQLite during migrations.
Atlas cells: 4 columns, 8 rows; sorted supplier ZIP images.
"""
# (public name, retail RUB, atlas cell)
COLOURS = [
    ("Розовый «Барби»", 5500, 0),
    ("Белый", 5200, 1),
    ("Бирюзовый", 5500, 2),
    ("Бирюзовый прозрачный", 5200, 3),
    ("Голубой марбл", 5500, 4),
    ("Голубой пастельный", 5900, 5),
    ("Грэнни Смит", 5900, 6),
    ("Древесный", 5700, 7),
    ("Жёлтый", 5500, 8),
    ("Жёлтый марбл", 5900, 9),
    ("Жёлтый прозрачный", 5500, 10),
    ("Зелёный марбл", 5900, 11),
    ("Зелёный прозрачный", 5500, 12),
    ("Красный", 5500, 13),
    ("Красный марбл", 5900, 14),
    ("Красный прозрачный", 5500, 15),
    ("Лаймовый марбл", 5900, 16),
    ("Лиловый", 5500, 17),
    ("Лиловый марбл", 5500, 18),
    ("Лунный", 5500, 19),
    ("Мятный пастельный", 5900, 20),
    ("Ночной переливающийся", 6500, 21),
    ("Оранжевый", 5500, 22),
    ("Оранжевый пастельный", 5900, 23),
    ("Оранжевый прозрачный", 5500, 24),
    ("Персиковый", 5500, 25),
    ("Прозрачный", 3900, 26),
    ("Розовый", 5500, 27),
    ("Розовый пастельный", 5900, 28),
    ("Синий прозрачный", 5500, 29),
    ("Фиолетовый марбл", 5700, 30),
    ("Чёрный", 4500, 31),
]

BY_NAME = {name: {"price": price, "atlas": atlas}
           for name, price, atlas in COLOURS}

# Some earlier workshop colours have no match in the supplier ZIP.
LEGACY = {
    "Фиолетовый дымчатый": {"price": 5500, "atlas": None},
    "Голубой": {"price": 5500, "atlas": None},
}

def record(name, is_black=False):
    return BY_NAME.get(name) or LEGACY.get(name) or {
        "price": 4500 if is_black else 5500,
        "atlas": None,
    }
