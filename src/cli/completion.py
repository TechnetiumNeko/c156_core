"""Readline command and virtual path completion."""

import shlex


class Completer:
    def __init__(self, cli):
        self.cli = cli
        self.matches: list[str] = []

    def __call__(self, text: str, state: int):
        if state == 0:
            self.matches = self._matches(text)
        return self.matches[state] if state < len(self.matches) else None

    def _matches(self, text: str) -> list[str]:
        try:
            import readline
        except ImportError:
            return []
        line = readline.get_line_buffer()
        before = line[:readline.get_begidx()]
        try:
            tokens = shlex.split(before)
        except ValueError:
            tokens = before.split()

        if not tokens:
            return [name for name in self.cli.command_names if name.startswith(text)]

        command = self.cli.commands_by_name.get(tokens[0])
        arg_index = len(tokens) - 1
        if tokens[0] == "help" and arg_index == 0:
            return [name for name in self.cli.command_names if name.startswith(text)]
        if command is None or command.path_argument is None or text.startswith("-"):
            return []

        # A command may declare path completion for only its first path argument.
        if command.name == "tree" and any(token in ("-d", "--max-depth") for token in tokens[1:]):
            return []
        if arg_index > 0:
            return []
        return self.cli.fs.completion_candidates(text, directories_only=command.directories_only)
