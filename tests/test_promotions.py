"""Promo quote, authorization and redemption use the real SQLite order path."""
import asyncio
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urlencode
import uuid

from aiohttp.test_utils import TestClient, TestServer
import config
import database
import shop


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=(database.DB_PATH,config.BOT_TOKEN,config.ADMIN_ID)
        database.DB_PATH=str(Path(self.tmp.name)/"data/kroog.db")
        config.BOT_TOKEN="123456:TEST_ONLY";config.ADMIN_ID=42
        database.init_db();shop.setup();database.set_stock(1,20)
        self.client=TestClient(TestServer(shop.application()));await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        database.DB_PATH,config.BOT_TOKEN,config.ADMIN_ID=self.old
        self.tmp.cleanup()

    def headers(self,user=43):
        fields={"auth_date":str(int(time.time())),"user":json.dumps({"id":user,"first_name":"Test"})}
        signed="\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        secret=hmac.digest(b"WebAppData",config.BOT_TOKEN.encode(),"sha256")
        fields["hash"]=hmac.new(secret,signed.encode(),"sha256").hexdigest()
        return {"X-Telegram-Init-Data":urlencode(fields)}

    def params(self,**changes):
        return dict(color=1,quantity=1,sleeve=False,label=False,rush=False,contact="@tester",
                    request_id=str(uuid.uuid4())) | changes

    async def create(self,**changes):
        response=await self.client.post('/api/promos',json=dict(code='FRIENDS_TEST',percent=20)|changes,
                                        headers=self.headers(42))
        self.assertEqual(response.status,200,await response.text())
        return (await response.json())["promos"][0]

    async def quote(self,**changes):
        return await self.client.post('/api/quote',json=self.params(**(dict(promo_code='FRIENDS_TEST') | changes)),headers=self.headers())

    async def place(self,user=43,**changes):
        return await self.client.post('/api/orders',json=self.params(**(dict(promo_code='FRIENDS_TEST') | changes)),headers=self.headers(user))

    def count_uses(self):
        with closing(database.get_connection()) as db:
            return db.execute('SELECT COUNT(*) FROM promotion_uses WHERE released=0').fetchone()[0]

    async def test_only_admin_can_create_list_and_toggle_promos(self):
        for method,path,data in [('get','/api/promos',None),('post','/api/promos',{'code':'NOPE','percent':10}),('post','/api/promos/1',{'active':False})]:
            response=await getattr(self.client,method)(path,json=data,headers=self.headers())
            self.assertEqual(response.status,403)
        response=await self.client.get('/api/promos')
        self.assertEqual(response.status,401)

    async def test_only_admin_can_delete_promos(self):
        promo = await self.create()
        for user in (43,):
            response = await self.client.post(
                f'/api/promos/{promo["id"]}', json={"delete": True},
                headers=self.headers(user))
            self.assertEqual(response.status, 403)
        response = await self.client.post(
            f'/api/promos/{promo["id"]}', json={"delete": True})
        self.assertEqual(response.status, 401)
        visible = await self.client.get('/api/promos',headers=self.headers(42))
        self.assertEqual(len((await visible.json())['promos']),1)

    async def test_delete_is_distinct_from_toggling_and_frees_name(self):
        promo = await self.create()
        pid = promo["id"]
        # Toggling off remains reversible.
        off = await self.client.post(
            f'/api/promos/{pid}',json={"active": False},headers=self.headers(42))
        self.assertEqual(off.status,200)
        self.assertFalse((await off.json())["promos"][0]["active"])
        on = await self.client.post(
            f'/api/promos/{pid}',json={"active": True},headers=self.headers(42))
        self.assertEqual(on.status,200)
        self.assertTrue((await on.json())["promos"][0]["active"])
        for malformed in ({"delete": False},{"delete": True,"active": True},{}):
            invalid = await self.client.post(
                f'/api/promos/{pid}',json=malformed,headers=self.headers(42))
            self.assertEqual(invalid.status,400)
        response = await self.client.post(
            f'/api/promos/{pid}',json={"delete": True},headers=self.headers(42))
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())["promos"],[])
        self.assertEqual((await self.quote()).status,400)
        # It is deleted, not merely paused: activation is no longer possible.
        again = await self.client.post(
            f'/api/promos/{pid}',json={"active": True},headers=self.headers(42))
        self.assertEqual(again.status,404)
        missing = await self.client.post(
            f'/api/promos/{pid}',json={"delete": True},headers=self.headers(42))
        self.assertEqual(missing.status,404)
        fresh = await self.create()
        self.assertNotEqual(fresh["id"],pid)
        self.assertEqual(fresh["code"],"FRIENDS_TEST")

    async def test_delete_used_promo_keeps_order_and_usage_history(self):
        promo = await self.create()
        created = await self.place()
        self.assertEqual(created.status,201,await created.text())
        order_id = (await created.json())['id']
        self.assertEqual(self.count_uses(),1)
        response = await self.client.post(
            f'/api/promos/{promo["id"]}',json={"delete": True},
            headers=self.headers(42))
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['promos'],[])
        with closing(database.get_connection()) as db:
            row = db.execute(
                'SELECT * FROM promotion_uses WHERE order_id=?',(order_id,)
            ).fetchone()
            self.assertEqual(row['promo_id'],promo['id'])
            archived = db.execute(
                'SELECT active, deleted_at, code FROM promotions WHERE id=?',
                (promo['id'],)).fetchone()
            self.assertEqual(archived['active'],0)
            self.assertIsNotNone(archived['deleted_at'])
            self.assertTrue(archived['code'].startswith('~deleted:'))
        saved = await self.client.get(
            f'/api/orders/{order_id}',headers=self.headers(42))
        self.assertEqual((await saved.json())['promo_code'],'FRIENDS_TEST')
        self.assertEqual((await saved.json())['total'],4000)

    async def test_quote_applies_one_code_without_reserving_anything(self):
        await self.create()
        response=await self.quote(promo_code=' friends_test ')
        self.assertEqual(response.status,200,await response.text())
        quote=await response.json()
        self.assertEqual(quote['total'],4000)
        self.assertEqual(quote['promo_code'],'FRIENDS_TEST')
        self.assertEqual(self.count_uses(),0)
        self.assertEqual(database.get_color(1)['stock'],20)

    async def test_order_discount_is_persisted_and_retry_does_not_consume_again(self):
        await self.create(max_uses=1)
        token=str(uuid.uuid4())
        first=await self.place(request_id=token)
        self.assertEqual(first.status,201,await first.text())
        order_id=(await first.json())['id']
        retried=await self.place(request_id=token)
        self.assertEqual(retried.status,200)
        self.assertEqual((await retried.json())['id'],order_id)
        self.assertEqual(self.count_uses(),1)
        self.assertEqual(database.get_color(1)['stock'],19)
        response=await self.client.get(f'/api/orders/{order_id}',headers=self.headers(42))
        saved=await response.json()
        self.assertEqual(saved['total'],4000)
        self.assertEqual(saved['promo_percent'],20)
        self.assertTrue(saved['promo_applied'])

    async def test_one_use_per_person_is_enforced(self):
        await self.create()
        self.assertEqual((await self.place()).status,201)
        again=await self.place()
        self.assertEqual(again.status,400)
        self.assertIn('уже использовали',await again.text())
        self.assertEqual(self.count_uses(),1)

    async def test_concurrent_orders_cannot_exceed_global_limit(self):
        await self.create(max_uses=1)
        responses=await asyncio.gather(self.place(user=43),self.place(user=44))
        self.assertEqual(sorted(r.status for r in responses),[201,400])
        self.assertEqual(self.count_uses(),1)
        self.assertEqual(database.get_color(1)['stock'],19)

    async def test_code_expired_between_quote_and_order_does_not_reserve_stock(self):
        await self.create(expires_at=int(time.time())+60)
        self.assertEqual((await self.quote()).status,200)
        with closing(database.get_connection()) as db,db:
            db.execute('UPDATE promotions SET expires_at=?',(int(time.time())-1,))
        response=await self.place()
        self.assertEqual(response.status,400)
        self.assertIn('истёк',await response.text())
        self.assertEqual(database.get_color(1)['stock'],20)
        self.assertEqual(self.count_uses(),0)

    async def test_paused_code_is_rejected_at_checkout_and_can_be_enabled(self):
        promo=await self.create()
        await self.client.post(f'/api/promos/{promo["id"]}',json={'active':False},headers=self.headers(42))
        self.assertEqual((await self.place()).status,400)
        await self.client.post(f'/api/promos/{promo["id"]}',json={'active':True},headers=self.headers(42))
        self.assertEqual((await self.quote()).status,200)

    async def test_personal_code_only_works_for_its_recipient(self):
        await self.create(user_id=44)
        self.assertEqual((await self.quote()).status,400)
        response=await self.place(user=44)
        self.assertEqual(response.status,201,await response.text())

    async def test_repeat_order_code_requires_a_previous_non_cancelled_order(self):
        await self.create(returning_only=True)
        self.assertEqual((await self.quote()).status,400)
        regular=await self.place(promo_code='')
        self.assertEqual(regular.status,201)
        self.assertEqual((await self.quote()).status,200)
        order_id=(await regular.json())['id']
        await self.client.post(f'/api/orders/{order_id}/cancel',headers=self.headers(42))
        self.assertEqual((await self.quote()).status,400)

    async def test_cancellation_releases_usage_once_and_keeps_order_history(self):
        await self.create(max_uses=1)
        created=await self.place();order_id=(await created.json())['id']
        for _ in range(2):
            response=await self.client.post(f'/api/orders/{order_id}/cancel',headers=self.headers(42))
            self.assertEqual(response.status,200)
        self.assertEqual(database.get_color(1)['stock'],20)
        self.assertEqual(self.count_uses(),0)
        response=await self.client.get(f'/api/orders/{order_id}',headers=self.headers(42))
        self.assertEqual((await response.json())['total'],4000)
        self.assertEqual((await self.place()).status,201)

    async def test_quantity_discount_and_promo_are_not_stacked(self):
        await self.create(percent=10)
        response=await self.quote(quantity=5)
        quote=await response.json()
        self.assertEqual(quote['volume_percent'],5)
        self.assertEqual(quote['percent'],10)
        self.assertEqual(quote['total'],22500)

    async def test_better_quantity_discount_does_not_consume_weaker_promo(self):
        await self.create(percent=3)
        response=await self.place(quantity=5)
        self.assertEqual(response.status,201)
        order_id=(await response.json())['id']
        saved=await self.client.get(f'/api/orders/{order_id}',headers=self.headers(42))
        data=await saved.json()
        self.assertEqual(data['total'],23750)
        self.assertFalse(data['promo_applied'])
        self.assertEqual(self.count_uses(),0)

    async def test_hundred_percent_code_has_zero_total(self):
        await self.create(percent=100)
        response=await self.quote()
        self.assertEqual((await response.json())['total'],0)

    async def test_rounding_matches_python_ties_to_even(self):
        await self.create(percent=5)
        response=await self.quote(rush=True)
        data=await response.json()
        self.assertEqual(data['subtotal'],6250)
        self.assertEqual(data['discount_rub'],312)
        self.assertEqual(data['total'],5938)

    async def test_multiple_or_unknown_codes_are_rejected(self):
        await self.create()
        for code in (['FRIENDS_TEST','OTHER'],'FRIENDS_TEST OTHER','UNKNOWN'):
            response=await self.quote(promo_code=code)
            self.assertEqual(response.status,400)
        self.assertEqual(self.count_uses(),0)

    async def test_creation_validates_discount_limit_dates_and_unique_name(self):
        for values in ({'percent':0},{'percent':101},{'percent':True},{'max_uses':0},
                       {'expires_at':int(time.time())-1},{'user_id':-1},{'code':'bad code'}):
            response=await self.client.post('/api/promos',json=dict(code='FRIENDS_TEST',percent=20)|values,headers=self.headers(42))
            self.assertEqual(response.status,400,values)
        await self.create()
        response=await self.client.post('/api/promos',json={'code':'friends_test','percent':20},headers=self.headers(42))
        self.assertEqual(response.status,409)

    async def test_client_cannot_supply_its_own_discount(self):
        response=await self.place(promo_code='',promo_percent=100,total=0)
        self.assertEqual(response.status,201)
        order_id=(await response.json())['id']
        saved=await self.client.get(f'/api/orders/{order_id}',headers=self.headers(42))
        self.assertEqual((await saved.json())['total'],5000)

    async def test_failed_order_does_not_consume_code(self):
        await self.create(max_uses=1)
        response=await self.place(quantity=21)
        self.assertEqual(response.status,409)
        self.assertEqual(self.count_uses(),0)
        self.assertEqual((await self.place()).status,201)


if __name__=='__main__':unittest.main()
