"""Built-in CLI commands. Add new commands by registering another Command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.errors import NotDirectory
from .paths import VirtualFileSystem


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    usage: str
    handler: Callable[["CommandContext", list[str]], None]
    path_argument: str | None = None
    directories_only: bool = False


@dataclass
class CommandContext:
    fs: VirtualFileSystem
    output: Callable[..., None]
    commands: tuple[Command, ...] = ()
    system_command_handler: Callable[[str], None] | None = None
    active_editor: object | None = None
    prompt: Callable[[str], str] = input


def _require_args(args: list[str], count: int, usage: str) -> None:
    if len(args) != count:
        raise ValueError(f"用法: {usage}")


def command_help(context: CommandContext, args: list[str]) -> None:
    """The registry is attached by CLI so extensions are included in help."""
    commands = context.commands
    if len(args) > 1:
        raise ValueError("用法: help <command>")
    if args:
        command = next((item for item in commands if item.name == args[0]), None)
        if command is None:
            raise ValueError(f"未知命令: {args[0]}")
        context.output(f"{command.name} - {command.summary}\n用法: {command.usage}")
        return
    context.output("可用命令:")
    for command in commands:
        context.output(f"  {command.name:<8} {command.summary}  ({command.usage})")
    context.output("输入 help <command> 查看命令详情。")


def command_pwd(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 0, "pwd")
    context.output(context.fs.display())


def command_cd(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "cd <path>")
    target = context.fs.resolve(args[0])
    if target.kind != "folder":
        raise NotDirectory("不是目录", details={"path": args[0]})
    context.fs.cwd_id = target.id


def command_ls(context: CommandContext, args: list[str]) -> None:
    if len(args) > 1:
        raise ValueError("用法: ls [path]")
    target = context.fs.resolve(args[0] if args else ".")
    if target.kind != "folder":
        context.output(target.name)
        return
    entries = context.fs.service.list_children(context.fs.scope, target.id)
    for entry in sorted(entries, key=lambda node: (node.kind != "folder", node.name.lower())):
        context.output(entry.name + ("/" if entry.kind == "folder" else ""))


def command_tree(context: CommandContext, args: list[str]) -> None:
    path = None
    max_depth = None
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-d", "--max-depth"):
            index += 1
            if index >= len(args):
                raise ValueError("用法: tree [path] [-d <max_depth>]")
            try:
                max_depth = int(args[index])
            except ValueError as exc:
                raise ValueError("max_depth 必须是非负整数") from exc
            if max_depth < 0:
                raise ValueError("max_depth 必须是非负整数")
        elif token.startswith("-"):
            raise ValueError(f"未知选项: {token}\n用法: tree [path] [-d <max_depth>]")
        elif path is None:
            path = token
        else:
            raise ValueError("用法: tree [path] [-d <max_depth>]")
        index += 1

    path = path if path is not None else "."
    max_depth = max_depth if max_depth is not None else 2

    target = context.fs.resolve(path)
    items = context.fs.service.list_tree(context.fs.scope, target.id, max_depth=max_depth)
    last_child = {item.node.parent_id: item.node.id for item in items if item.depth > 0}
    for item in items:
        node = item.node
        if item.depth == 0:
            context.output(node.name if node.path != "/" else "/")
        else:
            connector = "└── " if last_child[node.parent_id] == node.id else "├── "
            context.output("  " * item.depth + connector + node.name + ("/" if node.kind == "folder" else ""))


def command_cat(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "cat <path>")
    target = context.fs.resolve(args[0])
    content = context.fs.service.read_document(context.fs.scope, target.id).content
    context.output(content, end="")
    if not content.endswith("\n"):
        context.output("")


def command_mkdir(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "mkdir <path>")
    raise ValueError("mkdir 的服务接入尚未完成。")


def command_edit(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "edit <path>")
    raise ValueError("edit 的服务接入尚未完成。")


def built_in_commands() -> list[Command]:
    return [
        Command("help", "显示命令帮助", "help [command]", command_help),
        Command("ls", "列出目录内容", "ls [path]", command_ls, path_argument="optional"),
        Command("pwd", "显示当前虚拟路径", "pwd", command_pwd),
        Command("cd", "切换当前目录", "cd <path>", command_cd, path_argument="required", directories_only=True),
        Command("tree", "以树形显示目录", "tree [path] [-d <max_depth>]", command_tree, path_argument="optional"),
        Command("cat", "显示文本文件内容", "cat <path>", command_cat, path_argument="required"),
        Command("mkdir", "创建目录", "mkdir <path>", command_mkdir, path_argument="required", directories_only=True),
        Command("edit", "编辑 document 文档", "edit <path>", command_edit, path_argument="required"),
        Command("exit", "退出 CLI", "exit", lambda context, args: None),
    ]

