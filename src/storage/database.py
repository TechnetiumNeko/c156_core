"""SQLite connection and transaction boundary.

Runtime opening never creates a file, never creates or repairs a schema and
never switches journal mode.  Management code owns database creation and
runtime configuration through :meth:`Database.management_connection` and
:meth:`Database.configure_runtime`.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import BusyError, ConstraintError, SchemaError, StorageError
from .migrations import validate_schema

__all__ = ["Database"]


def _constraint_kind(message: str) -> str:
    lowered = message.lower()
    if lowered.startswith("unique constraint failed"):
        return "unique"
    if lowered.startswith("primary key constraint failed"):
        return "primary_key"
    if lowered.startswith("foreign key constraint failed"):
        return "foreign_key"
    if lowered.startswith("check constraint failed"):
        return "check"
    if lowered.startswith("not null constraint failed"):
        return "not_null"
    return "trigger"


def _translate(exc: sqlite3.Error) -> StorageError:
    """Map a raw SQLite failure onto the storage error hierarchy."""

    message = str(exc)
    details = {"sqlite_error": type(exc).__name__, "sqlite_message": message}
    if isinstance(exc, sqlite3.IntegrityError):
        kind = _constraint_kind(message)
        return ConstraintError(
            message, constraint=kind, details={**details, "constraint": kind}
        )
    if isinstance(exc, sqlite3.OperationalError):
        lowered = message.lower()
        if "locked" in lowered or "busy" in lowered:
            return BusyError(message, details=details)
        return StorageError(message, details=details)
    return StorageError(message, details=details)


class Database:
    """Opens short-lived SQLite connections for one database file."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        if (
            isinstance(busy_timeout_ms, bool)
            or not isinstance(busy_timeout_ms, int)
            or busy_timeout_ms < 0
        ):
            raise ValueError("busy_timeout_ms must be a non-negative integer")
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms

    # -- connection helpers -------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        """Open an existing file read/write; missing files are never created."""

        uri = self.path.absolute().as_uri() + "?mode=rw"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=self.busy_timeout_ms / 1000.0,
            )
        except sqlite3.Error as exc:
            raise _translate(exc) from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        except sqlite3.Error as exc:
            connection.close()
            raise _translate(exc) from exc
        return connection

    def _require_runtime(self, connection: sqlite3.Connection) -> None:
        """Reject empty, future-version or non-WAL targets without modifying them."""

        validate_schema(connection)
        try:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
            raise SchemaError(
                "could not read the database journal mode",
                details={"path": str(self.path)},
            ) from exc
        if str(journal_mode).lower() != "wal":
            raise SchemaError(
                "database is not configured for WAL",
                details={"journal_mode": journal_mode, "path": str(self.path)},
            )

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - never mask the real error
                pass

    # -- public boundary ----------------------------------------------------

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a short read or ``BEGIN IMMEDIATE`` transaction.

        The connection is always closed on exit.  A failed ``BEGIN``, business
        step or ``COMMIT`` rolls back any open transaction and preserves the
        original failure as the raised error's cause.
        """

        connection = self._open()
        try:
            self._require_runtime(connection)
            begin = "BEGIN IMMEDIATE" if write else "BEGIN"
            try:
                connection.execute(begin)
            except sqlite3.Error as exc:
                raise _translate(exc) from exc
            try:
                yield connection
            except sqlite3.Error as exc:
                self._rollback(connection)
                raise _translate(exc) from exc
            except BaseException:
                self._rollback(connection)
                raise
            try:
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback(connection)
                raise _translate(exc) from exc
        finally:
            connection.close()

    @contextmanager
    def management_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection for existing-database management and validation.

        This permits non-WAL databases and never creates tables; callers own any
        explicit transaction or ``PRAGMA`` change.
        """

        connection = self._open()
        try:
            yield connection
        except sqlite3.Error as exc:
            raise _translate(exc) from exc
        finally:
            connection.close()

    def configure_runtime(self) -> None:
        """Validate the protocol and enable WAL for an existing database."""

        with self.management_connection() as connection:
            validate_schema(connection)
            current = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if str(current).lower() == "wal":
                return
            try:
                result = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            except sqlite3.Error as exc:
                translated = _translate(exc)
                if isinstance(translated, BusyError):
                    # A held lock is a transient busy condition, not a schema
                    # failure; management code maps it to the domain StorageBusy.
                    raise translated from exc
                raise SchemaError(
                    "could not enable WAL journal mode",
                    details={"path": str(self.path)},
                ) from exc
            if str(result).lower() != "wal":
                raise SchemaError(
                    "database did not switch to WAL journal mode",
                    details={"journal_mode": result, "path": str(self.path)},
                )
