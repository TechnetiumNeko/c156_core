"""Small modal terminal editor. File and system commands stay outside Editor."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from src.file.document import Document

from .buffer import TextBuffer


SystemCommandHandler = Callable[[str], None]


@dataclass(frozen=True)
class EditorResult:
    """In-memory result; the system decides whether and how to persist it."""

    save_requested: bool
    content: str
    changed: bool


class Editor:
    """Edit one Document object; callers resolve paths and dispatch system commands."""

    def __init__(
        self,
        document: Document,
        system_command_handler: SystemCommandHandler | None = None,
    ):
        if not isinstance(document, Document):
            raise TypeError("Editor 只能打开 Document 对象")
        self.document = document
        self.system_command_handler = system_command_handler
        initial_content = document.content
        self.buffer = TextBuffer(initial_content)
        self.original_content = initial_content
        self.mode = "normal"
        self.command_line: str | None = None
        self.pending = ""
        self.count_text = ""
        self.status = f"{document.fullpath}  |  :wq 保存退出  :q 退出"
        self.top_visual_row = 0
        self.closed = False
        self.save_requested = False
        self._terminal_fd: int | None = None
        self._saved_terminal: list | None = None
        self._help_return_state: tuple[TextBuffer, str, int, str, str, str] | None = None

    def run(self) -> EditorResult:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("Editor 需要交互式终端")

        file_descriptor = sys.stdin.fileno()
        saved_terminal = termios.tcgetattr(file_descriptor)
        self._terminal_fd = file_descriptor
        self._saved_terminal = saved_terminal
        try:
            sys.stdout.write("\x1b[?1049h\x1b[?25l")
            sys.stdout.flush()
            tty.setraw(file_descriptor)
            while not self.closed:
                self._draw()
                key = self._read_key()
                if key == "\x03":
                    raise KeyboardInterrupt
                if self.command_line is not None:
                    self._handle_command_key(key)
                elif self.mode == "insert":
                    self._handle_insert_key(key)
                else:
                    self._handle_normal_key(key)
        finally:
            termios.tcsetattr(file_descriptor, termios.TCSADRAIN, saved_terminal)
            self._terminal_fd = None
            self._saved_terminal = None
            sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        return EditorResult(
            save_requested=self.save_requested,
            content=self.buffer.text,
            changed=self.buffer.text != self.original_content,
        )

    def _read_key(self) -> str:
        file_descriptor = sys.stdin.fileno()
        first = os.read(file_descriptor, 1)
        if not first:
            return ""
        if first != b"\x1b":
            first_value = first[0]
            if first_value < 0x80:
                sequence = first
            elif first_value & 0xE0 == 0xC0:
                length = 2
                sequence = first
            elif first_value & 0xF0 == 0xE0:
                length = 3
                sequence = first
            elif first_value & 0xF8 == 0xF0:
                length = 4
                sequence = first
            else:
                return "�"
            if first_value >= 0x80:
                while len(sequence) < length:
                    sequence += os.read(file_descriptor, 1)
            return sequence.decode("utf-8", errors="replace")

        ready, _, _ = select.select([file_descriptor], [], [], 0.04)
        if not ready:
            return "ESC"

        sequence = bytearray(first)
        while len(sequence) < 16:
            ready, _, _ = select.select([file_descriptor], [], [], 0.04)
            if not ready:
                break
            sequence.extend(os.read(file_descriptor, 1))
            if len(sequence) == 2 and sequence[1] not in (ord("["), ord("O")):
                break
            if len(sequence) >= 3 and sequence[1] == ord("O"):
                break
            if len(sequence) >= 3 and sequence[1] == ord("["):
                # CSI sequences end with a byte in the 0x40–0x7e range.
                if 0x40 <= sequence[-1] <= 0x7E:
                    break

        raw_sequence = bytes(sequence)
        arrows = {
            ord("A"): "UP",
            ord("B"): "DOWN",
            ord("C"): "RIGHT",
            ord("D"): "LEFT",
            ord("H"): "HOME",
            ord("F"): "END",
        }
        if raw_sequence in (b"\x1b[A", b"\x1bOA"):
            return "UP"
        if raw_sequence in (b"\x1b[B", b"\x1bOB"):
            return "DOWN"
        if raw_sequence in (b"\x1b[C", b"\x1bOC"):
            return "RIGHT"
        if raw_sequence in (b"\x1b[D", b"\x1bOD"):
            return "LEFT"
        if raw_sequence in (b"\x1b[H", b"\x1bOH"):
            return "HOME"
        if raw_sequence in (b"\x1b[F", b"\x1bOF"):
            return "END"
        if raw_sequence.startswith(b"\x1b[") and raw_sequence.endswith(b"~"):
            if raw_sequence[2:-1] == b"3":
                return "DELETE"
            if raw_sequence[2:-1] in (b"1", b"7"):
                return "HOME"
            if raw_sequence[2:-1] in (b"4", b"8"):
                return "END"
        if (
            raw_sequence.startswith(b"\x1b[")
            and raw_sequence[-1] in arrows
        ):
            return arrows[raw_sequence[-1]]
        return "ESC"

    @staticmethod
    def _char_width(char: str) -> int:
        if unicodedata.combining(char):
            return 0
        return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1

    @classmethod
    def _display_width(cls, text: str) -> int:
        return sum(cls._char_width(char) for char in text)

    @classmethod
    def _fit_cells(cls, text: str, width: int) -> str:
        result = []
        cells = 0
        for char in text:
            char_cells = cls._char_width(char)
            if cells + char_cells > width:
                break
            result.append(char)
            cells += char_cells
        return "".join(result) + " " * max(0, width - cells)

    def _visual_lines(self, terminal_columns: int) -> tuple[list[tuple[int, int, int, str, bool]], int]:
        number_width = max(4, len(str(len(self.buffer.lines))))
        gutter_width = number_width + 1
        # Leave the last terminal cell available for the insert-mode cursor.
        text_width = max(1, terminal_columns - gutter_width - 1)
        rows: list[tuple[int, int, int, str, bool]] = []

        for line_index, line in enumerate(self.buffer.lines):
            if not line:
                rows.append((line_index, 0, 0, "", True))
                continue
            start = 0
            while start < len(line):
                end = start
                cells = 0
                while end < len(line):
                    width = self._char_width(line[end])
                    if end > start and cells + width > text_width:
                        break
                    cells += width
                    end += 1
                if end == start:
                    end += 1
                rows.append((line_index, start, end, line[start:end], start == 0))
                start = end

        return rows, gutter_width

    def _cursor_visual_position(
        self,
        rows: list[tuple[int, int, int, str, bool]],
        gutter_width: int,
    ) -> tuple[int, int]:
        row_index = 0
        for visual_index, (line_index, start, end, _, _) in enumerate(rows):
            if line_index != self.buffer.row:
                continue
            if start <= self.buffer.column < end:
                row_index = visual_index
                break
            if self.buffer.column == end:
                next_is_same_line = (
                    visual_index + 1 < len(rows)
                    and rows[visual_index + 1][0] == line_index
                )
                if next_is_same_line:
                    continue
                row_index = visual_index
                break
            if self.buffer.column == start and start == end:
                row_index = visual_index
                break
        line = self.buffer.lines[self.buffer.row]
        row_data = rows[row_index]
        cursor_index = min(max(self.buffer.column, row_data[1]), row_data[2])
        column = gutter_width + self._display_width(line[row_data[1]:cursor_index]) + 1
        return row_index, column

    def _draw(self) -> None:
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
        except OSError:
            size = os.terminal_size((80, 24))
        terminal_rows = max(3, size.lines)
        terminal_columns = max(20, size.columns)
        viewport_rows = terminal_rows
        body_rows = max(1, viewport_rows - 2)
        top_padding = 0
        visual_rows, gutter_width = self._visual_lines(terminal_columns)
        cursor_visual_row, cursor_column = self._cursor_visual_position(visual_rows, gutter_width)
        if cursor_visual_row < self.top_visual_row:
            self.top_visual_row = cursor_visual_row
        elif cursor_visual_row >= self.top_visual_row + body_rows:
            self.top_visual_row = cursor_visual_row - body_rows + 1
        self.top_visual_row = min(self.top_visual_row, max(0, len(visual_rows) - body_rows))

        mode_name = "插入" if self.mode == "insert" else "普通"
        if self.command_line is not None:
            mode_name = "命令"
        visible = visual_rows[self.top_visual_row:self.top_visual_row + body_rows]
        header = f" C156 Editor  {self.document.fullpath}  [{mode_name}]"
        output = [
            "\x1b[?25l\x1b[2J",
            f"\x1b[{top_padding + 1};1H",
            self._fit_cells(header, terminal_columns) + "\r\n",
        ]
        number_width = gutter_width - 1
        for row_index in range(body_rows):
            if row_index < len(visible):
                line_index, _, _, text, first_segment = visible[row_index]
                number = f"{line_index + 1:>{number_width}} " if first_segment else " " * gutter_width
                rendered = number + text
                padding = max(0, terminal_columns - gutter_width - self._display_width(text))
                rendered += " " * padding
            else:
                rendered = "~"
                rendered += " " * max(0, terminal_columns - 1)
            output.append(rendered + "\r\n")

        if self.command_line is not None:
            footer = ":" + self.command_line
        else:
            footer = f"{mode_name} | {self.status}"
        output.append(self._fit_cells(footer, terminal_columns))

        if self.command_line is not None:
            cursor_terminal_row = top_padding + viewport_rows
            cursor_terminal_column = min(self._display_width(self.command_line) + 2, terminal_columns)
        else:
            cursor_terminal_row = top_padding + cursor_visual_row - self.top_visual_row + 2
            cursor_terminal_column = min(cursor_column, terminal_columns)
        output.append(f"\x1b[{cursor_terminal_row};{cursor_terminal_column}H\x1b[?25h")
        sys.stdout.write("".join(output))
        sys.stdout.flush()

    def _handle_normal_key(self, key: str) -> None:
        self.status = ""
        if self.pending == "g":
            if key == "g":
                self.buffer.row = 0
                self.buffer.column = 0
            self.pending = ""
            self.count_text = ""
            return
        if self.pending == "d":
            if key == "d":
                if self._help_return_state is not None:
                    self.status = "帮助文档为只读"
                else:
                    self.buffer.delete_lines(self._take_count())
            self.pending = ""
            self.count_text = ""
            return

        if key.isdigit() and len(key) == 1:
            if key != "0" or self.count_text:
                self.count_text += key
                return

        has_count = bool(self.count_text)
        count = self._take_count()
        motions = {
            "h": (-1, 0), "LEFT": (-1, 0),
            "l": (1, 0), "RIGHT": (1, 0),
            "k": (0, -1), "UP": (0, -1),
            "j": (0, 1), "DOWN": (0, 1),
        }
        if key in motions:
            horizontal, vertical = motions[key]
            if horizontal:
                self.buffer.move_horizontal(horizontal * count)
            if vertical:
                self.buffer.move_vertical(vertical * count)
        elif key == "0":
            self.buffer.line_start()
        elif key == "$":
            self.buffer.line_end()
        elif key == "G":
            self.buffer.row = min(len(self.buffer.lines) - 1, count - 1) if has_count else len(self.buffer.lines) - 1
            self.buffer.column = min(self.buffer.column, max(0, len(self.buffer.lines[self.buffer.row]) - 1))
        elif key == "g":
            self.pending = "g"
        elif key == "d":
            if self._help_return_state is not None:
                self.status = "帮助文档为只读"
            else:
                self.pending = "d"
        elif key == "i":
            if self._help_return_state is not None:
                self.status = "帮助文档为只读"
            else:
                self.mode = "insert"
        elif key == "o":
            if self._help_return_state is not None:
                self.status = "帮助文档为只读"
            else:
                self.buffer.open_line_below()
                self.mode = "insert"
        elif key == ":":
            self.command_line = ""
        elif key == "ESC":
            self.pending = ""
            self.count_text = ""
        else:
            self.status = f"未知按键: {key}"

    def _take_count(self) -> int:
        count = int(self.count_text) if self.count_text else 1
        self.count_text = ""
        return max(1, count)

    def _handle_insert_key(self, key: str) -> None:
        self.status = ""
        if key in ("ESC",):
            self.mode = "normal"
            self.buffer.column = min(self.buffer.column, max(0, len(self.buffer.lines[self.buffer.row]) - 1))
        elif key in ("\r", "\n"):
            self.buffer.insert_newline()
        elif key in ("\x7f", "\x08"):
            self.buffer.backspace()
        elif key == "DELETE":
            self.buffer.delete_forward()
        elif key in ("LEFT", "RIGHT", "UP", "DOWN", "HOME", "END"):
            self._move_insert_cursor(key)
        elif len(key) == 1 and key.isprintable():
            self.buffer.insert_text(key)

    def _move_insert_cursor(self, key: str) -> None:
        if key in ("LEFT", "HOME"):
            if key == "HOME":
                self.buffer.column = 0
            elif self.buffer.column:
                self.buffer.column -= 1
            elif self.buffer.row:
                self.buffer.row -= 1
                self.buffer.column = len(self.buffer.lines[self.buffer.row])
        elif key in ("RIGHT", "END"):
            if key == "END":
                self.buffer.column = len(self.buffer.lines[self.buffer.row])
            elif self.buffer.column < len(self.buffer.lines[self.buffer.row]):
                self.buffer.column += 1
            elif self.buffer.row < len(self.buffer.lines) - 1:
                self.buffer.row += 1
                self.buffer.column = 0
        elif key == "UP":
            self.buffer.row = max(0, self.buffer.row - 1)
            self.buffer.column = min(self.buffer.column, len(self.buffer.lines[self.buffer.row]))
        elif key == "DOWN":
            self.buffer.row = min(len(self.buffer.lines) - 1, self.buffer.row + 1)
            self.buffer.column = min(self.buffer.column, len(self.buffer.lines[self.buffer.row]))

    def _handle_command_key(self, key: str) -> None:
        if key in ("ESC",):
            self.command_line = None
        elif key in ("\r", "\n"):
            command = self.command_line or ""
            self.command_line = None
            self._execute_command(command.strip())
        elif key in ("\x7f", "\x08"):
            self.command_line = (self.command_line or "")[:-1]
        elif len(key) == 1 and key.isprintable():
            self.command_line = (self.command_line or "") + key

    def _execute_command(self, command: str) -> None:
        if command == "h":
            if self._help_return_state is None:
                self._open_help()
            else:
                self.status = "帮助文档已打开；使用 :q 返回"
        elif command == "wq":
            if self._help_return_state is not None:
                self.status = "帮助文档为只读；使用 :q 返回"
            else:
                self.save_requested = True
                self.status = "请求系统保存文档"
                self.closed = True
        elif command == "q!":
            if self._help_return_state is not None:
                self._close_help()
            else:
                self.closed = True
        elif command == "q":
            if self._help_return_state is not None:
                self._close_help()
            elif self.buffer.text != self.original_content:
                self.status = "有未保存修改；使用 :wq 保存或 :q! 放弃"
            else:
                self.closed = True
        elif command and self.system_command_handler is not None:
            try:
                self._dispatch_system_command(command)
                self.status = f"系统命令已执行: {command}"
            except Exception as exc:
                self.status = f"系统命令失败: {exc}"
        elif command:
            self.status = f"此命令由系统处理: {command}"
        else:
            self.status = ""

    def _dispatch_system_command(self, command: str) -> None:
        """Run delegated system commands in normal terminal mode for readable output."""
        file_descriptor = self._terminal_fd
        saved_terminal = self._saved_terminal
        if file_descriptor is not None and saved_terminal is not None:
            termios.tcsetattr(file_descriptor, termios.TCSADRAIN, saved_terminal)
        try:
            self.system_command_handler(command)
        finally:
            if file_descriptor is not None and saved_terminal is not None and not self.closed:
                tty.setraw(file_descriptor)

    def _open_help(self) -> None:
        self._help_return_state = (
            self.buffer,
            self.original_content,
            self.top_visual_row,
            self.mode,
            self.pending,
            self.status,
        )
        help_text = Path(__file__).with_name("help.md").read_text(encoding="utf-8")
        self.buffer = TextBuffer(help_text)
        self.original_content = help_text
        self.top_visual_row = 0
        self.mode = "normal"
        self.pending = ""
        self.count_text = ""
        self.status = "只读帮助 | :q 返回文档"

    def _close_help(self) -> None:
        if self._help_return_state is None:
            return
        (
            self.buffer,
            self.original_content,
            self.top_visual_row,
            self.mode,
            self.pending,
            self.status,
        ) = self._help_return_state
        self._help_return_state = None
        self.count_text = ""
