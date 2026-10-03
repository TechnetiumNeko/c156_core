"""Interactive CLI application and extensible command registry."""

from __future__ import annotations

import argparse
from getpass import getpass
import shlex
import sys
from pathlib import Path
from typing import Callable

from ..core.errors import ContentError
from ..core.models import ContentScope
from ..editor import Editor
from ..services.content import ContentService
from ..services.identity import IdentityService
from ..services.unit_of_work import ApplicationUnitOfWork
from ..storage import Database
from ..storage.errors import StorageError

from .commands import Command, CommandContext, built_in_commands, handle_pending
from .completion import Completer
from .paths import VirtualFileSystem


class CLI:
    def __init__(
        self,
        service: ContentService,
        scope: ContentScope,
        output: Callable[..., None] = print,
        prompt: Callable[[str], str] = input,
        *,
        editor_factory: Callable = Editor,
        session_token: str | None,
        identity: IdentityService | None = None,
        password_prompt: Callable[[str], str] = getpass,
    ):
        self.fs = VirtualFileSystem(service, scope, session_token=session_token)
        self.identity = identity or IdentityService(service._uow._database)
        self.password_prompt = password_prompt
        user_id = self.identity.current_session(session_token=session_token).user.id if session_token else None
        self.output = output
        self.exit_requested = False
        self.commands_by_name: dict[str, Command] = {}
        self.register_commands(built_in_commands())
        self.context = CommandContext(
            self.fs,
            output,
            system_command_handler=self._execute_editor_command,
            prompt=prompt,
            editor_factory=editor_factory,
            user_id=user_id,
            validate_session=lambda: self.identity.current_session(session_token=self.fs.session_token),
        )
        self.register(Command("login", "登录账号", "login", self._login))
        self.register(Command("logout", "退出账号", "logout", self._logout))
        self.context.commands = tuple(self.commands_by_name.values())

    def _login(self, context, args):
        if args:
            raise ValueError("用法: login")
        if context.active_editor is not None:
            raise ValueError("请先关闭编辑器再切换账号")
        name = context.prompt("用户名: ")
        password = self.password_prompt("密码: ")
        grant = self.identity.login(name, password, source="local")
        if context.pending_edit is not None and context.pending_edit.user_id != grant.user.id:
            try:
                discard = context.prompt("另一用户的旧草稿必须明确放弃，输入 discard 确认: ")
            except (EOFError, KeyboardInterrupt):
                self.identity.logout(session_token=grant.session_token)
                raise
            if discard.strip().lower() != "discard":
                self.identity.logout(session_token=grant.session_token)
                context.output("登录切换已取消，旧草稿已保留。")
                return
            context.pending_edit = None
        old_token = self.fs.session_token
        self.fs.session_token = grant.session_token
        context.user_id = grant.user.id
        if old_token:
            try:
                self.identity.logout(session_token=old_token)
            except ContentError:
                pass
        self._ensure_cwd()
        context.output(f"已登录: {grant.user.login_name}")

    def _logout(self, context, args):
        if args:
            raise ValueError("用法: logout")
        if context.active_editor is not None:
            raise ValueError("请先关闭编辑器再退出账号")
        try:
            if self.fs.session_token:
                self.identity.logout(session_token=self.fs.session_token)
        except ContentError:
            pass
        finally:
            self.fs.session_token = None
            context.user_id = None
            self.fs.available = False
            self.fs.cached_path = "未登录"
        context.output("已退出账号；未保存草稿仍保留在内存。")

    def _execute_editor_command(self, line: str) -> None:
        """Dispatch editor ':' commands through the normal CLI command registry."""
        try:
            words = shlex.split(line)
        except ValueError as exc:
            raise ValueError(f"解析命令失败: {exc}") from exc
        if words and words[0] == "edit":
            raise ValueError("编辑器中不能再次执行 edit")
        if words and words[0] == "exit" and self.context.active_editor is not None:
            raise ValueError("请先使用 :wq 保存或 :q! 明确放弃，再退出 CLI")
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
            self.output("当前目录已删除或失去访问权限，已回到可读祖先目录。")

    def execute(self, line: str) -> bool:
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
            if self.context.active_editor is not None:
                self.output("请先使用 :wq 保存或 :q! 明确放弃，再退出 CLI")
                return True
            return not handle_pending(self.context)
        command = self.commands_by_name.get(name)
        if command is None:
            self.output(f"未知命令: {name}。输入 help 查看可用命令。")
            return True
        if self.context.active_editor is not None and name == "edit":
            self.output("编辑器中不能再次执行 edit")
            return True
        try:
            if name not in ("login", "logout", "help"):
                if self.context.user_id is None:
                    self.output("请先 login 登录账号。")
                    return True
                self._ensure_cwd()
                if not self.fs.available:
                    self.output("无可用内容；可使用 login、logout、help 或 exit。")
                    return True
            command.handler(self.context, args)
        except (KeyboardInterrupt, EOFError):
            self.output("操作已取消。" + ("未保存正文已保留在当前会话。"
                                      if self.context.pending_edit is not None else ""))
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
        try:
            self._ensure_cwd()
        except ContentError:
            self.fs.available = False
            self.fs.cached_path = "请登录"
        while True:
            prompt = f"c156:{self.fs.cached_path}$ "
            try:
                line = self.context.prompt(prompt)
            except EOFError:
                self.output("")
                if self.context.pending_edit is not None:
                    self.output("正文仍未保存；当前会话结束后内存正文将丢失。")
                return 0
            except KeyboardInterrupt:
                self.output("")
                continue
            if not self.execute(line):
                return 0


def content_error_message(error: ContentError) -> str:
    labels = {
        "unauthenticated": "会话无效，请 login 重新登录",
        "forbidden": "没有操作权限",
        "rate_limited": "认证尝试过于频繁",
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


def main(argv: list[str] | None = None, *, prompt=input, password_prompt=getpass) -> int:
    parser = argparse.ArgumentParser(description="C156 内容命令行")
    parser.add_argument("--database", type=Path, default=default_database_path())
    args = parser.parse_args(argv)
    try:
        database = Database(args.database)
        service = ContentService(database)
        with ApplicationUnitOfWork(database).transaction() as work:
            scope = work.default_scope()
            initialized = bool(work.identity.list_users())
    except (ContentError, StorageError, OSError) as exc:
        print(f"无法启动：数据库缺失、无效或尚未配置 WAL。{exc}", file=sys.stderr)
        database = shlex.quote(str(args.database))
        print(f"新建或完成运行配置：python -m src.storage init --database {database}", file=sys.stderr)
        print(f"导入旧数据或继续迁移配置：python -m src.storage migrate-legacy --source data --database {database}", file=sys.stderr)
        return 1
    if not initialized:
        print("尚无账号，请显式执行本机引导：python -m src.identity bootstrap-admin --database " + shlex.quote(str(args.database)) + " --login-name <name> --display-name <name>", file=sys.stderr)
        return 1
    cli = CLI(service, scope, session_token=None, prompt=prompt, password_prompt=password_prompt)
    cli.execute("login")
    if cli.fs.session_token is None:
        return 1
    return cli.run()


if __name__ == "__main__":
    raise SystemExit(main())
