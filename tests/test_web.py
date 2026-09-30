"""A few real HTTP checks against disposable content databases."""
import http.client
from contextlib import closing
import json
import subprocess
import sqlite3
import sys
import threading
from urllib.parse import urlencode

from src.services.content import ContentService
from src.storage import Database
from src.storage.management import initialize_database
from src.web.app import create_server
from tests.helpers import PROJECT_ROOT, TempPathTestCase


class WebTests(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        initialize_database(self.path)
        self.service = ContentService(Database(self.path))
        self.scope = self.service.default_scope()
        self.server = create_server(self.path, port=0)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        status, data, _ = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 200)
        self.root = data['root']
        self.nonce = data['nonce']

    def request(self, method, path, data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        values = {'Content-Type': 'application/json', 'X-C156-Nonce': getattr(self, 'nonce', '')}
        values.update(headers or {})
        body = json.dumps(data).encode() if data is not None else raw
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        payload = response.read()
        result = json.loads(payload) if response.getheader('Content-Type', '').startswith('application/json') else payload
        answer = response.status, result, dict(response.getheaders())
        connection.close()
        return answer

    def test_roundtrip_and_independent_service_visibility_and_conflict(self):
        status, data, _ = self.request('POST', '/api/folder', {'parent_id': self.root['id'], 'name': '作品'})
        self.assertEqual(status, 201)
        folder = data['node']
        status, data, _ = self.request('POST', '/api/document', {'parent_id': folder['id'], 'name': '文章', 'content': '初稿\r\n'})
        self.assertEqual(status, 201)
        document = data['document']
        self.assertEqual(self.service.read_document(self.scope, document['id']).content, '初稿\r\n')
        self.service.set_metadata(self.scope, document['id'], {'nested': {'tags': ['a']}}, expected_version=document['version'])
        status, data, _ = self.request('GET', '/api/document?' + urlencode({'object_id': document['id']}))
        self.assertEqual(data['document']['metadata'], {'nested': {'tags': ['a']}})
        save = {'object_id': document['id'], 'content': '修改', 'expected_revision_id': document['revision_id']}
        self.assertEqual(self.request('PUT', '/api/document', save)[0], 200)
        status, error, _ = self.request('PUT', '/api/document', save)
        self.assertEqual((status, error['error']['code']), (409, 'conflict'))
        latest = self.service.read_document(self.scope, document['id'])
        self.service.save_document(self.scope, latest.id, '独立入口', expected_revision_id=latest.revision_id)
        self.assertEqual(self.request('GET', '/api/document?' + urlencode({'object_id': latest.id}))[1]['document']['content'], '独立入口')
        children = self.request('GET', '/api/children?' + urlencode({'folder_id': folder['id']}))[1]['nodes']
        self.assertEqual([node['id'] for node in children], [latest.id])

    def test_security_validation_and_domain_errors(self):
        payload = {'parent_id': self.root['id'], 'name': 'one'}
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'X-C156-Nonce': ''}):
            self.assertEqual(self.request('POST', '/api/folder', payload, headers)[0], 403)
        host = 'localhost:4321'
        self.assertEqual(self.request('POST', '/api/folder', payload, {'Host': host, 'Origin': 'http://' + host})[0], 201)
        self.assertEqual(self.request('POST', '/api/folder', payload)[0], 409)
        self.assertEqual(self.request('POST', '/api/folder', {**payload, 'name': '../bad'})[0], 422)
        for data in ({**payload, 'workspace_id': 'other'}, {'parent_id': 1, 'name': 'x'}, []):
            self.assertEqual(self.request('POST', '/api/folder', data)[0], 400)
        self.assertEqual(self.request('POST', '/api/folder', raw=b'{broken')[0], 400)
        self.assertEqual(self.request('POST', '/api/folder', payload, {'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.request('POST', '/api/folder', raw=b'', headers={'Content-Length': str(2 * 1024 * 1024 + 1)})[0], 413)
        self.assertEqual(self.request('GET', '/api/bootstrap?root_id=other')[0], 400)
        outside = self.root['parent_id']
        self.assertEqual(self.request('GET', '/api/children?' + urlencode({'folder_id': outside}))[0], 403)
        self.assertEqual(self.request('GET', '/api/document?object_id=missing')[0], 404)
        self.assertEqual(self.request('GET', '/api/document?' + urlencode({'object_id': self.root['id']}))[0], 422)
        for path in ('/data/c156.sqlite', '/../run_cli.py', '/%2e%2e/run_cli.py', '/vendor/LICENSE', '/api/unknown'):
            status, data, headers = self.request('GET', path)
            self.assertEqual(status, 404)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('Content-Security-Policy', headers)
            self.assertIn('error', data)

    def test_startup_and_help_never_create_database(self):
        missing = self.temp_path('missing.sqlite')
        with self.assertRaises(Exception):
            create_server(missing, port=0)
        self.assertFalse(missing.exists())
        for name, statement in (('non-wal.sqlite', 'PRAGMA journal_mode = DELETE'),
                                ('unknown.sqlite', 'PRAGMA user_version = 99')):
            invalid = self.temp_path(name)
            initialize_database(invalid)
            with closing(sqlite3.connect(invalid)) as connection:
                connection.execute(statement)
            before = invalid.read_bytes()
            with self.assertRaises(Exception):
                create_server(invalid, port=0)
            self.assertEqual(invalid.read_bytes(), before)
        for entry in (['run_web.py'], ['-m', 'src.web']):
            result = subprocess.run([sys.executable, *entry, '--database', str(missing), '--help'], cwd=PROJECT_ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--port', result.stdout)
            self.assertFalse(missing.exists())
