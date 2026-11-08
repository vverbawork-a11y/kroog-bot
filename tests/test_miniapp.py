"""End-to-end Mini App order tests without Telegram or a production database."""
import hashlib
import hmac
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

import config
import database
import shop


class MiniAppOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = database.DB_PATH
        self.old_token = config.BOT_TOKEN
        self.old_admin = config.ADMIN_ID
        database.DB_PATH = str(Path(self.tmp.name) / "data" / "kroog.db")
        config.BOT_TOKEN = "012345:TEST_ONLY"
        config.ADMIN_ID = 42
        database.init_db()
        shop.setup()
        database.set_stock(1, 5)
        self.client = TestClient(TestServer(shop.application()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        database.DB_PATH = self.old_path
        config.BOT_TOKEN = self.old_token
        config.ADMIN_ID = self.old_admin
        self.tmp.cleanup()

    def headers(self, user_id):
        values = {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False,
                               separators=(",", ":")),
        }
        signed = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret = hmac.digest(b"WebAppData", config.BOT_TOKEN.encode(), "sha256")
        values["hash"] = hmac.new(secret, signed.encode(), "sha256").hexdigest()
        return {"X-Telegram-Init-Data": urlencode(values)}

    def params(self):
        return {
            "color": 1, "quantity": 2, "sleeve": True,
            "label": True, "rush": False, "contact": "@client",
            "request_id": str(uuid.uuid4()),
        }

    async def submit(self, params, files=True):
        form = FormData()
        form.add_field("payload", json.dumps(params, ensure_ascii=False))
        if files:
            for key in ("sleeve_front", "sleeve_back", "label_a", "label_b"):
                form.add_field(key, b"TEST FILE " + key.encode(),
                               filename=key + ".jpg", content_type="image/jpeg")
        return await self.client.post("/api/orders", data=form, headers=self.headers(43))

    async def test_artwork_order_is_saved_and_private(self):
        response = await self.submit(self.params())
        self.assertEqual(response.status, 201, await response.text())
        order_id = (await response.json())["id"]
        self.assertEqual(database.get_color(1)["stock"], 3)
        with database.get_connection() as db:
            details = json.loads(db.execute(
                "SELECT details FROM shop_orders WHERE id=?", (order_id,)
            ).fetchone()["details"])
        self.assertEqual(len(details["files"]), 4)
        file_url = f"/api/orders/{order_id}/artwork/sleeve_front"
        unauthorized = await self.client.get(file_url, headers=self.headers(43))
        self.assertEqual(unauthorized.status, 403)
        permitted = await self.client.get(file_url, headers=self.headers(42))
        self.assertEqual(permitted.status, 200)
        self.assertEqual(await permitted.read(), b"TEST FILE sleeve_front")

    async def test_retry_is_idempotent(self):
        params = self.params()
        first = await self.submit(params)
        self.assertEqual(first.status, 201, await first.text())
        order_id = (await first.json())["id"]
        second = await self.submit(params)
        self.assertEqual(second.status, 200, await second.text())
        self.assertEqual((await second.json())["id"], order_id)
        self.assertEqual(database.get_color(1)["stock"], 3)

    async def test_cancel_returns_stock_once(self):
        response = await self.submit(self.params())
        order_id = (await response.json())["id"]
        for _ in range(2):
            cancelled = await self.client.post(
                f"/api/orders/{order_id}/cancel",
                json={}, headers=self.headers(42))
            self.assertEqual(cancelled.status, 200)
        self.assertEqual(database.get_color(1)["stock"], 5)

    async def test_files_require_matching_print_option(self):
        params = self.params()
        params["sleeve"] = False
        response = await self.submit(params)
        self.assertEqual(response.status, 400)
        self.assertEqual(database.get_color(1)["stock"], 5)


if __name__ == "__main__":
    unittest.main()
