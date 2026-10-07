"""Management adapter contracts against the real identity and content services."""
import httpx
from tests.test_server_api import ServerFixture


class ManagementAPITest(ServerFixture):
    async def setup_admin(self):
        result = await self.login()
        self.csrf = result.json()['csrf']
        self.root = (await self.client.get('/api/bootstrap')).json()['root']['id']

    async def write(self, method, path, body):
        return await self.client.request(method, path, json=body, headers={'X-C156-CSRF': self.csrf})

    async def create_member(self, name='member'):
        result = await self.write('POST', '/api/admin/users', {'login_name': name, 'display_name': '成员'})
        self.assertEqual(result.status_code, 201, result.text)
        grant = result.json()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://127.0.0.1:8001') as guest:
            nonce = (await guest.get('/api/bootstrap')).json()['nonce']
            activated = await guest.post('/api/auth/activate', json={'token': grant['token'], 'password': self.password}, headers={'X-C156-Nonce': nonce})
            self.assertEqual(activated.status_code, 200, activated.text)
            return activated.json()['user']

    async def test_account_lifecycle_and_current_identity(self):
        await self.setup_admin()
        user = await self.create_member()
        result = await self.client.get('/api/admin/users')
        self.assertIn(user['id'], [u['id'] for u in result.json()['users']])
        member = self.app.state.services.identity.login('member', self.password, source='local')
        self.client.cookies.clear()
        self.client.cookies.set('c156_session', member.session_token, domain='127.0.0.1', path='/')
        self.csrf = member.csrf_token
        self.assert_error(await self.client.get('/api/admin/users'), 403)
        self.assert_error(await self.client.get('/api/workspace/members'), 403)
        forged = await self.write('PUT', '/api/account/profile', {'display_name': '伪造', 'expected_version': user['version'], 'user_id': result.json()['users'][0]['id']})
        self.assert_error(forged, 422)
        profile = await self.write('PUT', '/api/account/profile', {'display_name': '新名字', 'expected_version': user['version']})
        self.assertEqual(profile.json()['user']['id'], user['id'])
        self.assertEqual(profile.json()['user']['display_name'], '新名字')
        changed = await self.write('PUT', '/api/account/password', {'old_password': self.password, 'new_password': 'changed-password-12345'})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertIn('Max-Age=0', changed.headers['set-cookie'])
        self.assert_error(await self.client.get('/api/session'), 401)
        nonce = (await self.client.get('/api/bootstrap')).json()['nonce']
        wrong = await self.client.post('/api/auth/login', json={'login_name': 'member', 'password': self.password}, headers={'X-C156-Nonce': nonce})
        self.assert_error(wrong, 401)
        good = await self.client.post('/api/auth/login', json={'login_name': 'member', 'password': 'changed-password-12345'}, headers={'X-C156-Nonce': nonce})
        self.assertEqual(good.status_code, 200, good.text)

    async def test_membership_and_site_admin_are_separate(self):
        await self.setup_admin()
        user = await self.create_member()
        version = (await self.client.get('/api/workspace/members')).json()['workspace']['version']
        added = await self.write('POST', '/api/workspace/members', {'login_name': 'member', 'role': 'editor', 'expected_version': version})
        self.assertEqual(added.status_code, 200, added.text)
        self.assertIn('editor', [m['role'] for m in added.json()['workspace']['members']])
        self.assert_error(await self.write('PUT', '/api/workspace/members', {'user_id': user['id'], 'role': 'reader', 'expected_version': version}), 409)
        version = added.json()['workspace']['version']
        removed = await self.write('DELETE', '/api/workspace/members', {'user_id': user['id'], 'expected_version': version})
        self.assertEqual(removed.status_code, 200, removed.text)
        promoted = await self.write('PUT', '/api/admin/users/site-admin', {'user_id': user['id'], 'enabled': True, 'expected_version': user['version']})
        self.assertEqual(promoted.status_code, 200, promoted.text)
        grant = self.app.state.services.identity.login('member', self.password, source='local')
        self.client.cookies.clear()
        self.client.cookies.set('c156_session', grant.session_token, domain='127.0.0.1', path='/')
        self.assertEqual((await self.client.get('/api/admin/users')).status_code, 200)
        self.assert_error(await self.client.get('/api/workspace/members'), 403)
        self.csrf = grant.csrf_token
        denied = await self.write('POST', '/api/workspace/invitations', {'login_name': 'noauthority', 'display_name': '无工作区授权', 'role': 'reader', 'expected_version': removed.json()['workspace']['version']})
        self.assert_error(denied, 403)
        self.assertNotIn('noauthority', [item['login_name'] for item in (await self.client.get('/api/admin/users')).json()['users']])

    async def test_nodes_versions_and_delete_snapshot(self):
        await self.setup_admin()
        folder = await self.write('POST', '/api/folder', {'parent_id': self.root, 'name': '文件夹'})
        self.assertEqual(folder.status_code, 201, folder.text)
        node = folder.json()['node']
        result = await self.write('POST', '/api/document', {'parent_id': node['id'], 'name': '文档', 'content': '# 原文', 'visibility': 'private'})
        self.assertEqual(result.status_code, 201, result.text)
        doc = result.json()['document']
        self.assertEqual(result.json()['access']['visibility'], 'private')
        rename = await self.write('PUT', '/api/node/rename', {'object_id': doc['id'], 'name': '新名称', 'expected_version': doc['version']})
        self.assertEqual(rename.status_code, 200, rename.text)
        reread = (await self.client.get('/api/document', params={'object_id': doc['id']})).json()['document']
        self.assertEqual((reread['content'], reread['revision_id']), (doc['content'], doc['revision_id']))
        self.assert_error(await self.write('PUT', '/api/node/rename', {'object_id': doc['id'], 'name': '过期修改', 'expected_version': doc['version']}), 409)
        plan = (await self.client.get('/api/folder/delete-plan', params={'folder_id': node['id']})).json()
        self.assertIn(doc['id'], [item['node']['id'] for item in plan['items']])
        extra = await self.write('POST', '/api/document', {'parent_id': node['id'], 'name': '后添加'})
        self.assertEqual(extra.status_code, 201, extra.text)
        deletion = {'object_id': node['id'], 'expected_version': plan['version'], 'recursive': True, 'expected_subtree_token': plan['subtree_token']}
        self.assert_error(await self.write('DELETE', '/api/node', deletion), 409)
        self.assertEqual((await self.client.get('/api/document', params={'object_id': doc['id']})).status_code, 200)
        latest = (await self.client.get('/api/folder/delete-plan', params={'folder_id': node['id']})).json()
        deletion.update(expected_version=latest['version'], expected_subtree_token=latest['subtree_token'])
        self.assertEqual((await self.write('DELETE', '/api/node', deletion)).json(), {'ok': True})
        self.assert_error(await self.client.get('/api/document', params={'object_id': doc['id']}), 404)

    async def test_management_requires_proofs_and_strict_fields(self):
        await self.setup_admin()
        self.assert_error(await self.client.post('/api/folder', json={'parent_id': self.root, 'name': '无证明'}), 403)
        self.assert_error(await self.write('POST', '/api/admin/users', {'login_name': 'forged', 'display_name': '伪造', 'site_admin': True}), 422)
        self.assert_error(await self.write('PUT', '/api/node/rename', {'object_id': self.root, 'name': '根', 'expected_version': True}), 422)
        self.assert_error(await self.client.get('/api/admin/users?user_id=forged'), 400)

    async def test_default_workspace_settings_and_ownership(self):
        await self.setup_admin()
        user = await self.create_member()
        version = (await self.client.get('/api/workspace/members')).json()['workspace']['version']
        added = await self.write('POST', '/api/workspace/members', {'login_name': 'member', 'role': 'editor', 'expected_version': version})
        self.assertEqual(added.status_code, 200, added.text)
        version = added.json()['workspace']['version']
        changed = await self.write('PUT', '/api/workspace/read-scope', {'read_scope': 'authenticated', 'expected_version': version})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()['workspace']['read_scope'], 'authenticated')
        self.assert_error(await self.write('PUT', '/api/workspace/read-scope', {'read_scope': 'everyone', 'expected_version': version}), 409)
        version = changed.json()['workspace']['version']
        transferred = await self.write('POST', '/api/workspace/ownership', {'target_user_id': user['id'], 'expected_version': version})
        self.assertEqual(transferred.status_code, 200, transferred.text)
        members = {item['user']['login_name']: item['role'] for item in transferred.json()['workspace']['members']}
        self.assertEqual(members, {'admin': 'admin', 'member': 'owner'})
        self.assert_error(await self.write('POST', '/api/workspace/ownership', {'target_user_id': user['id'], 'expected_version': transferred.json()['workspace']['version']}), 403)

    async def test_invitation_preconfigures_role_and_activation_keeps_it(self):
        await self.setup_admin()
        version = (await self.client.get('/api/workspace/members')).json()['workspace']['version']
        invite = await self.write('POST', '/api/workspace/invitations', {'login_name': 'writer', 'display_name': '新编辑', 'role': 'editor', 'expected_version': version})
        self.assertEqual(invite.status_code, 201, invite.text)
        grant = invite.json()
        pending = next(item for item in grant['workspace']['members'] if item['user']['login_name'] == 'writer')
        self.assertEqual((pending['user']['status'], pending['role']), ('invited', 'editor'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://127.0.0.1:8001') as guest:
            nonce = (await guest.get('/api/bootstrap')).json()['nonce']
            login_body = {'login_name': 'writer', 'password': 'abcdefgh'}
            self.assert_error(await guest.post('/api/auth/login', json=login_body, headers={'X-C156-Nonce': nonce}), 401)
            activated = await guest.post('/api/auth/activate', json={'token': grant['token'], 'password': 'abcdefgh'}, headers={'X-C156-Nonce': nonce})
            self.assertEqual(activated.status_code, 200, activated.text)
            login = await guest.post('/api/auth/login', json=login_body, headers={'X-C156-Nonce': nonce})
            self.assertEqual(login.status_code, 200, login.text)
            state = (await guest.get('/api/bootstrap')).json()
            self.assertEqual(state['workspace_role'], 'editor')
            created = await guest.post('/api/document', json={'parent_id': state['root']['id'], 'name': '激活后创建'}, headers={'X-C156-CSRF': login.json()['csrf']})
            self.assertEqual(created.status_code, 201, created.text)
        denied = await self.write('POST', '/api/workspace/invitations', {'login_name': 'notcreated', 'display_name': '不可创建', 'role': 'owner', 'expected_version': grant['workspace']['version']})
        self.assert_error(denied, 400)
        stale = await self.write('POST', '/api/workspace/invitations', {'login_name': 'stalewriter', 'display_name': '过期邀请', 'role': 'reader', 'expected_version': version})
        self.assert_error(stale, 409)
        names = [item['login_name'] for item in (await self.client.get('/api/admin/users')).json()['users']]
        self.assertNotIn('notcreated', names)
        self.assertNotIn('stalewriter', names)
