"""Interactive CLI application and extensible command registry."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable

from .commands import Command, CommandContext, built_in_commands
from .completion import Completer
from .paths import VirtualFileSystem


class CLI:
    def __init__(
        self,
        root: Path,
        output: Callable[..., None] = print,
        prompt: Callable[[str], str] = input,
    ):
        self.fs = VirtualFileSystem(root)
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
            prompt = f"c156:{self.fs.display()}$ "
            try:
                line = input(prompt)
            except EOFError:
                self.output("")
                return 0
            except KeyboardInterrupt:
                self.output("")
                continue
            if not self.execute(line):
                return 0


def default_data_root() -> Path:
    # app.py is <repo>/src/cli/app.py
    return Path(__file__).resolve().parents[2] / "data" / "main"


def main() -> int:
    return CLI(default_data_root()).run()


if __name__ == "__main__":
    raise SystemExit(main())
