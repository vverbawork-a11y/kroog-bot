"""
Конфигурация бота.

Секреты берутся в таком порядке:
1) переменные окружения — так задаём их на хостинге Bothost (приоритет);
2) локальный файл config_local.py — для запуска на своём Маке
   (он в .gitignore и в GitHub НЕ попадает).

Сам этот файл секретов НЕ содержит — его безопасно публиковать в GitHub.
"""

import os


def _from_local(name, default):
    """Пробуем взять значение из config_local.py (если файл есть)."""
    try:
        import config_local
    except ImportError:
        return default
    return getattr(config_local, name, default)


BOT_TOKEN = os.getenv("BOT_TOKEN") or _from_local("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID") or _from_local("ADMIN_ID", 0))
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME") or _from_local("MANAGER_USERNAME", "")
# Ссылка-приглашение на Telegram-канал (личная, поэтому через окружение, не в коде)
CHANNEL_URL = os.getenv("CHANNEL_URL") or _from_local("CHANNEL_URL", "")
