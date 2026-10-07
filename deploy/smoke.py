"""Exercise final images and persisted data. Requires Docker; no cloud credentials."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError


def main():
    compose = Path(__file__).with_name('compose.yaml').resolve()
    sha = os.environ['BUILD_SHA']
    with tempfile.TemporaryDirectory(prefix='c156-smoke-') as directory:
        root = Path(directory)
        for name in ('data', 'assets', 'backups'):
            (root / name).mkdir(mode=0o700)
        rootless = 'rootless' in subprocess.check_output(['docker', 'info', '--format', '{{json .SecurityOptions}}'], text=True)
        if rootless:
            # Disposable fixture only: rootless UID mapping differs from host UID.
            for name in ('data', 'assets', 'backups'):
                (root / name).chmod(0o777)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'DEPLOY_ROOT': directory, 'SITE_DOMAIN': 'smoke.example',
               'APP_PORT': str(port), 'APP_UID': str(os.getuid()), 'APP_GID': str(os.getgid())}
        command = ['docker', 'compose', '-p', 'c156-smoke-' + secrets.token_hex(4), '-f', str(compose)]
        def run(*args, input=None):
            return subprocess.run(command + list(args), env=env, input=input, text=True,
                                  check=True, stdout=subprocess.PIPE).stdout
        opener = build_opener(ProxyHandler({}))
        def request(path, body=None, method=None, headers=None):
            req = Request(f'http://127.0.0.1:{port}' + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method, headers={'Host': 'smoke.example', 'Origin': 'https://smoke.example',
                'X-Forwarded-For': '203.0.113.7', 'X-Forwarded-Proto': 'https',
                'Content-Type': 'application/json', **(headers or {})})
            with opener.open(req, timeout=5) as response:
                raw = response.read()
                return raw, response.headers
        password = secrets.token_urlsafe(24)
        seed = '''import json,sys
from pathlib import Path
from src.storage.management import initialize_database
from src.storage import Database
from src.services.bootstrap import bootstrap_admin
from src.services.identity import IdentityService
from src.services.content import ContentService
from src.services.unit_of_work import ApplicationUnitOfWork
p=json.load(sys.stdin)['password']; path=Path('/data/c156.sqlite'); initialize_database(path)
db=Database(path); bootstrap_admin(db,'admin','Smoke',p)
gr=IdentityService(db).login('admin',p,source='smoke')
with ApplicationUnitOfWork(db).transaction() as w: scope=w.default_scope()
doc=ContentService(db).create_document(scope,scope.root_id,'Smoke',content='before',session_token=gr.session_token)
print(doc.id)
'''
        # Password travels through stdin, never command arguments or output.
        try:
            object_id = run('run', '--rm', '-T', '--no-deps', 'backend', 'python', '-c', seed,
                            input=json.dumps({'password': password})).strip().splitlines()[-1]
            run('up', '-d', '--wait', '--wait-timeout', '90')
            assert json.loads(request('/build-info.json')[0]) == {'build_sha': sha}
            assert json.loads(request('/api/healthz')[0]) == {'status': 'ok', 'build_sha': sha}
            assert b'<html' in request('/')[0].lower()
            nonce = json.loads(request('/api/bootstrap')[0])['nonce']
            raw, headers = request('/api/auth/login', {'login_name': 'admin', 'password': password},
                                   headers={'X-C156-Nonce': nonce})
            assert 'Secure' in headers['Set-Cookie']
            # HTTP smoke manually sends Secure cookie; browser TLS is checked at deployment.
            cookie = headers['Set-Cookie'].split(';')[0]
            auth = {'Cookie': cookie, 'X-C156-CSRF': json.loads(raw)['csrf']}
            document = json.loads(request('/api/document?object_id=' + object_id, headers=auth)[0])['document']
            saved = json.loads(request('/api/document', {'object_id': object_id, 'content': 'after\n中文\n',
                'expected_revision_id': document['revision_id']}, 'PUT', auth)[0])
            run('exec', '-T', 'backend', 'python', '-m', 'src.storage', 'backup',
                '--database', '/data/c156.sqlite', '--output', '/backups/smoke.sqlite')
            run('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '90')
            assert json.loads(request('/api/document?object_id=' + object_id, headers=auth)[0]) == saved
            request('/api/auth/logout', {}, headers=auth)
            try:
                request('/api/session', headers=auth)
                raise AssertionError('logged-out session accepted')
            except HTTPError as error:
                assert error.code == 401
            print('Final image smoke passed: health, SHA, login, save, recreate, persistence, logout.')
        finally:
            run('down', '--remove-orphans')

if __name__ == '__main__':
    main()
