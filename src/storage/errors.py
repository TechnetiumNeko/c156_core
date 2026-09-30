"""Storage-layer errors for SQLite protocol, connections and transactions.

These errors stay inside the storage boundary.  The service layer translates
them into domain errors (for example ``BusyError`` -> ``StorageBusy``) and never
formats terminal or HTTP output here.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["StorageError", "BusyError", "ConstraintError", "SchemaError"]


class StorageError(Exception):
    """Base class for recoverable storage failures."""

    code = "storage_error"

    def __init__(
        self, message: str = "", *, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}


class BusyError(StorageError):
    """SQLite could not acquire the requested lock within the busy timeout."""

    code = "storage_busy"


class ConstraintError(StorageError):
    """A database constraint rejected a write.

    ``constraint`` names the category that failed (``unique``, ``foreign_key``,
    ``check``, ``not_null``, ``primary_key`` or ``trigger``).  The original
    ``sqlite3.IntegrityError`` is preserved as ``__cause__``.
    """

    code = "storage_constraint"

    def __init__(
        self,
        message: str = "",
        *,
        constraint: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.constraint = constraint


class SchemaError(StorageError):
    """The database protocol or runtime configuration is not compatible."""

    code = "storage_schema"
