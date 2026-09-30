"""Interactive CLI application and extensible command registry."""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Callable

from ..core.errors import ContentError
from ..core.models import ContentScope
from ..services.content import ContentService
from ..storage import Database
from ..storage.errors import StorageError

from .commands import Command, CommandContext, built_in_commands
from .completion import Completer
from .paths import VirtualFileSystem


class CLI:
    def __init__(
        self,
        service: ContentService,
        scope: ContentScope,
        output: Callable[..., None] = print,
        prompt: Callable[[str], str] = input,
    ):
        self.fs = VirtualFileSystem(service, scope)
        self.output = output
        self.exit_requested = False
        self.commands_by_name: dict[str, Command] = {}
        self.register_commands(built_in_commands())
        self.context = CommandContext(
            self.fs,
            output,
            system_command_handler=self._execute_editor_command,
            prompt=prompt,
        )
        self.context.commands = tuple(self.commands_by_name.values())

    def _execute_editor_command(self, line: str) -> None:
        """Dispatch editor ':' commands through the normal CLI command registry."""
        try:
            words = shlex.split(line)
        except ValueError as exc:
            raise ValueError(f"解析命令失败: {exc}") from exc
        if words and words[0] == "edit":
            raise ValueError("编辑器中不能再次执行 edit")
        if not self.execute(line):
            self.exit_requested = True
            if self.context.active_editor is not None:
                self.context.active_editor.closed = True

    @property
    def command_names(self) -> tuple[str, ...]:
        return tuple(self.commands_by_name)

    def register(self, command: Command) -> None:
        if command.name in self.commands_by_name:
            raise ValueError(f"命令已注册: {command.name}")
        self.commands_by_name[command.name] = command
        self.context.commands = tuple(self.commands_by_name.values())

    def register_commands(self, commands: list[Command]) -> None:
        for command in commands:
            if command.name in self.commands_by_name:
                raise ValueError(f"命令已注册: {command.name}")
            self.commands_by_name[command.name] = command
        if hasattr(self, "context"):
            self.context.commands = tuple(self.commands_by_name.values())

    def _ensure_cwd(self) -> None:
        if self.fs.ensure_cwd():
            self.output("当前目录已删除或移出访问范围，已回到 main 根目录。")

    def execute(self, line: str) -> bool:
        try:
            self._ensure_cwd()
        except ContentError as exc:
            self.output(content_error_message(exc))
            return True
        try:
            words = shlex.split(line)
        except ValueError as exc:
            self.output(f"解析命令失败: {exc}")
            return True
        if not words:
            return True
        name, args = words[0], words[1:]
        if name == "exit":
            if args:
                self.output(f"用法: {name}")
                return True
            return False
        command = self.commands_by_name.get(name)
        if command is None:
            self.output(f"未知命令: {name}。输入 help 查看可用命令。")
            return True
        if self.context.active_editor is not None and name == "edit":
            self.output("编辑器中不能再次执行 edit")
            return True
        try:
            command.handler(self.context, args)
        except ContentError as exc:
            self.output(content_error_message(exc))
        except (OSError, ValueError, RuntimeError) as exc:
            self.output(f"{exc}")
        return not self.exit_requested

    def run(self) -> int:
        try:
            import readline

            completer = Completer(self)
            readline.set_completer(completer)
            readline.set_completer_delims(" \t\n")
            readline.parse_and_bind("tab: complete")
        except ImportError:
            pass

        self.output("C156 CLI 已启动。输入 help 查看命令，输入 exit 退出。")
        while True:
            self._ensure_cwd()
            prompt = f"c156:{self.fs.display()}$ "
            try:
                line = self.context.prompt(prompt)
            except EOFError:
                self.output("")
                return 0
            except KeyboardInterrupt:
                self.output("")
                continue
            if not self.execute(line):
                return 0


def content_error_message(error: ContentError) -> str:
    labels = {
        "not_found": "路径或对象不存在",
        "not_directory": "不是目录",
        "not_document": "不是文档",
        "path_outside_root": "路径超出虚拟根目录",
        "invalid_argument": "参数无效",
        "invalid_name": "名称无效",
        "already_exists": "名称已存在",
        "conflict": "内容已被修改，请重新读取",
        "storage_busy": "数据库繁忙，请稍后重试",
        "unsupported_schema": "数据库协议或运行配置无效",
    }
    return f"{labels.get(error.code, '内容操作失败')}: {error}"


def default_database_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "c156.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C156 内容命令行")
    parser.add_argument("--database", type=Path, default=default_database_path())
    args = parser.parse_args(argv)
    try:
        service = ContentService(Database(args.database))
        scope = service.default_scope()
    except (ContentError, StorageError, OSError) as exc:
        print(f"无法启动：数据库缺失、无效或尚未配置 WAL。{exc}", file=sys.stderr)
        database = shlex.quote(str(args.database))
        print(f"新建或完成运行配置：python -m src.storage init --database {database}", file=sys.stderr)
        print(f"导入旧数据或继续迁移配置：python -m src.storage migrate-legacy --source data --database {database}", file=sys.stderr)
        return 1
    return CLI(service, scope).run()


if __name__ == "__main__":
    raise SystemExit(main())
