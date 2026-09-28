"""Document-only modal terminal editor."""

from .buffer import TextBuffer
from .editor import Editor, EditorResult, SystemCommandHandler

__all__ = ["Editor", "EditorResult", "SystemCommandHandler", "TextBuffer"]
