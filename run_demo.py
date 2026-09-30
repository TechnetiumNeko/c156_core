#!/usr/bin/env python3
"""Prepare a separate demo workspace, start it and open the browser."""
import argparse
import errno
import json
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from src.core.errors import ContentError
from src.services.content import ContentService
from src.storage import Database
from src.storage.errors import StorageError
from src.storage.management import initialize_database
from src.web.app import create_server

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


def prepare_demo(path):
    """Seed a new demo only; reopening never changes existing writing."""
    if not path.exists():
        initialize_database(path)
        service = ContentService(Database(path))
        scope = service.default_scope()
        service.create_document(scope, scope.root_id, '开始阅读.md', content=WELCOME)
        work = service.create_folder(scope, scope.root_id, '作品')
        service.create_document(scope, work.id, '第一章.md', content='# 第一章\n\n风从旧港口吹来。\n\n> 所有故事都有一个出发的地方。\n\n这里留给你的故事。\n')
        settings = service.create_folder(scope, scope.root_id, '设定')
        service.create_document(scope, settings.id, '人物.md', content='# 人物档案\n\n## 名字\n\n林舟\n\n## 背景\n\n在港口长大，准备第一次远行。\n')
    service = ContentService(Database(path))
    return service.default_scope()


def is_running(port, root_id):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/bootstrap', timeout=2) as response:
            data = json.load(response)
            return isinstance(data, dict) and isinstance(data.get('root'), dict) and data['root'].get('id') == root_id
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
            if is_running(args.port, scope.root_id):
                url = f'http://127.0.0.1:{args.port}/'
                print(f'工作台已在运行：{url}', flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                return 0
            server = create_server(args.database, port=0)
    except (ContentError, StorageError, OSError, ValueError) as error:
        print(f'演示启动失败：{error}', file=sys.stderr)
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
