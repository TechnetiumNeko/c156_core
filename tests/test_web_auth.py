"""Authentication transport and complete management routing on real SQLite/HTTP."""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from unittest.mock import patch

from src.services.bootstrap import bootstrap_admin
from src.services.content import ContentService
from src.services.identity import IdentityService
from src.storage import Database
from src.storage.management import initialize_database
from src.web.app import create_server
from src.web.auth import session_cookie
from tests.helpers import TempPathTestCase
from tests import test_web

PASSWORD = 'correct horse battery staple'

class WebAuthTests(TempPathTestCase):
    request = test_web.WebTests.request

    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        initialize_database(self.path)
        self.database = Database(self.path)
        self.owner = bootstrap_admin(self.database, 'owner', 'Owner', PASSWORD)
        self.server = create_server(self.path, 0)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.nonce = self.request('GET', '/api/bootstrap')[1]['nonce']
        self.login()
        self.root = self.request('GET', '/api/bootstrap')[1]['root']['id']

    def login(self, name='owner'):
        status, data, headers = self.request('POST', '/api/auth/login', {'login_name': name, 'password': PASSWORD})
        self.assertEqual(status, 200, data)
        self.csrf = data['csrf']
        return data, headers

    def write(self, method, route, data):
        status, result, _ = self.request(method, route, data)
        self.assertIn(status, (200, 201), result)
        return result

    def version(self):
        return self.request('GET', '/api/workspace/members')[1]['workspace']['version']

    def test_cookie_csrf_validation_expiry_and_relogin(self):
        data, headers = self.login()
        self.assertEqual(set(data), {'user', 'csrf', 'expires_at'})
        cookie = headers['Set-Cookie']
        for value in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age='):
            self.assertIn(value, cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertNotIn('Secure', cookie)
        token = self.cookie.split('=', 1)[1]
        self.assertNotIn(token, json.dumps(data))
        current = self.request('GET', '/api/session')[1]
        self.assertEqual(current, data)
        self.assertEqual(self.request('GET', '/api/session', headers={'Origin': 'http://evil.example'})[0], 403)
        payload = {'display_name': 'Changed', 'expected_version': data['user']['version']}
        for headers in ({'X-C156-CSRF': ''}, {'X-C156-CSRF': self.nonce}, {'Origin': 'null'}, {'Host': '127.0.0.1:1'}):
            self.assertEqual(self.request('PUT', '/api/account/profile', payload, headers)[0], 403)
        for extra in ({'actor': 'owner'}, {'session_token': token}, {'expected_version': True}):
            self.assertEqual(self.request('PUT', '/api/account/profile', {**payload, **extra})[0], 400)
        self.assertEqual(self.request('PUT', '/api/account/profile', raw=b'{"display_name":"x","display_name":"y","expected_version":1}')[0], 400)
        self.assertEqual(self.request('GET', '/api/session?session_token=' + token)[0], 400)
        self.write('PUT', '/api/account/profile', payload)
        # Advance service time; cookie remains opaque and the first response stays 401.
        future = datetime.now(timezone.utc) + timedelta(days=2)
        self.server.api.service = ContentService(self.database, clock=lambda: future)
        status, error, headers = self.request('GET', '/api/bootstrap')
        self.assertEqual((status, error['error']['code']), (401, 'unauthenticated'))
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.cookie = None
        anonymous = self.request('GET', '/api/bootstrap')[1]
        self.assertEqual(set(anonymous), {'initialized', 'nonce'})
        self.server.api.service = ContentService(self.database)
        self.nonce = anonymous['nonce']
        self.login()
        self.assertEqual(self.request('GET', '/api/bootstrap')[0], 200)
        for bad in ('c156_session=bad', 'c156_session=', 'c156_session=a; c156_session=b', 'c156_session="broken'):
            self.assertEqual(self.request('GET', '/api/bootstrap', headers={'Cookie': bad})[0], 401)
        self.cookie = None
        self.login()
        grant = IdentityService(self.database).login('owner', PASSWORD, source='local')
        self.assertIn('Secure', session_cookie(grant, secure=True))
        self.write('POST', '/api/auth/logout', {})
        self.cookie = None
        self.assertEqual(self.request('GET', '/api/session')[0], 401)
        self.cookie = None
        self.nonce = self.request('GET', '/api/bootstrap')[1]['nonce']
        self.login()
        self.write('PUT', '/api/account/password', {'old_password': PASSWORD, 'new_password': PASSWORD + '!'} )
        self.assertEqual(self.request('GET', '/api/session')[0], 401)

    def test_all_management_routes_and_private_creation(self):
        grant = self.write('POST', '/api/admin/users', {'login_name': 'editor', 'display_name': 'Editor'})
        invited = grant['user']
        grant = self.write('POST', '/api/admin/users/activation', {'user_id': invited['id'], 'expected_version': invited['version']})
        active = self.write('POST', '/api/auth/activate', {'token': grant['token'], 'password': PASSWORD})['user']
        self.assertEqual(active['status'], 'active')
        self.assertEqual(len(self.request('GET', '/api/admin/users')[1]['users']), 2)
        workspace = self.write('POST', '/api/workspace/members', {'login_name': 'editor', 'role': 'editor', 'expected_version': self.version()})['workspace']
        self.assertEqual(next(m['role'] for m in workspace['members'] if m['user']['id'] == active['id']), 'editor')
        self.write('PUT', '/api/workspace/members', {'user_id': active['id'], 'role': 'reader', 'expected_version': self.version()})
        self.write('DELETE', '/api/workspace/members', {'user_id': active['id'], 'expected_version': self.version()})
        self.write('POST', '/api/workspace/members', {'login_name': 'editor', 'role': 'editor', 'expected_version': self.version()})
        self.write('PUT', '/api/workspace/read-scope', {'read_scope': 'everyone', 'expected_version': self.version()})
        created = self.write('POST', '/api/document', {'parent_id': self.root, 'name': 'Private', 'content': 'secret text', 'visibility': 'private'})
        private = created['document']
        self.assertEqual(created['access']['version'], self.version())
        self.assertEqual(created['access']['visibility'], 'private')
        self.assertIn('edit', created['access']['actions'])
        saved = self.write('PUT', '/api/document', {'object_id': private['id'], 'content': 'secret text', 'expected_revision_id': private['revision_id']})
        self.assertEqual(saved['access'], created['access'])
        view = self.request('GET', '/api/document?' + urlencode({'object_id': private['id']}))[1]
        self.assertEqual(view['access']['visibility'], 'private')
        self.assertEqual(view['access']['version'], self.version())
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute('SELECT creator_id FROM content_ownership WHERE object_id=?', (private['id'],)).fetchone()[0], self.owner.id)
            self.assertEqual(connection.execute('SELECT owner_id FROM content_privacy WHERE object_id=?', (private['id'],)).fetchone()[0], self.owner.id)
            self.assertEqual(connection.execute('SELECT count(*) FROM document_revisions WHERE object_id=?', (private['id'],)).fetchone()[0], 1)
        access = self.request('GET', '/api/access?' + urlencode({'object_id': private['id']}))[1]['access']
        self.assertIn('decisions', access)
        rule = {'object_id': private['id'], 'subject_type': 'role', 'subject_key': 'reader', 'action': 'edit'}
        self.write('PUT', '/api/access/rule', {**rule, 'effect': 'deny', 'expected_version': self.version()})
        self.write('DELETE', '/api/access/rule', {**rule, 'expected_version': self.version()})
        self.write('PUT', '/api/access/visibility', {'object_id': private['id'], 'visibility': 'inherit', 'expected_version': self.version()})
        frozen = self.write('POST', '/api/document/freeze', {'object_id': private['id'], 'expected_version': self.version()})['access']
        self.assertTrue(frozen['frozen'])
        owner_cookie, owner_csrf = self.cookie, self.csrf
        self.cookie = None
        self.login('editor')
        status, error, _ = self.request('PUT', '/api/document', {'object_id': private['id'], 'content': 'blocked', 'expected_revision_id': private['revision_id']})
        self.assertEqual((status, error['error']['code']), (403, 'frozen'))
        self.cookie, self.csrf = owner_cookie, owner_csrf
        self.write('DELETE', '/api/document/freeze', {'object_id': private['id'], 'expected_version': self.version()})
        user = self.write('PUT', '/api/admin/users/site-admin', {'user_id': active['id'], 'enabled': True, 'expected_version': active['version']})['user']
        reset = self.write('POST', '/api/admin/users/reset', {'user_id': user['id'], 'expected_version': user['version']})
        user = self.write('POST', '/api/auth/reset', {'token': reset['token'], 'password': PASSWORD})['user']
        user = self.write('POST', '/api/admin/users/disable', {'user_id': user['id'], 'expected_version': user['version']})['user']
        enabled = self.write('POST', '/api/admin/users/enable', {'user_id': user['id'], 'expected_version': user['version']})
        self.assertEqual(enabled['purpose'], 'reset')
        self.write('POST', '/api/auth/reset', {'token': enabled['token'], 'password': PASSWORD})
        self.write('POST', '/api/workspace/ownership', {'target_user_id': active['id'], 'expected_version': self.version()})
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['workspace_role'], 'admin')

    def test_no_root_management_source_throttle_safe_logs_and_methods(self):
        grant = self.write('POST', '/api/admin/users', {'login_name': 'outsider', 'display_name': 'Outside'})
        outsider = self.write('POST', '/api/auth/activate', {'token': grant['token'], 'password': PASSWORD})['user']
        self.write('PUT', '/api/admin/users/site-admin', {'user_id': outsider['id'], 'enabled': True, 'expected_version': outsider['version']})
        self.cookie = None
        self.login('outsider')
        view = self.request('GET', '/api/bootstrap')[1]
        self.assertIsNone(view['root'])
        self.assertIsNone(view['root_access'])
        self.assertEqual(self.request('GET', '/api/session')[0], 200)
        self.assertEqual(self.request('GET', '/api/admin/users')[0], 200)
        self.write('PUT', '/api/account/profile', {'display_name': 'New name', 'expected_version': view['user']['version']})
        self.write('POST', '/api/auth/logout', {})
        self.cookie = None
        self.login()
        for method, route in [('DELETE', '/api/workspace/members'), ('PATCH', '/api/document')]:
            self.assertEqual(self.request(method, route, {}, {'X-C156-CSRF': ''})[0], 403)
            self.assertEqual(self.request(method, route, {}, {'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('PATCH', '/api/document', {})[0], 405)
        self.assertEqual(self.request('POST', '/api/unknown', {})[0], 404)
        self.cookie = None
        secret = 'credential-query-should-not-log'
        with self.assertLogs('src.web.http', level=logging.INFO) as logs:
            self.assertEqual(self.request('GET', '/api/unknown?token=' + secret)[0], 400)
        self.assertNotIn(secret, '\n'.join(logs.output))
        with patch.object(self.server.api.service, 'bootstrap', side_effect=RuntimeError(secret)), self.assertLogs('src.web.http', level=logging.ERROR) as logs:
            status, error, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 500)
        self.assertNotIn(secret, json.dumps(error) + '\n'.join(logs.output))
        for _ in range(11):
            status, result, headers = self.request('POST', '/api/auth/login', {'login_name': 'missing', 'password': PASSWORD}, {'X-Forwarded-For': 'evil.example'})
        self.assertEqual((status, result['error']['code']), (429, 'rate_limited'))
        self.assertGreater(int(headers['Retry-After']), 0)
        with self.database.transaction() as connection:
            sources = [r[0] for r in connection.execute("SELECT bucket_key FROM auth_throttles WHERE bucket_type='source'")]
        self.assertIn('127.0.0.1', sources)
        self.assertNotIn('evil.example', sources)

    def test_fresh_library_minimal_bootstrap(self):
        fresh = self.temp_path('fresh.sqlite')
        initialize_database(fresh)
        server = create_server(fresh, 0)
        old = self.server
        self.server = server
        self.cookie = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, view, _ = self.request('GET', '/api/bootstrap')
            self.assertEqual(status, 200)
            self.assertEqual(set(view), {'initialized', 'nonce'})
            self.assertFalse(view['initialized'])
            with Database(fresh).transaction() as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM users').fetchone()[0], 0)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            self.server = old
