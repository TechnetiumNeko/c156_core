"""Readline command and virtual path completion with shell quoting."""

import shlex


def _tokens(value: str):
    """Lex incomplete shell input, retaining the current token's raw start.

    Unlike shlex.split this accepts the open quote present while typing.
    The lexer does no expansion or content lookup.
    """
    tokens = []
    start = None
    decoded = ''
    quote = None
    escaped = False
    for index, char in enumerate(value):
        if start is None and not char.isspace():
            start = index
        if escaped:
            # Match the CLI shlex parser inside double quotes.
            if quote == '"' and char not in '\\"':
                decoded += '\\'
            decoded += char
            escaped = False
        elif char == '\\' and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
            else:
                decoded += char
        elif char in "'\"":
            quote = char
        elif char.isspace():
            if start is not None:
                tokens.append((decoded, start))
                decoded = ''
                start = None
        else:
            decoded += char
    return tokens, decoded, start, quote, escaped


def _replacement(candidate: str, prefix: str) -> str | None:
    """Encode the suffix after readline's preserved raw token prefix."""
    _, decoded, _, quote, escaped = _tokens(prefix)
    if not candidate.startswith(decoded):
        return None
    remaining = candidate[len(decoded):]
    first = ''
    if escaped:
        if not remaining:
            return None
        first, remaining = remaining[0], remaining[1:]
    if quote == "'":
        return first + remaining.replace("'", "'\\''") + "'"
    if quote == '"':
        return first + '"' + (shlex.quote(remaining) if remaining else '')
    return first + (shlex.quote(remaining) if remaining else '')


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
        begidx = readline.get_begidx()
        endidx = readline.get_endidx()
        completed, value, start, _, _ = _tokens(line[:endidx])
        if not completed:
            return [name for name in self.cli.command_names if name.startswith(value)]
        command_name = completed[0][0]
        if command_name == 'help' and len(completed) == 1:
            return [name for name in self.cli.command_names if name.startswith(value)]
        command = self.cli.commands_by_name.get(command_name)
        if command is None or command.path_argument is None or value.startswith('-'):
            return []
        if len(completed) != 1:
            return []
        prefix = line[start:begidx] if start is not None else ''
        candidates = self.cli.fs.completion_candidates(value, directories_only=command.directories_only)
        return [replacement for candidate in candidates
                if (replacement := _replacement(candidate, prefix)) is not None]
