"""Construct and run the local Web entry without initializing storage."""
import argparse
import secrets
import shlex
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from ..core.errors import ContentError
from ..services.content import ContentService
from ..services.identity import IdentityService
from ..services.accounts import AccountService
from ..services.access import AccessService
from ..services.unit_of_work import ApplicationUnitOfWork
from ..storage import Database
from ..storage.errors import StorageError
from .api import API
from .http import Handler


def create_server(database_path, port=8000, *, cookie_secure=False):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError('port must be an integer from 0 to 65535')
    if type(cookie_secure) is not bool:
        raise ValueError("cookie_secure must be boolean")
    database = Database(Path(database_path))
    service = ContentService(database)
    # Startup configuration only: validate fixed topology without reading nodes or bodies.
    with ApplicationUnitOfWork(database).transaction() as work:
        scope = work.default_scope()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.cookie_secure = cookie_secure
    server.api = API(service, scope, secrets.token_urlsafe(32), identity_service=IdentityService(database),
                     accounts=AccountService(database), access=AccessService(database))
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description='C156 本地文档工作台')
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[2] / 'data' / 'c156.sqlite')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    try:
        server = create_server(args.database, args.port)
    except (ContentError, StorageError, OSError, ValueError) as error:
        print(f'无法启动本地工作台：{error}', file=sys.stderr)
        database = shlex.quote(str(args.database))
        print(f'新建或完成运行配置：python -m src.storage init --database {database}', file=sys.stderr)
        print(f'导入旧数据：python -m src.storage migrate-legacy --source data --database {database}', file=sys.stderr)
        return 1
    print(f'C156 文档工作台：http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
