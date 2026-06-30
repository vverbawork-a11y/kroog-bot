# Деплой бота KROOG на сервер (VPS + systemd)

Пошаговая инструкция. Будем выполнять её вместе, когда у тебя будет сервер.
Понадобится: **IP сервера**, **логин** (`root`) и **пароль** от хостера.

---

## 0. Важно перед началом
На сервере должна работать **только одна** копия бота. Поэтому сначала
**останови бота на Маке** (закрой окно `run.command` или выполни `pkill -f main.py`).

---

## 1. Подключиться к серверу (с Мака)
В Терминале на Маке:
```
ssh root@IP_СЕРВЕРА
```
Введи пароль (при вводе он не отображается — это нормально). Подтверди `yes`, если спросит.

## 2. Установить Python (на сервере)
```
apt update && apt install -y python3 python3-venv rsync
```

## 3. Скопировать проект на сервер (НОВОЕ окно Терминала на Маке)
Не закрывая SSH, открой второе окно Терминала и выполни:
```
rsync -av --exclude venv --exclude __pycache__ --exclude '*.db' \
  ~/Desktop/kroog_bot/ root@IP_СЕРВЕРА:/root/kroog_bot/
```
(venv и база НЕ копируются: venv пересоберём на сервере, база создастся заново.)

## 4. Поставить зависимости (в окне с SSH)
```
cd /root/kroog_bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 5. Проверить, что запускается
```
./venv/bin/python main.py
```
Увидел `Run polling for bot @kroog_vinyl_bot` — всё ок. Останови: `Ctrl + C`.

## 6. Сделать службу (автозапуск 24/7)
```
cp deploy/kroog-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kroog-bot
systemctl status kroog-bot
```
Зелёное `active (running)` — бот работает и переживёт перезагрузку сервера.

---

## Управление потом
| Действие | Команда |
|---|---|
| Логи в реальном времени | `journalctl -u kroog-bot -f` |
| Перезапустить | `systemctl restart kroog-bot` |
| Остановить | `systemctl stop kroog-bot` |
| Запустить | `systemctl start kroog-bot` |
| Статус | `systemctl status kroog-bot` |

## Обновить код после правок (с Мака)
```
rsync -av --exclude venv --exclude __pycache__ --exclude '*.db' \
  ~/Desktop/kroog_bot/ root@IP_СЕРВЕРА:/root/kroog_bot/
ssh root@IP_СЕРВЕРА 'systemctl restart kroog-bot'
```
