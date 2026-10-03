#!/usr/bin/env python3
"""Prepare a separate demo workspace, start it and open the browser."""
import argparse
import errno
from getpass import getpass
import shlex
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from src.core.errors import ContentError, InvalidArgument
from src.services.content import ContentService
from src.services.identity import IdentityService
from src.services.bootstrap import bootstrap_admin
from src.services.unit_of_work import ApplicationUnitOfWork
from src.storage import Database
from src.storage.errors import StorageError
from src.storage.management import initialize_database
from src.web.app import create_server, instance_marker

ROOT = Path(__file__).resolve().parent
WELCOME = """# 开始写作

这里是 C156 的小型文档工作台。你可以直接改写这篇文档，右边会跟着显示预览。

## 试一试

- 在左侧展开「作品」，阅读第一章。
- 点击「新建文档」，写一段自己的文字。
- 用顶部的按钮切换源码、预览和并排视图。
- 点击「保存」，或按 Ctrl / Cmd + S。

> 编辑不会自动保存。保存后，下次打开仍能看到你的修改。

| 内容 | 放在哪里 |
| --- | --- |
| 章节草稿 | 作品 |
| 人物与世界观 | 设定 |

这个工作区供试手使用，网页和终端可以读写同一份内容。
"""


def prepare_demo(path, *, prompt=input, password_prompt=getpass):
    """Explicitly bootstrap and seed only a newly initialized demo library."""
    path = Path(path)
    is_new = not path.exists()
    if is_new:
        initialize_database(path)
    database = Database(path)
    service = ContentService(database)
    with ApplicationUnitOfWork(database).transaction() as work:
        scope = work.default_scope()
        initialized = bool(work.identity.list_users())
    if not is_new:
        if not initialized:
            command = shlex.quote(str(path))
            raise InvalidArgument('已有库尚未引导，请先运行：python -m src.identity bootstrap-admin '
                                  f'--database {command} --login-name <name> --display-name <name>')
        return scope
    login_name = prompt('首管理员登录名 [demo_owner]：') or 'demo_owner'
    display_name = prompt('显示名 [演示管理员]：') or '演示管理员'
    password = password_prompt('密码（15–128 个字符）：')
    if password != password_prompt('再次输入密码：'):
        raise InvalidArgument('passwords do not match')
    user = bootstrap_admin(database, login_name, display_name, password)
    identity = IdentityService(database)
    grant = identity.login(user.login_name, password, source='local')
    del password
    try:
        scope = service.default_scope(session_token=grant.session_token)
        service.create_document(scope, scope.root_id, '开始阅读.md', content=WELCOME, session_token=grant.session_token)
        work = service.create_folder(scope, scope.root_id, '作品', session_token=grant.session_token)
        service.create_document(scope, work.id, '第一章.md', content='# 第一章\n\n风从旧港口吹来。\n\n> 所有故事都有一个出发的地方。\n\n这里留给你的故事。\n', session_token=grant.session_token)
        settings = service.create_folder(scope, scope.root_id, '设定', session_token=grant.session_token)
        service.create_document(scope, settings.id, '人物.md', content='# 人物档案\n\n## 名字\n\n林舟\n\n## 背景\n\n在港口长大，准备第一次远行。\n', session_token=grant.session_token)
    finally:
        identity.logout(session_token=grant.session_token)
    return scope


def is_running(port, scope):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/bootstrap', timeout=2) as response:
            return response.headers.get('X-C156-Instance') == instance_marker(scope)
    except (OSError, ValueError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='一键启动 C156 示例工作台，保留每次保存的修改')
    parser.add_argument('--database', type=Path, default=ROOT / '.c156' / 'demo.sqlite')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--no-browser', action='store_true', help='只启动服务，不自动打开浏览器')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port 必须介于 0 和 65535')
    try:
        scope = prepare_demo(args.database)
        try:
            server = create_server(args.database, args.port)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            if is_running(args.port, scope):
                url = f'http://127.0.0.1:{args.port}/'
                print(f'工作台已在运行：{url}', flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            server = create_server(args.database, port=0)
    except (ContentError, StorageError, OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(f'演示启动失败：{error}；旧版本库请使用新的 --database 路径，不覆盖原库。', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'C156 工作台：{url}\n保存的修改会保留。关闭此终端或按 Ctrl+C 停止服务。', flush=True)
    if not args.no_browser:
        opener = threading.Thread(target=webbrowser.open, args=(url,), daemon=True)
        opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
