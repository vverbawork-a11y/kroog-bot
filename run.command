#!/bin/bash
# Двойной клик по этому файлу запускает бота KROOG.
# caffeinate не даёт Маку уснуть, пока бот работает. Ctrl+C — остановить.
cd "$(dirname "$0")"
caffeinate -i ./venv/bin/python main.py
