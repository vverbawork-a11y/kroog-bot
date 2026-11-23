"""Reservation expiry, manual payment and migration safety."""
import asyncio
import time
import unittest
import test_miniapp as fixtures
import database
import shop

class ReservationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.MiniAppOrderTests.asyncSetUp
    asyncTearDown = fixtures.MiniAppOrderTests.asyncTearDown
    headers = fixtures.MiniAppOrderTests.headers
    params = fixtures.MiniAppOrderTests.params

    async def create(self):
        response=await self.client.post('/api/orders',json=self.params(),headers=self.headers(43))
        self.assertEqual(response.status,201,await response.text())
        return (await response.json())['id']

    def expire_at(self,order_id,deadline):
        with database.get_connection() as db,db:
            db.execute('UPDATE shop_orders SET expires_at=? WHERE id=?',(deadline,order_id))

    def sweep_at(self,now):
        with database.get_connection() as db,db:
            db.execute('BEGIN IMMEDIATE')
            return shop.expire_orders(db,now)

    async def test_deadline_is_24_hours_and_stock_returns_exactly_once(self):
        before=int(time.time());oid=await self.create()
        with database.get_connection() as db:
            deadline=db.execute('SELECT expires_at FROM shop_orders WHERE id=?',(oid,)).fetchone()[0]
        self.assertTrue(before+86400<=deadline<=int(time.time())+86400)
        self.assertEqual(self.sweep_at(deadline-1),0)
        self.assertEqual(database.get_color(1)['stock'],3)
        self.assertEqual(self.sweep_at(deadline),1)
        self.assertEqual(self.sweep_at(deadline+1),0)
        self.assertEqual(database.get_color(1)['stock'],5)
        response=await self.client.get(f'/api/orders/{oid}',headers=self.headers(42))
        self.assertEqual((await response.json())['cancel_reason'],'expired')

    async def test_only_admin_can_mark_paid_and_paid_order_never_expires(self):
        oid=await self.create()
        response=await self.client.post(f'/api/orders/{oid}/paid',json={},headers=self.headers(43))
        self.assertEqual(response.status,403)
        response=await self.client.post(f'/api/orders/{oid}/paid',json={})
        self.assertEqual(response.status,401)
        for _ in range(2):
            response=await self.client.post(f'/api/orders/{oid}/paid',json={},headers=self.headers(42))
            self.assertEqual(response.status,200)
        self.assertEqual(self.sweep_at(int(time.time())+100000),0)
        self.assertEqual(database.get_color(1)['stock'],3)
        response=await self.client.get(f'/api/orders/{oid}',headers=self.headers(42))
        self.assertTrue((await response.json())['paid'])

    async def test_expired_order_cannot_be_paid_or_return_stock_twice(self):
        oid=await self.create();self.expire_at(oid,int(time.time())-1)
        response=await self.client.post(f'/api/orders/{oid}/paid',json={},headers=self.headers(42))
        self.assertEqual(response.status,409)
        self.assertEqual(database.get_color(1)['stock'],5)
        await self.client.post(f'/api/orders/{oid}/cancel',json={},headers=self.headers(42))
        self.assertEqual(database.get_color(1)['stock'],5)

    async def test_previous_orders_without_deadlines_are_not_cancelled(self):
        oid=await self.create();self.expire_at(oid,None)
        shop.setup()
        self.assertEqual(self.sweep_at(int(time.time())+1000000),0)
        self.assertEqual(database.get_color(1)['stock'],3)

    async def test_expired_stock_is_available_when_catalog_is_loaded(self):
        oid=await self.create();self.expire_at(oid,int(time.time())-1)
        response=await self.client.get('/api/catalog',headers=self.headers(43))
        self.assertEqual(response.status,200)
        self.assertEqual(database.get_color(1)['stock'],5)

    async def test_expiry_releases_coupon(self):
        import promotions
        with database.get_connection() as db,db:
            promotions.create(db,{'code':'RESERVE20','percent':20,'max_uses':1})
        response=await self.client.post('/api/orders',json=self.params()|{'promo_code':'RESERVE20'},headers=self.headers(43))
        oid=(await response.json())['id'];self.expire_at(oid,int(time.time())-1)
        shop.sweep_expired_orders()
        with database.get_connection() as db:
            self.assertEqual(db.execute('SELECT released FROM promotion_uses WHERE order_id=?',(oid,)).fetchone()[0],1)
            self.assertEqual(promotions.validate(db,'RESERVE20',44)['code'],'RESERVE20')

    async def test_payment_and_expiry_race_has_one_consistent_result(self):
        oid=await self.create();self.expire_at(oid,int(time.time())-1)
        async def pay():
            return await self.client.post(f'/api/orders/{oid}/paid',json={},headers=self.headers(42))
        async def view():
            return await self.client.get('/api/catalog',headers=self.headers(43))
        payment,_=await asyncio.gather(pay(),view())
        self.assertEqual(payment.status,409)
        self.assertEqual(database.get_color(1)['stock'],5)
