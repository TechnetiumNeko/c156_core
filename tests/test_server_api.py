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

    async def content_fixture(self, content='原始正文\n\n'):
        login = await self.login()
        self.assertEqual(login.status_code, 200, login.text)
        self.csrf = login.json()['csrf']
        self.token = self.client.cookies['c156_session']
        self.services = self.app.state.services
        self.scope = self.services.scope
        self.doc = self.services.content.create_document(
            self.scope, self.scope.root_id, '中文文档', content=content, session_token=self.token)
        return self.doc

    async def save(self, content, revision=None, headers=None):
        return await self.client.put('/api/document', json={
            'object_id': self.doc.id, 'content': content,
            'expected_revision_id': revision or self.doc.revision_id},
            headers=headers if headers is not None else {'X-C156-CSRF': self.csrf})

    async def test_read_save_read_persists(self):
        doc = await self.content_fixture()
        children = await self.client.get('/api/children', params={'folder_id': self.scope.root_id})
        self.assertEqual(children.status_code, 200, children.text)
        node = next(node for node in children.json()['nodes'] if node['id'] == doc.id)
        self.assertEqual(node['name'], '中文文档')
        self.assertIn('edit', node['access']['actions'])
        read = await self.client.get('/api/document', params={'object_id': doc.id})
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()['document']['content'], doc.content)
        self.assertEqual(read.json()['document']['revision_id'], doc.revision_id)
        updated = '修改后，保留中文和末尾空行。\n\n'
        saved = await self.save(updated)
        self.assertEqual(saved.status_code, 200, saved.text)
        r2 = saved.json()['document']['revision_id']
        self.assertNotEqual(r2, doc.revision_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                    base_url='http://127.0.0.1:8001',
                                    cookies={'c156_session': self.token}) as fresh:
            reread = await fresh.get('/api/document', params={'object_id': doc.id})
        self.assertEqual(reread.json(), saved.json())
        persisted = self.services.content.read_document(self.scope, doc.id, session_token=self.token)
        self.assertEqual((persisted.content, persisted.revision_id), (updated, r2))

    async def test_stale_revision_preserves_server_content(self):
        doc = await self.content_fixture()
        reads = [await self.client.get('/api/document', params={'object_id': doc.id}) for _ in range(2)]
        revisions = [r.json()['document']['revision_id'] for r in reads]
        self.assertEqual(revisions, [doc.revision_id, doc.revision_id])
        saved = await self.save('第一次保存\n', revisions[0])
        self.assertEqual(saved.status_code, 200, saved.text)
        stale = await self.save('过期正文', revisions[1])
        self.assert_error(stale, 409)
        self.assertEqual(stale.json()['error']['code'], 'conflict')
        reread = await self.client.get('/api/document', params={'object_id': doc.id})
        self.assertEqual(reread.json(), saved.json())

    async def test_hidden_read_and_revoked_write(self):
        from src.services.accounts import AccountService
        from src.services.access import AccessService
        from src.storage import Database
        doc = await self.content_fixture()
        access = AccessService(Database(self.config.database_path))
        accounts = AccountService(Database(self.config.database_path))
        grant = accounts.create_user('editor', '编辑成员', session_token=self.token)
        self.services.identity.activate(grant.token, self.password, source='127.0.0.1')
        def version():
            return access.workspace_access(self.scope, session_token=self.token).version
        access.add_member(self.scope, 'editor', 'editor', session_token=self.token, expected_version=version())
        private = self.services.content.create_document(self.scope, self.scope.root_id,
            '私密标题', content='隐藏正文', visibility='private', session_token=self.token)
        member = self.services.identity.login('editor', self.password, source='127.0.0.1')
        self.client.cookies.set('c156_session', member.session_token, domain='127.0.0.1', path='/')
        self.csrf = member.csrf_token
        hidden = await self.client.get('/api/document', params={'object_id': private.id})
        self.assert_error(hidden, 404)
        self.assertNotIn('隐藏正文', hidden.text)
        listing = await self.client.get('/api/children', params={'folder_id': self.scope.root_id})
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn(private.id, [node['id'] for node in listing.json()['nodes']])
        self.assertEqual((await self.client.get('/api/document', params={'object_id': doc.id})).status_code, 200)
        access.set_member_role(self.scope, grant.user.id, 'reader', session_token=self.token, expected_version=version())
        denied = await self.save('权限撤销后的正文')
        self.assert_error(denied, 403)
        self.assertEqual(denied.json()['error']['code'], 'forbidden')
        access.set_member_role(self.scope, grant.user.id, 'editor', session_token=self.token, expected_version=version())
        access.freeze_document(self.scope, doc.id, session_token=self.token, expected_version=version())
        frozen = await self.save('冻结后的正文')
        self.assert_error(frozen, 403)
        self.assertEqual(frozen.json()['error']['code'], 'frozen')
        reread = await self.client.get('/api/document', params={'object_id': doc.id})
        self.assertEqual(reread.json()['document']['content'], doc.content)
        self.assertEqual(reread.json()['document']['revision_id'], doc.revision_id)
        self.assertTrue(reread.json()['access']['frozen'])

    async def test_document_rejects_authority_fields_and_missing_csrf(self):
        await self.content_fixture()
        self.assert_error(await self.save('未经授权', headers={}), 403)
        body = {'object_id': self.doc.id, 'content': '不应保存', 'expected_revision_id': self.doc.revision_id}
        for extra in ('scope', 'metadata', 'role'):
            response = await self.client.put('/api/document', json={**body, extra: {}},
                headers={'X-C156-CSRF': self.csrf})
            self.assert_error(response, 422)
        for path in ('/api/children?folder_id=x&folder_id=y', '/api/document?object_id=x&scope=y'):
            self.assert_error(await self.client.get(path), 400)
        self.assertEqual(self.services.content.read_document(self.scope, self.doc.id,
            session_token=self.token).content, self.doc.content)

    async def test_operations_history_restore_and_retained_projection(self):
        self.assertEqual(set((await self.client.get('/api/bootstrap')).json()), {'initialized', 'nonce'})
        doc = await self.content_fixture()
        boot = (await self.client.get('/api/bootstrap')).json()
        self.assertEqual(boot['scope'], {'workspace_id': self.scope.workspace_id, 'branch_id': self.scope.branch_id})
        params = {'object_id': doc.id, 'operation_id': 'save-1'}
        self.assertEqual((await self.client.get('/api/document/operation', params=params)).json(), {'operation': None})
        body = {**params, 'content': 'second\n', 'expected_revision_id': doc.revision_id}
        self.assert_error(await self.client.put('/api/document', json=body), 403)
        headers = {'X-C156-CSRF': self.csrf}
        saved = await self.client.put('/api/document', json=body, headers=headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        result = saved.json()
        self.assertEqual(set(result), {'operation', 'current_revision_id'})
        self.assertEqual(set(result['operation']), {'operation_id', 'operation_type', 'result_revision_id', 'changed', 'created_at'})
        self.assertTrue(result['operation']['changed'])
        self.assertEqual((await self.client.put('/api/document', json=body, headers=headers)).json(), result)
        self.assertEqual((await self.client.get('/api/document/operation', params=params)).json(), result)
        first = (await self.client.get('/api/document/history', params={'object_id': doc.id, 'limit': 1})).json()
        self.assertEqual(set(first), {'revisions', 'head_revision_id', 'next_cursor'})
        self.assertEqual(len(first['revisions']), 1)
        self.assertEqual(set(first['revisions'][0]), {'revision_id', 'parent_revision_id', 'actor_id', 'actor_display_name', 'source_kind', 'restored_from_revision_id', 'created_at'})
        last = (await self.client.get('/api/document/history', params={'object_id': doc.id, 'cursor': first['next_cursor']})).json()
        self.assertEqual(last['revisions'][0]['revision_id'], doc.revision_id)
        old = (await self.client.get('/api/document/revision', params={'object_id': doc.id, 'revision_id': doc.revision_id})).json()
        self.assertEqual(set(old), set(first['revisions'][0]) | {'content'})
        self.assertEqual(old['content'], doc.content)
        diff = (await self.client.get('/api/document/diff', params={'object_id': doc.id,
            'from_revision_id': doc.revision_id, 'to_revision_id': result['current_revision_id']})).json()
        self.assertEqual(set(diff), {'from_revision_id', 'to_revision_id', 'diff'})
        self.assertIn('+second', diff['diff'])
        restore_body = {'object_id': doc.id, 'operation_id': 'restore-1',
            'expected_revision_id': result['current_revision_id'], 'source_revision_id': doc.revision_id}
        self.assert_error(await self.client.post('/api/document/restore', json=restore_body), 403)
        restored = await self.client.post('/api/document/restore', json=restore_body, headers=headers)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['operation']['operation_type'], 'restore')
        confirmed = (await self.client.get('/api/document/operation', params=params)).json()
        self.assertEqual(confirmed['operation'], result['operation'])
        self.assertEqual(confirmed['current_revision_id'], restored.json()['current_revision_id'])
        current = self.services.content.read_document(self.scope, doc.id, session_token=self.token)
        self.services.content.delete_node(self.scope, doc.id, expected_version=current.version, session_token=self.token)
        retained = (await self.client.get('/api/documents/deleted', params={'limit': 1})).json()
        self.assertEqual(retained, {'documents': [{'object_id': doc.id, 'name': doc.name, 'path': doc.path}], 'next_cursor': None})
        self.assertEqual((await self.client.get('/api/document/operation', params=params)).json(), confirmed)
        self.assert_error(await self.client.post('/api/document/restore', json={**restore_body,
            'operation_id': 'new'}, headers=headers), 404)
        self.assert_error(await self.client.get('/api/document', params={'object_id': doc.id}), 404)
