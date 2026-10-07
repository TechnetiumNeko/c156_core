"""New adapter authentication uses real services on disposable databases."""
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
import httpx
from src.server.app import create_app
from src.server.config import ServerConfig
from src.storage import Database
from src.storage.management import initialize_database
from src.services.bootstrap import bootstrap_admin

class ServerFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        path = Path(self.temp.name) / 'library.sqlite'
        initialize_database(path)
        self.password = 'server-test-password-123'
        bootstrap_admin(Database(path), 'admin', 'Administrator', self.password)
        self.config = ServerConfig(path)
        self.app = create_app(self.config)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 1234), raise_app_exceptions=False), base_url='http://127.0.0.1:8001')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp.cleanup()

    async def login(self, **kwargs):
        nonce = (await self.client.get('/api/bootstrap')).json()['nonce']
        return await self.client.post('/api/auth/login', json={'login_name': 'admin', 'password': self.password}, headers={'X-C156-Nonce': nonce}, **kwargs)

    def assert_error(self, response, status):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(set(response.json()['error']), {'code', 'message', 'details'})
        self.assertEqual(response.json()['error']['details'], {})
        self.assertEqual(response.headers['cache-control'], 'no-store')

class ServerAPITest(ServerFixture):
    async def test_login_session_logout_cookie_contract(self):
        response = await self.login()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('session_token', response.json())
        cookie = response.headers['set-cookie']
        for value in ('HttpOnly', 'SameSite=strict', 'Path=/'):
            self.assertIn(value, cookie)
        self.assertNotIn('Secure', cookie)
        session = await self.client.get('/api/session')
        self.assertEqual(session.json(), response.json())
        bootstrap = await self.client.get('/api/bootstrap')
        self.assertEqual(bootstrap.json()['user'], session.json()['user'])
        logout = await self.client.post('/api/auth/logout', json={}, headers={'X-C156-CSRF': session.json()['csrf']})
        self.assertEqual(logout.json(), {'ok': True})
        self.assertIn('Max-Age=0', logout.headers['set-cookie'])
        self.assert_error(await self.client.get('/api/session'), 401)
        nonce = (await self.client.get('/api/bootstrap')).json()['nonce']
        failures = []
        for name in ('admin', 'unknown'):
            failed = await self.client.post('/api/auth/login', json={'login_name': name, 'password': 'wrong'}, headers={'X-C156-Nonce': nonce})
            self.assert_error(failed, 401)
            failures.append(failed.json())
        self.assertEqual(*failures)

    async def test_host_origin_nonce_and_csrf(self):
        for headers in ({'host': 'evil.example'}, {'origin': 'http://evil.example'}, {'origin': 'null'}):
            self.assert_error(await self.client.get('/api/bootstrap', headers=headers), 403)
        good = await self.client.get('/api/bootstrap', headers={'host': 'localhost:5173', 'origin': 'http://localhost:5173'})
        self.assertEqual(good.status_code, 200)
        self.assert_error(await self.client.post('/api/auth/login', json={'login_name': 'admin', 'password': self.password}), 403)
        await self.login()
        for headers in ({}, {'X-C156-CSRF': 'wrong'}):
            self.assert_error(await self.client.post('/api/auth/logout', json={}, headers=headers), 403)

    async def test_invalid_cookie_is_not_guest(self):
        for token in ('broken', 'x' * 43, 'x' * 43 + '; c156_session=' + 'y' * 43):
            headers = {'cookie': 'c156_session=' + token}
            for path in ('/api/bootstrap', '/api/session'):
                response = await self.client.get(path, headers=headers)
                self.assert_error(response, 401)
                self.assertIn('Max-Age=0', response.headers['set-cookie'])
            response = await self.client.post('/api/auth/login', json={'login_name': 'admin', 'password': self.password}, headers={**headers, 'X-C156-Nonce': self.app.state.services.nonce})
            self.assert_error(response, 401)

    async def test_secure_cookie_configuration(self):
        self.app.state.config = replace(self.config, cookie_secure=True)
        self.assertIn('Secure', (await self.login()).headers['set-cookie'])

    async def test_unknown_api_and_method(self):
        self.assert_error(await self.client.get('/api/unknown'), 404)
        self.assert_error(await self.client.put('/api/session'), 405)
        self.assert_error(await self.client.get('/docs'), 404)
