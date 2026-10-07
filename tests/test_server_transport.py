"""Transport failures are bounded, strict and sanitized."""
from unittest.mock import patch
from src.core.errors import Conflict, RateLimited, StorageBusy
from tests.test_server_api import ServerFixture

class ServerTransportTest(ServerFixture):
    async def test_json_and_query_are_strict(self):
        for body in ('{"login_name":"admin","login_name":"other","password":"secret"}', '{"login_name":NaN}', '{"a":Infinity}', '{"a":1e999}', '[]', '{'):
            self.assert_error(await self.client.post('/api/auth/login', content=body), 400)
        for body in ({'login_name': 'admin', 'password': 'secret', 'extra': True}, {'login_name': 123, 'password': 'secret'}):
            self.assert_error(await self.client.post('/api/auth/login', json=body), 422)
        self.assert_error(await self.client.get('/api/bootstrap?x=1&x=2'), 400)
        self.assert_error(await self.client.get('/api/bootstrap?unknown=1'), 400)

    async def test_oversize_stream_without_trusted_length(self):
        async def chunks():
            yield b'{"password":"'
            for _ in range(33):
                yield b'x' * 65536
            yield b'"}'
        self.assert_error(await self.client.post('/api/auth/login', content=chunks()), 413)

    async def test_errors_do_not_leak_sensitive_input(self):
        sentinel = 'SENSITIVE-password-body-sentinel'
        response = await self.client.post('/api/auth/login', json={'login_name': 3, 'password': sentinel})
        self.assert_error(response, 422)
        self.assertNotIn(sentinel, response.text)
        with patch.object(type(self.app.state.services.content), 'bootstrap', side_effect=RuntimeError(sentinel)):
            with self.assertLogs('src.server.errors', level='ERROR') as logs:
                response = await self.client.get('/api/bootstrap')
        self.assert_error(response, 500)
        self.assertNotIn(sentinel, response.text)
        self.assertNotIn(sentinel, '\n'.join(logs.output))

    async def test_domain_errors_are_sanitized_and_retryable(self):
        for error, status in ((Conflict('private object'), 409), (StorageBusy('private storage'), 503),
                              (RateLimited('private user', details={'retry_after': 7}), 429)):
            with patch.object(type(self.app.state.services.content), 'bootstrap', side_effect=error):
                response = await self.client.get('/api/bootstrap')
            self.assert_error(response, status)
            self.assertNotIn('private', response.text)
            if status == 429:
                self.assertEqual(response.headers['retry-after'], '7')
