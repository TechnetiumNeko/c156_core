"""Alembic-backed schema lifecycle on caller-owned sqlite3 transactions.

Alembic generates static SQLite SQL; sqlite3 executes complete statements (not
executescript, which commits). SQLAlchemy never wraps or closes the caller's
connection. The immutable revisions are the sole schema definition.
"""
from __future__ import annotations

from functools import lru_cache
from io import StringIO
from pathlib import Path
import re
import sqlite3
from threading import RLock

from .errors import BusyError, SchemaError

BASELINE_REVISION = "0001_protocol2_baseline"
HEAD_REVISION = "0002_history_operations"
LEGACY_WRITER_BLOCKER = 3
_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_LOCK = RLock()


def _config(output: StringIO):
    from alembic.config import Config
    config = Config(str(_ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    return config


@lru_cache(maxsize=8)
def _upgrade_sql(start: str, end: str) -> str:
    from alembic import command
    output = StringIO()
    with _ALEMBIC_LOCK:
        command.upgrade(_config(output), f"{start}:{end}", sql=True)
    return output.getvalue()


def _execute_sql(connection: sqlite3.Connection, sql: str) -> None:
    statement = ""
    for character in sql:
        statement += character
        if character == ";" and sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaError("migration emitted an incomplete SQL statement")


def _upgrade(connection: sqlite3.Connection, start: str, end: str) -> None:
    _execute_sql(connection, _upgrade_sql(start, end))


def _stamp_baseline(connection: sqlite3.Connection) -> None:
    from alembic import command
    output = StringIO()
    with _ALEMBIC_LOCK:
        command.stamp(_config(output), BASELINE_REVISION, sql=True)
    _execute_sql(connection, output.getvalue())


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"
    )}


def validate_schema(connection: sqlite3.Connection) -> str:
    """Read-only runtime gate; never stamp or implicitly migrate an old database."""
    try:
        if "alembic_version" not in _tables(connection):
            raise SchemaError("database requires explicit upgrade; Alembic revision missing")
        revisions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version")]
        if revisions != [HEAD_REVISION]:
            raise SchemaError("unsupported Alembic revision", details={"expected": HEAD_REVISION, "actual": revisions})
        marker = connection.execute("PRAGMA user_version").fetchone()[0]
        if marker != LEGACY_WRITER_BLOCKER:
            raise SchemaError("legacy writer blocker is missing", details={"actual": marker})
    except sqlite3.DatabaseError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise BusyError(str(exc)) from exc
        raise SchemaError("database file is not a usable content database") from exc
    return HEAD_REVISION


def _normalize_sql(sql: str) -> tuple[str, ...]:
    # Ignore formatting only; preserve quoted values and every constraint token.
    return tuple(re.findall(r"'(?:(?:'')|[^'])*'|\"[^\"]*\"|\w+|[^\s]", sql))


def _signature(connection: sqlite3.Connection) -> tuple:
    objects = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT GLOB 'sqlite_*' AND name != 'alembic_version' ORDER BY type,name"
    ).fetchall()
    definitions = tuple((row[0], row[1], row[2], _normalize_sql(row[3] or "")) for row in objects)
    structures = []
    for table in sorted(_tables(connection) - {"alembic_version"}):
        quoted = '"' + table.replace('"', '""') + '"'
        structures.append((table,
            tuple(tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({quoted})")),
            tuple(tuple(row) for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")),
            tuple(tuple(row) for row in connection.execute(f"PRAGMA index_list({quoted})"))))
    return definitions, tuple(structures)


@lru_cache(maxsize=2)
def _expected_signature(revision: str) -> tuple:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("BEGIN")
        _upgrade(connection, "base", revision)
        return _signature(connection)
    finally:
        connection.close()


def _check_integrity(connection: sqlite3.Connection) -> None:
    if [tuple(row) for row in connection.execute("PRAGMA integrity_check")] != [("ok",)]:
        raise SchemaError("database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SchemaError("database foreign key check failed")


def _validate_structure(connection: sqlite3.Connection, revision: str) -> None:
    if _signature(connection) != _expected_signature(revision):
        raise SchemaError("database does not match the supported schema", details={"revision": revision})
    _check_integrity(connection)


def validate_backup_schema(connection: sqlite3.Connection) -> str:
    """Recognize source protocol 2 or current head, including structure and data."""
    if "alembic_version" in _tables(connection):
        revision = validate_schema(connection)
    else:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise SchemaError("unrecognized protocol 2 baseline")
        revision = BASELINE_REVISION
    _validate_structure(connection, revision)
    return revision


def create_schema(connection: sqlite3.Connection) -> None:
    """Build current head inside an already-open, caller-owned transaction."""
    if not connection.in_transaction:
        raise SchemaError("create_schema requires a caller-owned transaction")
    if _tables(connection):
        raise sqlite3.OperationalError("schema creation requires an empty database")
    _upgrade(connection, "base", HEAD_REVISION)
    _check_integrity(connection)


def upgrade_database(database: Path) -> str:
    """Explicitly adopt protocol 2 or build an empty existing database atomically.

    Writers must already be stopped. This entry point never creates a missing
    path, changes journal mode, or replaces an existing file on failure.
    """
    path = Path(database)
    connection = sqlite3.connect(path.absolute().as_uri() + "?mode=rw", uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        # SQLite cannot change this setting within a transaction. Populated
        # revision/access-rule rebuilds require OFF, then explicit verification.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if not _tables(connection):
                if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
                    raise SchemaError("unrecognized empty database version")
                create_schema(connection)
            elif "alembic_version" in _tables(connection):
                validate_schema(connection)
            else:
                validate_backup_schema(connection)
                _stamp_baseline(connection)
                _upgrade(connection, BASELINE_REVISION, HEAD_REVISION)
            validate_schema(connection)
            _validate_structure(connection, HEAD_REVISION)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    return HEAD_REVISION
