"""Production-only boundaries reuse the real authenticated service fixture."""
from dataclasses import replace
from unittest import TestCase

from src.server.config import ServerConfig
from tests.test_server_api import ServerFixture


class ProductionConfigTest(TestCase):
    def test_production_requires_https_and_limited_proxy_network(self):
        for origin, networks in [('http://docs.example.com', ('172.30.156.0/24',)),
                                 ('https://docs.example.com', ('0.0.0.0/0',)),
                                 ('https://docs.example.com/path', ('172.30.156.0/24',))]:
            with self.subTest(origin=origin, networks=networks), self.assertRaises(ValueError):
                ServerConfig('unused', mode='production', host='0.0.0.0', public_origin=origin,
                             trusted_proxy_cidrs=networks)


class ProductionAPITest(ServerFixture):
    async def test_https_cookie_and_readiness_do_not_expose_identity(self):
        self.app.state.config = replace(self.config, mode='production', host='0.0.0.0',
                                        public_origin='https://docs.example.com',
                                        trusted_proxy_cidrs=('172.30.156.0/24',))
        self.client.headers['host'] = 'docs.example.com'
        self.client.headers['origin'] = 'https://docs.example.com'
        # Transport guard holds startup config; create a real production app.
        from src.server.app import create_app
        import httpx
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.app = create_app(self.app.state.config)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                       base_url='https://docs.example.com')
        health = await self.client.get('/api/healthz')
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {'status': 'ok', 'build_sha': 'dev'})
        self.client.headers['origin'] = 'https://docs.example.com'
        response = await self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertEqual((await self.client.get('/api/session')).status_code, 200)
        self.assertEqual((await self.client.get('/api/healthz', headers={'origin':'https://evil.example'})).status_code, 403)

    async def test_readiness_rejects_missing_database_without_recreating_it(self):
        path = self.config.database_path
        moved = path.with_suffix('.saved')
        path.rename(moved)
        try:
            response = await self.client.get('/api/healthz')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(path.exists())
            self.assertNotIn(str(path), response.text)
        finally:
            moved.rename(path)


class TrustedProxyTest(TestCase):
    def test_only_trusted_peer_can_supply_identity_source(self):
        import asyncio
        import httpx
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        from src.server.auth import parse_identity

        async def app(scope, receive, send):
            await JSONResponse({'source': parse_identity(Request(scope)).source})(scope, receive, send)

        async def check():
            protected = ProxyHeadersMiddleware(app, trusted_hosts='172.30.156.0/24')
            for peer, expected in [('172.30.156.2', '203.0.113.7'), ('192.168.1.2', '192.168.1.2')]:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=protected, client=(peer, 1)), base_url='http://example') as client:
                    response = await client.get('/', headers={'X-Forwarded-For': '203.0.113.7'})
                    self.assertEqual(response.json(), {'source': expected})
        asyncio.run(check())
