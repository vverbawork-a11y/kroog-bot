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

    async def test_live_version_endpoint_and_uncached_index(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        html = await response.text()
        self.assertIn('name="kroog-release" content="'+shop.RELEASE+'"', html)
        release = await self.client.get("/api/release")
        self.assertEqual(release.status, 200)
        self.assertEqual((await release.json())["release"], shop.RELEASE)
        self.assertIn("no-store", release.headers.get("Cache-Control", ""))

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


    async def test_supplier_colours_visible_and_stock_preserved(self):
        # Existing stock is never reset during catalogue migrations.
        database.init_db()
        self.assertEqual(database.get_color(1)["stock"], 5)
        response = await self.client.get("/api/catalog", headers=self.headers(43))
        self.assertEqual(response.status, 200)
        public = await response.json()
        self.assertFalse(public["admin"])
        by_name = {p["name"]: p for p in public["colors"]}
        self.assertGreaterEqual(len(by_name), 32)
        self.assertEqual(by_name["Оранжевый прозрачный"]["atlas"], 24)
        self.assertEqual(by_name["Прозрачный"]["price"], 3900)
        self.assertEqual(by_name["Ночной переливающийся"]["price"], 6500)
        self.assertEqual(by_name["Чёрный"]["stock"], 5)
        self.assertEqual(by_name["Оранжевый прозрачный"]["stock"], 0)

    async def test_out_of_stock_inquiry_persists_and_is_idempotent(self):
        response = await self.client.get("/api/catalog", headers=self.headers(43))
        catalog = await response.json()
        orange = next(p for p in catalog["colors"]
                      if p["name"] == "Оранжевый прозрачный")
        self.assertEqual(orange["stock"], 0)
        request = {
            "color": orange["id"], "contact": "@client",
            "request_id": str(uuid.uuid4())
        }
        first = await self.client.post(
            "/api/inquiries", json=request, headers=self.headers(43))
        self.assertEqual(first.status, 201, await first.text())
        inquiry_id = (await first.json())["id"]
        second = await self.client.post(
            "/api/inquiries", json=request, headers=self.headers(43))
        self.assertEqual(second.status, 200, await second.text())
        self.assertEqual((await second.json())["id"], inquiry_id)
        with database.get_connection() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM shop_inquiries WHERE id=?", (inquiry_id,)
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(database.get_color(orange["id"])["stock"], 0)

    async def test_inquiry_rejects_available_colour(self):
        request = {"color": 1, "contact": "@client", "request_id": str(uuid.uuid4())}
        response = await self.client.post(
            "/api/inquiries", json=request, headers=self.headers(43))
        self.assertEqual(response.status, 409)

    async def test_checkout_uses_per_colour_price(self):
        with database.get_connection() as db, db:
            row = db.execute("SELECT id FROM colors WHERE name=?", ("Прозрачный",)).fetchone()
        self.assertIsNotNone(row)
        database.set_stock(row["id"], 2)
        params = self.params()
        params.update(color=row["id"], quantity=1, sleeve=False, label=False)
        response = await self.submit(params, files=False)
        self.assertEqual(response.status, 201, await response.text())
        order_id = (await response.json())["id"]
        with database.get_connection() as db:
            details = json.loads(db.execute(
                "SELECT details FROM shop_orders WHERE id=?", (order_id,)
            ).fetchone()["details"])
        self.assertEqual(details["unit_price"], 3900)
        self.assertEqual(details["total"], 3900)


    async def test_current_physical_stock_and_incoming_colours(self):
        available={"Красный марбл": 10, "Прозрачный": 10,
                   "Голубой марбл": 10}
        incoming={
            "Синий прозрачный", "Оранжевый прозрачный",
            "Зелёный марбл", "Розовый «Барби»",
            "Фиолетовый марбл", "Ночной переливающийся",
            "Жёлтый прозрачный", "Красный прозрачный", "Древесный",
        }
        with database.get_connection() as db:
            rows=db.execute("SELECT name,stock,incoming FROM colors").fetchall()
        stock={r["name"]: r["stock"] for r in rows}
        flags={r["name"] for r in rows if r["incoming"]}
        for name, count in available.items():
            self.assertEqual(stock[name], count)
        self.assertEqual(stock["Чёрный"], 5)  # test setup changes the confirmed 10 to 5
        self.assertEqual(flags, incoming)
        for r in rows:
            if r["name"] not in available and r["name"] != "Чёрный":
                self.assertEqual(r["stock"], 0)

    async def test_restart_does_not_reset_revised_stock(self):
        with database.get_connection() as db, db:
            row=db.execute("SELECT id FROM colors WHERE name=?", ("Красный марбл",)).fetchone()
        database.set_stock(row["id"], 7)
        database.init_db()
        self.assertEqual(database.get_color(row["id"])["stock"], 7)

    async def test_incoming_is_not_orderable_and_is_first_out_of_stock(self):
        resp=await self.client.get("/api/catalog",headers=self.headers(43))
        self.assertEqual(resp.status,200)
        colors=(await resp.json())["colors"]
        upcoming=[r for r in colors if r["incoming"]]
        self.assertEqual(len(upcoming),9)
        self.assertTrue(all(r["stock"]==0 for r in upcoming))
        seen_unavailable=False
        for c in colors:
            if c["stock"]==0 and not c["incoming"]:
                seen_unavailable=True
            elif c["incoming"]:
                self.assertFalse(seen_unavailable)
        response=await self.submit(dict(self.params(),color=upcoming[0]["id"]),files=False)
        self.assertEqual(response.status,409)

    async def test_admin_can_mark_incoming_and_receive_stock(self):
        with database.get_connection() as db:
            row=db.execute("SELECT id,stock,incoming FROM colors WHERE name=?", ("Жёлтый",)).fetchone()
        self.assertEqual(row["stock"],0)
        resp=await self.client.post("/api/stock",json={
            "color":row["id"],"stock":0,"previous":0,
            "incoming":True,"previous_incoming":False,
        },headers=self.headers(42))
        self.assertEqual(resp.status,200,await resp.text())
        self.assertEqual(database.get_color(row["id"])["incoming"],1)
        resp=await self.client.post("/api/stock",json={
            "color":row["id"],"stock":10,"previous":0,
            "incoming":True,"previous_incoming":True,
        },headers=self.headers(42))
        self.assertEqual(resp.status,200,await resp.text())
        self.assertEqual(database.get_color(row["id"])["stock"],10)
        self.assertEqual(database.get_color(row["id"])["incoming"],0)


    async def test_music_plan_is_saved_for_manager(self):
        data = dict(self.params(), sleeve=False, label=False, rpm=45,
                    tracks_a="Первый трек\nВторой трек",
                    tracks_b="Финал", audio_link="https://example.com/my-audio")
        response = await self.submit(data, files=False)
        self.assertEqual(response.status, 201, await response.text())
        order_id = (await response.json())["id"]
        private = await self.client.get(
            f"/api/orders/{order_id}", headers=self.headers(42))
        self.assertEqual(private.status, 200)
        details = await private.json()
        self.assertEqual(details["rpm"], 45)
        self.assertEqual(details["tracklist_a"],
                         ["A1 — Первый трек", "A2 — Второй трек"])
        self.assertEqual(details["tracklist_b"], ["B1 — Финал"])
        self.assertEqual(details["audio_link"], "https://example.com/my-audio")

    async def test_invalid_music_options_do_not_reserve_stock(self):
        for invalid in ({"rpm": 78}, {"rpm": "45"},
                        {"audio_link": "file:///etc/passwd"},
                        {"tracks_a": "x" * 1001}):
            with self.subTest(invalid=invalid):
                data = dict(self.params(), sleeve=False, label=False, **invalid)
                response = await self.submit(data, files=False)
                self.assertEqual(response.status, 400, await response.text())
                self.assertEqual(database.get_color(1)["stock"], 5)

    async def test_original_supplied_photos_are_present(self):
        from pathlib import Path
        base=Path(__file__).resolve().parents[1]/"miniapp"/"assets"/"supplier"
        photos=sorted(base.glob("*.jpg"))
        self.assertEqual(len(photos),32)
        for image in photos:
            with self.subTest(image=image.name):
                data=image.read_bytes()
                self.assertTrue(data.startswith(b"\xff\xd8"), image.name)
                self.assertTrue(data.endswith(b"\xff\xd9"), image.name)
                self.assertGreater(len(data),30000)

    async def test_clean_ui_and_form_position(self):
        from pathlib import Path
        base=Path(__file__).resolve().parents[1]/"miniapp"
        page=(base/"index.html").read_text(encoding="utf-8")
        script=(base/"assets"/"shop.js").read_text(encoding="utf-8")
        self.assertLess(page.index('id="aboutNav"'),page.index('id="worksNav"'))
        self.assertNotIn('id="incomingSection"', page)
        self.assertIn('id="audioSetup"',page)
        self.assertNotIn('id="tracksA"',page)
        self.assertNotIn('id="sleeveFiles"',page)
        self.assertNotIn("brand-mascot",page)
        self.assertNotIn("brand-hero",page)
        self.assertNotIn("Твоя пластинка</h1>",page)
        self.assertNotIn(" ↗",page)
        self.assertIn("/assets/supplier/",script)
        self.assertNotIn("backgroundSize",script)
        self.assertIn('id="ambientBg"',page)
        self.assertTrue((base/"assets"/"white-violet-lines-hq.mp4").exists())
        self.assertTrue((base/"assets"/"white-violet-bg.png").exists())


if __name__ == "__main__":
    unittest.main()
