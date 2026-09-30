"""Built-in CLI commands. Add new commands by registering another Command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.errors import NotDirectory, NotFound, NotDocument, PathOutsideRoot
from ..core.paths import parse_path, validate_name
from ..editor import Editor
from .paths import VirtualFileSystem


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    usage: str
    handler: Callable[["CommandContext", list[str]], None]
    path_argument: str | None = None
    directories_only: bool = False


@dataclass(frozen=True)
class PendingEdit:
    """Unsaved text and its original optimistic concurrency base, in memory only."""

    object_id: str
    title: str
    base_revision_id: str
    content: str


@dataclass
class CommandContext:
    fs: VirtualFileSystem
    output: Callable[..., None]
    commands: tuple[Command, ...] = ()
    system_command_handler: Callable[[str], None] | None = None
    active_editor: object | None = None
    prompt: Callable[[str], str] = input
    editor_factory: Callable = Editor
    pending_edit: PendingEdit | None = None


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
    parent, name = creation_target(context, args[0])
    context.fs.service.create_folder(context.fs.scope, parent.id, name)


def command_edit(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "edit <path>")
    path = args[0]
    if parse_path(path).trailing_slash:
        raise NotDocument("文档路径不能以 / 结尾")
    try:
        target = context.fs.resolve(path)
    except (NotFound, PathOutsideRoot):
        pending = context.pending_edit
        parsed = parse_path(path)
        # Restore an unavailable object by its last displayed path, without
        # creating a replacement or changing the original save revision.
        if pending is not None and all(part not in (".", "..") for part in parsed.parts):
            prefix = "/" if parsed.absolute else context.fs.display().rstrip("/") + "/"
            if prefix + "/".join(parsed.parts) == pending.title:
                edit_pending(context)
                return
        # Resolve/validate the parent before asking; this also rejects doc/../leaf.
        parent, name = creation_target(context, path)
        if context.pending_edit is not None and not handle_pending(context):
            return
        try:
            answer = context.prompt("文档不存在，是否创建？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            context.output("创建已取消。" + ("未保存正文已保留在当前会话。"
                                      if context.pending_edit is not None else ""))
            return
        if answer not in ("y", "yes"):
            return
        target = context.fs.service.create_document(context.fs.scope, parent.id, name)
    pending = context.pending_edit
    if pending is not None and pending.object_id == target.id:
        edit_pending(context)
        return
    if pending is not None and not handle_pending(context):
        return
    document = context.fs.service.read_document(context.fs.scope, target.id)
    initial = PendingEdit(document.id, document.path, document.revision_id, document.content)
    edit_pending(context, initial)


def creation_target(context: CommandContext, path: str):
    """Split a virtual path lexically; resolution still validates each parent step."""
    parsed = parse_path(path)
    name = parsed.parts[-1] if parsed.parts else ""
    validate_name(name)
    parent_path = "/".join(parsed.parts[:-1])
    if parsed.absolute:
        parent_path = "/" + parent_path
    parent = context.fs.resolve(parent_path or ".")
    if parent.kind != "folder":
        raise NotDirectory("父路径不是目录")
    return parent, name


def pending_choice(context: CommandContext, *, allow_discard=False) -> str:
    choices = "continue 继续编辑 / view 查看正文 / later 稍后处理"
    if allow_discard:
        choices += " / discard 明确放弃"
    try:
        return context.prompt(f"存在未保存正文：{choices} [later] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        context.output("正文仍未保存，已保留在当前会话。")
        return "later"


def view_pending(context: CommandContext) -> None:
    context.output(context.pending_edit.content)


def handle_pending(context: CommandContext) -> bool:
    """Guard switching documents and exiting; only explicit discard drops text."""
    while context.pending_edit is not None:
        choice = pending_choice(context, allow_discard=True)
        if choice in ("discard", "放弃"):
            context.pending_edit = None
        elif choice in ("view", "查看"):
            view_pending(context)
        elif choice in ("continue", "继续"):
            edit_pending(context)
        else:
            return False
    return True


def edit_pending(context: CommandContext, initial: PendingEdit | None = None) -> None:
    """Run editors outside transactions; failures retain text and the exact base."""
    pending = initial if initial is not None else context.pending_edit
    while pending is not None:
        editor = context.editor_factory(pending.object_id, pending.title, pending.content,
                                        system_command_handler=context.system_command_handler)
        context.active_editor = editor
        try:
            result = editor.run()
        except KeyboardInterrupt:
            # Real editor can return its in-memory buffer after terminal cleanup.
            # Substitutes need only run(); an existing failed buffer remains intact.
            if isinstance(editor, Editor):
                interrupted = editor.result()
                if interrupted.changed:
                    context.pending_edit = PendingEdit(pending.object_id, pending.title,
                                                       pending.base_revision_id, interrupted.content)
            context.output("编辑已取消。" + ("未保存正文已保留在当前会话。"
                                      if context.pending_edit is not None else ""))
            return
        finally:
            context.active_editor = None
        if not result.save_requested:
            # A normal :q on restored text is not permission to drop the earlier
            # failed result; only explicit :q! (or the CLI discard choice) is.
            if result.discard_requested:
                context.pending_edit = None
            return
        pending = PendingEdit(pending.object_id, pending.title,
                              pending.base_revision_id, result.content)
        context.pending_edit = pending
        try:
            context.fs.service.save_document(context.fs.scope, pending.object_id,
                                             pending.content,
                                             expected_revision_id=pending.base_revision_id)
        except KeyboardInterrupt:
            context.output("保存已取消；未保存正文已保留在当前会话。")
            return
        except Exception as exc:
            # Unknown storage errors must preserve the result just like conflicts.
            context.output(f"保存失败：{exc}；正文仍未保存，已保留。")
        else:
            context.pending_edit = None
            return
        while True:
            choice = pending_choice(context)
            if choice in ("view", "查看"):
                view_pending(context)
            elif choice in ("continue", "继续"):
                break
            else:
                return


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

