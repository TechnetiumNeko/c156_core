"""Built-in CLI commands. Add new commands by registering another Command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

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
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {args[0]}")
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录: {args[0]}")
    context.fs.cwd = target


def command_ls(context: CommandContext, args: list[str]) -> None:
    if len(args) > 1:
        raise ValueError("用法: ls [path]")
    target = context.fs.resolve(args[0] if args else ".")
    if not target.exists():
        raise FileNotFoundError(f"路径不存在: {args[0]}")
    if target.is_file():
        context.output(target.name)
        return
    if not target.is_dir():
        raise ValueError(f"不支持的文件类型: {args[0]}")
    entries = sorted(
        (entry for entry in target.iterdir() if entry.name != ".folder"),
        key=lambda item: (not item.is_dir(), item.name.lower()),
    )
    for entry in entries:
        context.output(entry.name + ("/" if entry.is_dir() else ""))


def _tree_lines(root: Path, max_depth: int, virtual_root: Path) -> list[str]:
    lines = [root.name or "/"]

    def visit(directory: Path, level: int) -> None:
        if level >= max_depth:
            return
        try:
            children = sorted(
                (child for child in directory.iterdir() if child.name != ".folder"),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError as exc:
            lines.append(f"{'  ' * (level + 1)}[无法读取: {exc}]")
            return
        for index, child in enumerate(children):
            last = index == len(children) - 1
            prefix = "  " * (level + 1) + ("└── " if last else "├── ")
            lines.append(prefix + child.name + ("/" if child.is_dir() else ""))
            if child.is_dir():
                try:
                    child.resolve().relative_to(virtual_root)
                except (OSError, ValueError):
                    continue
                visit(child, level + 1)

    visit(root, 0)
    return lines


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
    if not target.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    for line in _tree_lines(target, max_depth, context.fs.root):
        context.output(line)


def command_cat(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "cat <path>")
    target = context.fs.resolve(args[0])
    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {args[0]}")
    if not target.is_file():
        raise IsADirectoryError(f"不是文件: {args[0]}")
    from ..file.document import Document
    from ..file.sql import SqlFile

    try:
        record = SqlFile(target).record()
    except (FileNotFoundError, ValueError):
        record = None

    if record is not None:
        if record.kind != "document":
            raise ValueError(f"cat 只能显示 document 正文，目标类型为 {record.kind}: {args[0]}")
        content = Document(target).content
    else:
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"无法以 UTF-8 文本读取: {args[0]}") from exc
    context.output(content, end="")
    if not content.endswith("\n"):
        context.output("")


def command_mkdir(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "mkdir <path>")
    target = context.fs.resolve(args[0])
    parent_container = target.parent / ".folder"
    parent_folder = None
    if parent_container.is_file():
        from ..file.folder import Folder

        parent_folder = Folder(parent_container)

    target.mkdir()
    if parent_folder is not None:
        try:
            child = Folder.create(
                target / ".folder",
                fullpath=f"{parent_folder.fullpath.rstrip('/')}/{target.name}",
                parent_id=parent_folder.id,
            )
            parent_folder.add_child(child.id)
        except Exception:
            import shutil

            shutil.rmtree(target, ignore_errors=True)
            raise


def command_edit(context: CommandContext, args: list[str]) -> None:
    _require_args(args, 1, "edit <path>")
    target = context.fs.resolve(args[0])
    if target.exists() and not target.is_file():
        raise IsADirectoryError(f"edit 只能打开 document，不能打开目录: {args[0]}")

    from ..editor import Editor
    from ..file.document import Document
    from ..file.folder import Folder

    parent_container = target.parent / ".folder"
    if not parent_container.is_file():
        raise ValueError(f"父目录没有有效的 .folder 容器: {target.parent}")
    parent = Folder(parent_container)
    expected_fullpath = f"{parent.fullpath.rstrip('/')}/{target.name}"
    created = False

    if target.exists():
        document = Document(target)
        if document.fullpath != expected_fullpath or document.parent_id != parent.id:
            raise ValueError(f"文档元数据与所在目录不匹配: {args[0]}")
        if document.id not in parent.children():
            raise ValueError(f"文档未登记在所在目录的 file 表中: {args[0]}")
    else:
        try:
            answer = context.prompt(
                f"文档不存在: {args[0]}。是否创建并编辑？ [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            context.output("已取消，未创建文档。")
            return
        if answer not in ("y", "yes"):
            context.output("已取消，未创建文档。")
            return
        if not target.parent.is_dir():
            raise FileNotFoundError(f"父目录不存在: {target.parent}")
        target.touch(exist_ok=False)
        try:
            document = Document.create(
                target,
                fullpath=expected_fullpath,
                parent_id=parent.id,
            )
            parent.add_child(document.id)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        created = True
        context.output(f"已创建文档: {args[0]}")

    # Future authority checks belong here, before opening the editor.
    editor = Editor(document, system_command_handler=context.system_command_handler)
    context.active_editor = editor
    try:
        result = editor.run()
    except KeyboardInterrupt:
        if created:
            context.output("编辑已取消；新建的空文档仍保留。")
        else:
            context.output("编辑已取消，未保存。")
        return
    finally:
        context.active_editor = None

    if result.save_requested:
        # Persist content and metadata here, outside the editor.
        document.content = result.content
        document.sql.set_metadata("modified_at", datetime.now().astimezone().isoformat())
        context.output("文档已保存。")
    elif created:
        context.output("新建的文档尚未保存内容。")


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

