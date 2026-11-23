"""Private artwork uploads for Mini App orders.

Artwork is stored beside the persistent SQLite DB, never in public static routes.
Only the Telegram-authenticated manager can download it.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

from aiohttp import web

import database

FIELDS = {"sleeve_front", "sleeve_back", "label_a", "label_b"}
EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff", ".psd", ".ai"}
MAX_FILE = 20 * 1024 * 1024
MAX_PAYLOAD = 16384


def validate_signature(path, suffix):
    # Reject renamed scripts/executables. This is format screening, not antivirus.
    with path.open("rb") as source:
        head = source.read(1024)
    signatures = {
        ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".tif": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
        ".tiff": (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"),
        ".psd": (b"8BPS\x00\x01",), ".pdf": (b"%PDF-",),
        ".ai": (b"%PDF-", b"%!PS-Adobe-"),
    }
    if not head.startswith(signatures.get(suffix, ())):
        raise web.HTTPBadRequest(text="Содержимое макета не соответствует формату файла.")


async def bounded_read(stream, limit=MAX_PAYLOAD):
    body = bytearray()
    while True:
        chunk = await stream.read_chunk(size=8192)
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=len(body))



def directory():
    root = Path(database.DB_PATH).resolve().parent / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def discard(stage):
    if stage:
        shutil.rmtree(stage, ignore_errors=True)


async def receive(request):
    """Return (JSON payload, files manifest, temporary upload dir).

    A JSON-only order remains supported for older clients.
    """
    # Browsers encode FormData with only a text field as urlencoded rather
    # than multipart. Orders without uploaded artwork must also work.
    if request.content_type == "application/x-www-form-urlencoded":
        try:
            form = await request.post()
            if len(form) != 1 or "payload" not in form:
                raise ValueError
            data = json.loads(form["payload"])
            if not isinstance(data, dict):
                raise ValueError
            return data, {}, None
        except (ValueError, TypeError, KeyError):
            raise web.HTTPBadRequest(text="Некорректная заявка.")

    if request.content_type != "multipart/form-data":
        try:
            data = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Не удалось прочитать заявку.")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="Некорректная заявка.")
        return data, {}, None

    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=directory()))
    files = {}
    data = None
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "payload" and part.filename is None:
                if data is not None:
                    raise web.HTTPBadRequest(text="Повторяющиеся параметры заказа.")
                body = await bounded_read(part)
                try:
                    data = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise web.HTTPBadRequest(text="Некорректная заявка.")
            elif part.name in FIELDS and part.filename:
                key = part.name
                if key in files:
                    raise web.HTTPBadRequest(text="Файл для этой стороны уже прикреплён.")
                original = part.filename.replace("\\", "/").split("/")[-1][:150]
                suffix = Path(original).suffix.lower()
                if not original or suffix not in EXTENSIONS:
                    raise web.HTTPBadRequest(text="Поддерживаются JPG, PNG, PDF, TIFF, PSD и AI.")
                name = key + suffix
                size = 0
                with (stage / name).open("wb") as output:
                    while chunk := await part.read_chunk(size=65536):
                        size += len(chunk)
                        if size > MAX_FILE:
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE, actual_size=size,
                                text="Каждый макет должен быть не больше 20 МБ.")
                        output.write(chunk)
                if not size:
                    raise web.HTTPBadRequest(text="Прикреплён пустой файл.")
                validate_signature(stage / name, suffix)
                files[key] = {"name": original, "stored": name, "size": size}
            else:
                raise web.HTTPBadRequest(text="Неизвестное поле в заявке.")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="Отсутствуют параметры заказа.")
        return data, files, stage
    except Exception:
        discard(stage)
        raise


def finalize(stage, order_id, files):
    """Move staged files to an order-owned directory before DB commit."""
    if not stage:
        return None
    if not files:
        discard(stage)
        return None
    target = directory() / str(order_id)
    if target.exists():
        raise RuntimeError("Order artwork directory already exists")
    os.replace(stage, target)
    return target


def stored_file(order_id, manifest, field):
    if field not in FIELDS or not isinstance(manifest, dict):
        raise web.HTTPNotFound(text="Макет не найден.")
    entry = manifest.get(field)
    if not isinstance(entry, dict) or entry.get("stored") not in {
        field + ext for ext in EXTENSIONS
    }:
        raise web.HTTPNotFound(text="Макет не найден.")
    result = directory() / str(order_id) / entry["stored"]
    if not result.is_file():
        raise web.HTTPNotFound(text="Файл макета не найден.")
    return result, entry.get("name", field)
