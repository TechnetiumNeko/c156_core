"""Transport failures are bounded, strict and sanitized."""
from unittest.mock import patch
import httpx
import traceback
from src.server.errors import UnexpectedErrorBoundary
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
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 1234)), base_url='http://127.0.0.1:8001') as client:
                    response = await client.get('/api/bootstrap')
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

    async def test_failure_after_headers_aborts_without_original_exception(self):
        sentinel = 'SENSITIVE-stream-error'
        messages = []
        async def failing_app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'partial', 'more_body': True})
            raise ValueError(sentinel)
        async def receive():
            return {'type': 'http.request', 'body': b''}
        async def send(message):
            messages.append(message)
        with self.assertLogs('src.server.errors', level='ERROR') as logs:
            try:
                await UnexpectedErrorBoundary(failing_app)({'type': 'http'}, receive, send)
            except RuntimeError as error:
                rendered = ''.join(traceback.format_exception(error))
            else:
                self.fail('An incomplete response must abort.')
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn(sentinel, '\n'.join(logs.output))
        self.assertEqual([message['type'] for message in messages], ['http.response.start', 'http.response.body'])

    async def test_history_and_operation_fields_remain_strict(self):
        login = await self.login()
        headers = {'X-C156-CSRF': login.json()['csrf']}
        save = {'object_id': 'doc', 'content': '', 'expected_revision_id': 'r1', 'operation_id': 'op'}
        restore = {'object_id': 'doc', 'source_revision_id': 'r1', 'expected_revision_id': 'r2', 'operation_id': 'op'}
        for method, path, body in ((self.client.put, '/api/document', save),
                                   (self.client.post, '/api/document/restore', restore)):
            for extra in ({'scope': {}}, {'operation_id': None}, {'operation_id': 5}, {'expected_revision_id': False}):
                self.assert_error(await method(path, json={**body, **extra}, headers=headers), 422)
            self.assert_error(await method(path + '?unexpected=1', json=body, headers=headers), 400)
            self.assert_error(await method(path, content='{"operation_id":"a","operation_id":"b"}', headers=headers), 400)
        for path in ('/api/document/history?object_id=x&limit=1&limit=2',
                     '/api/document/history?object_id=x&other=1',
                     '/api/document/history?limit=1', '/api/documents/deleted?object_id=x',
                     '/api/document/revision?object_id=x', '/api/document/diff?object_id=x',
                     '/api/document/operation?object_id=x&operation_id=y&operation_id=z'):
            self.assert_error(await self.client.get(path), 400)
        for limit in ('0', '-1', '101', '1.0', 'true', '01', ''):
            self.assert_error(await self.client.get('/api/document/history', params={'object_id': 'x', 'limit': limit}), 400)
