"""In-memory text buffer and cursor operations for the terminal editor."""


class TextBuffer:
    def __init__(self, content: str):
        self.final_newline = content.endswith("\n")
        self.lines = content.splitlines()
        if not self.lines:
            self.lines = [""]
        self.row = 0
        self.column = 0

    @property
    def text(self) -> str:
        value = "\n".join(self.lines)
        return value + ("\n" if self.final_newline else "")

    def move_horizontal(self, amount: int) -> None:
        line = self.lines[self.row]
        self.column = max(0, min(self.column + amount, max(0, len(line) - 1)))

    def move_vertical(self, amount: int) -> None:
        self.row = max(0, min(self.row + amount, len(self.lines) - 1))
        self.column = min(self.column, max(0, len(self.lines[self.row]) - 1))

    def line_start(self) -> None:
        self.column = 0

    def line_end(self) -> None:
        self.column = max(0, len(self.lines[self.row]) - 1)

    def insert_text(self, value: str) -> None:
        if not value:
            return
        line = self.lines[self.row]
        self.lines[self.row] = line[: self.column] + value + line[self.column :]
        self.column += len(value)

    def insert_newline(self) -> None:
        line = self.lines[self.row]
        self.lines[self.row] = line[: self.column]
        self.lines.insert(self.row + 1, line[self.column :])
        self.row += 1
        self.column = 0

    def backspace(self) -> None:
        if self.column > 0:
            line = self.lines[self.row]
            self.lines[self.row] = line[: self.column - 1] + line[self.column :]
            self.column -= 1
        elif self.row > 0:
            current = self.lines.pop(self.row)
            self.row -= 1
            self.column = len(self.lines[self.row])
            self.lines[self.row] += current

    def delete_forward(self) -> None:
        line = self.lines[self.row]
        if self.column < len(line):
            self.lines[self.row] = line[: self.column] + line[self.column + 1 :]
        elif self.row < len(self.lines) - 1:
            self.lines[self.row] += self.lines.pop(self.row + 1)

    def open_line_below(self) -> None:
        self.lines.insert(self.row + 1, "")
        self.row += 1
        self.column = 0

    def delete_lines(self, count: int = 1) -> None:
        stop = min(len(self.lines), self.row + max(1, count))
        del self.lines[self.row : stop]
        if not self.lines:
            self.lines = [""]
            self.final_newline = False
        self.row = min(self.row, len(self.lines) - 1)
        self.column = min(self.column, max(0, len(self.lines[self.row]) - 1))
