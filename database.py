"""
Вся работа с базой данных SQLite живёт здесь.
База — это один файл kroog.db рядом с проектом.

Для нашего объёма заказов обычного модуля sqlite3 более чем достаточно.
"""

import os
import sqlite3

# Путь к файлу базы. Локально — kroog.db рядом с проектом.
# На хостинге зададим переменную DB_PATH с путём к постоянному хранилищу (Volume),
# иначе остатки на складе будут сбрасываться при перезапуске.
DB_PATH = os.getenv("DB_PATH", "kroog.db")

# Стартовый список цветов: (название, остаток, чёрный?, пояснение, эмодзи)
# is_black = 1 только у чёрного (он стоит 3500, остальные — 4500).
START_COLORS = [
    ("Чёрный", 60, 1, None, "⚫"),
    ("Белый", 10, 0, None, "⚪"),
    ("Прозрачный", 35, 0, None, "💎"),
    ("Синий прозрачный", 10, 0, None, "🔵"),
    ("Оранжевый прозрачный", 10, 0, None, "🟠"),
    ("Зелёный марбл", 10, 0, "Мраморный рисунок", "🟢"),
    ("Розовый «Барби»", 10, 0, None, "🩷"),
    ("Фиолетовый дымчатый", 5, 0, None, "🟣"),
    ("Русалка", 5, 0, "Хамелеон: переливается из зелёного в фиолетовый — зависит от света и угла", "🧜"),
    ("Жёлтый прозрачный", 5, 0, None, "🟡"),
    ("Красный прозрачный", 10, 0, None, "🔴"),
    ("Древесный", 10, 0, "Имитация дерева", "🟤"),
]

# Соответствие «название → эмодзи» для миграции уже существующей базы
COLOR_EMOJI = {name: emoji for name, _, _, _, emoji in START_COLORS}


def get_connection() -> sqlite3.Connection:
    """Открыть соединение с базой. row_factory — чтобы обращаться к полям по имени."""
    # Если путь содержит папку (например data/kroog.db) — создаём её
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создать таблицу (если ещё нет) и заполнить стартовыми цветами (если пусто)."""
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS colors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            stock       INTEGER NOT NULL DEFAULT 0,
            is_black    INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            emoji       TEXT NOT NULL DEFAULT ''
        )
        """
    )

    # Миграция: если база старая и столбца emoji ещё нет — добавляем и заполняем
    columns = [row[1] for row in conn.execute("PRAGMA table_info(colors)")]
    if "emoji" not in columns:
        conn.execute("ALTER TABLE colors ADD COLUMN emoji TEXT NOT NULL DEFAULT ''")
    for name, emoji in COLOR_EMOJI.items():
        conn.execute(
            "UPDATE colors SET emoji = ? WHERE name = ? AND (emoji = '' OR emoji IS NULL)",
            (emoji, name),
        )

    # Если цветов ещё нет — добавляем стартовые
    count = conn.execute("SELECT COUNT(*) FROM colors").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT INTO colors (name, stock, is_black, description, emoji) "
            "VALUES (?, ?, ?, ?, ?)",
            START_COLORS,
        )
    conn.commit()
    conn.close()


def get_all_colors() -> list[sqlite3.Row]:
    """Все цвета (включая те, что закончились)."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM colors ORDER BY id").fetchall()
    conn.close()
    return rows


def get_available_colors() -> list[sqlite3.Row]:
    """Только цвета, которых есть хотя бы 1 штука."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM colors WHERE stock > 0 ORDER BY id").fetchall()
    conn.close()
    return rows


def get_color(color_id: int) -> sqlite3.Row | None:
    """Один цвет по id."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM colors WHERE id = ?", (color_id,)).fetchone()
    conn.close()
    return row


def decrease_stock(color_id: int, quantity: int) -> None:
    """Уменьшить остаток после оформленного заказа."""
    conn = get_connection()
    conn.execute(
        "UPDATE colors SET stock = stock - ? WHERE id = ?",
        (quantity, color_id),
    )
    conn.commit()
    conn.close()


def add_stock(color_id: int, quantity: int) -> None:
    """Пополнить склад (для админ-команды, когда придут заготовки)."""
    conn = get_connection()
    conn.execute(
        "UPDATE colors SET stock = stock + ? WHERE id = ?",
        (quantity, color_id),
    )
    conn.commit()
    conn.close()


def set_stock(color_id: int, quantity: int) -> None:
    """Задать точный остаток (для админ-команды /set)."""
    conn = get_connection()
    conn.execute(
        "UPDATE colors SET stock = ? WHERE id = ?",
        (quantity, color_id),
    )
    conn.commit()
    conn.close()
