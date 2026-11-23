"""Attacker-controlled requests against an isolated SQLite DB, never production."""
import asyncio
import json
import unittest
from urllib.parse import parse_qsl, urlencode
from aiohttp import FormData
import test_miniapp as fixtures
import database
import shop
import artwork


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.MiniAppOrderTests.asyncSetUp
    asyncTearDown = fixtures.MiniAppOrderTests.asyncTearDown
    headers = fixtures.MiniAppOrderTests.headers
    params = fixtures.MiniAppOrderTests.params

    async def test_all_private_routes_require_authentication(self):
        routes = [('GET','/api/catalog'), ('GET','/api/orders/1'),
                  ('GET','/api/orders/1/artwork/label_a'), ('GET','/api/promos'),
                  ('POST','/api/orders'), ('POST','/api/quote'),
                  ('POST','/api/orders/1/cancel'), ('POST','/api/stock'),
                  ('POST','/api/promos'), ('POST','/api/promos/1'), ('POST','/api/inquiries')]
        for method,path in routes:
            response = await self.client.request(method,path,json={})
            self.assertEqual(response.status,401,path)
            self.assertIn('no-store',response.headers['Cache-Control'])

    async def test_cannot_forge_admin_or_expiry_or_duplicate_signed_fields(self):
        original = dict(parse_qsl(self.headers(43)['X-Telegram-Init-Data']))
        variants = [dict(original,user=json.dumps({'id':42,'is_admin':True})),
                    dict(original,auth_date='0'), dict(original,hash='0'*64)]
        for fields in variants:
            response = await self.client.get('/api/promos',headers={
                'X-Telegram-Init-Data':urlencode(fields)})
            self.assertEqual(response.status,401)
        response=await self.client.get('/api/catalog',headers={
            'X-Telegram-Init-Data':urlencode(original)+'&user=%7B%22id%22%3A42%7D'})
        self.assertEqual(response.status,401)

    async def test_customer_cannot_read_cancel_or_edit_admin_data(self):
        for method,path,data in [
            ('GET','/api/orders/1',None),('POST','/api/orders/1/cancel',{}),
            ('GET','/api/orders/99999',None),('POST','/api/stock',{'stock':999}),
            ('GET','/api/promos',None),('POST','/api/promos',{'percent':100})]:
            response=await self.client.request(method,path,json=data,headers=self.headers(43))
            self.assertEqual(response.status,403,path)
        self.assertEqual(database.get_color(1)['stock'],5)

    async def test_price_admin_and_payment_flags_are_not_trusted(self):
        data=self.params() | {'total':1,'unit_price':1,'discount_rub':99999,
                            'admin':True,'user_id':42,'paid':True,'payment_status':'paid'}
        response=await self.client.post('/api/orders',json=data,headers=self.headers(43))
        self.assertEqual(response.status,201)
        order_id=(await response.json())['id']
        response=await self.client.get(f'/api/orders/{order_id}',headers=self.headers(42))
        details=await response.json()
        self.assertGreater(details['total'],1)
        self.assertEqual(details['user_id'],43)
        self.assertFalse(details['paid'])
        self.assertIsNone(details['paid_at'])
        for field in ['admin','payment_status']:self.assertNotIn(field,details)

    async def test_negative_zero_bool_and_fractional_quantities_rejected(self):
        for value in [-1,0,True,1.5,'-1']:
            response=await self.client.post('/api/orders',json=self.params()|{'quantity':value},headers=self.headers(43))
            self.assertEqual(response.status,400)
        self.assertEqual(database.get_color(1)['stock'],5)

    async def test_renamed_script_upload_rejected_and_deleted(self):
        form=FormData()
        form.add_field('payload',json.dumps(self.params()))
        form.add_field('sleeve_front',b'<script>alert(1)</script>',filename='photo.jpg',content_type='image/jpeg')
        response=await self.client.post('/api/orders',data=form,headers=self.headers(43))
        self.assertEqual(response.status,400)
        self.assertEqual(list(artwork.directory().iterdir()),[])
        self.assertEqual(database.get_color(1)['stock'],5)

    async def test_large_json_and_multipart_payloads_rejected(self):
        response=await self.client.post('/api/quote',json={'padding':'x'*20000},headers=self.headers(43))
        self.assertEqual(response.status,413)
        form=FormData()
        form.add_field('payload','x'*20000,content_type='application/json')
        form.add_field('label_a',b'%PDF-test',filename='a.pdf')
        response=await self.client.post('/api/orders',data=form,headers=self.headers(43))
        self.assertEqual(response.status,413)
        self.assertEqual(list(artwork.directory().iterdir()),[])

    async def test_order_flood_limited_per_verified_user_and_survives_restart(self):
        for _ in range(10):
            response=await self.client.post('/api/orders',json={},headers=self.headers(43))
            self.assertEqual(response.status,400)
        shop.setup()
        response=await self.client.post('/api/orders',json={},headers=self.headers(43))
        self.assertEqual(response.status,429)
        self.assertIn('Retry-After',response.headers)
        response=await self.client.post('/api/orders',json={},headers=self.headers(44))
        self.assertEqual(response.status,400)

    async def test_concurrent_reservations_never_overdraw_stock(self):
        async def send():
            return await self.client.post('/api/orders',json=self.params()|{'quantity':3},headers=self.headers(43))
        responses=await asyncio.gather(send(),send())
        self.assertEqual(sorted(r.status for r in responses),[201,409])
        self.assertEqual(database.get_color(1)['stock'],2)

    async def test_script_and_sql_text_are_stored_as_text(self):
        contact="<script>alert(1)</script>'; DROP TABLE colors;--"
        response=await self.client.post('/api/orders',json=self.params()|{'contact':contact},headers=self.headers(43))
        self.assertEqual(response.status,201)
        self.assertEqual(database.get_color(1)['stock'],3)
        response=await self.client.get('/api/orders/'+str((await response.json())['id']),headers=self.headers(42))
        self.assertEqual((await response.json())['contact'],contact)
        self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
