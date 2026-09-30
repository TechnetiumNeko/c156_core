"""Structured domain errors for the content kernel.

Errors carry a stable machine-readable ``code`` and a structured ``details``
mapping.  They never format terminal text or HTTP responses; entry points map
them to their own presentation.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = [
    "ContentError",
    "NotFound",
    "AlreadyExists",
    "NotDirectory",
    "NotDocument",
    "InvalidName",
    "InvalidArgument",
    "PathOutsideRoot",
    "Conflict",
    "DirectoryNotEmpty",
    "ProtectedNode",
    "InvalidMove",
    "StorageBusy",
    "UnsupportedSchema",
    "MigrationError",
]


class ContentError(Exception):
    """Base class for all recoverable content-domain errors."""

    code = "content_error"

    def __init__(self, message: str = "", *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({self.message!r}, code={self.code!r})"


class NotFound(ContentError):
    """The object does not exist in the requested scope, or is deleted."""

    code = "not_found"


class AlreadyExists(ContentError):
    """An active sibling already uses the requested name."""

    code = "already_exists"


class NotDirectory(ContentError):
    """The node exists but is not an active folder."""

    code = "not_directory"


class NotDocument(ContentError):
    """The node exists but is not a document."""

    code = "not_document"


class InvalidName(ContentError):
    """A file or folder name violates the naming rules."""

    code = "invalid_name"


class InvalidArgument(ContentError):
    """An operation argument, path or metadata value is invalid."""

    code = "invalid_argument"


class PathOutsideRoot(ContentError):
    """The resolved path escapes the access root."""

    code = "path_outside_root"


class Conflict(ContentError):
    """An expected version or revision no longer matches storage."""

    code = "conflict"


class DirectoryNotEmpty(ContentError):
    """A non-recursive delete of a folder with active children."""

    code = "directory_not_empty"


class ProtectedNode(ContentError):
    """A protected root or top-level node cannot be modified."""

    code = "protected_node"


class InvalidMove(ContentError):
    """A move would place a folder inside itself or a descendant."""

    code = "invalid_move"


class StorageBusy(ContentError):
    """The database lock could not be acquired within the busy timeout."""

    code = "storage_busy"


class UnsupportedSchema(ContentError):
    """The database protocol version or default tree is not usable."""

    code = "unsupported_schema"


class MigrationError(ContentError):
    """Legacy data could not be scanned, validated or imported."""

    code = "migration_error"
