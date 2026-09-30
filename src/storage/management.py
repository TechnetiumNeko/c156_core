"""Explicit database initialization and default-tree validation.

The management layer is the only place that creates a new content database.
Initialization builds the protocol schema and the default workspace tree inside
one transaction, then configures WAL.  An already existing target is only
validated (protocol and default tree) and idempotently reconfigured; it is
never overwritten, repaired or rebuilt.

Runtime reads must not call these functions for side effects; they open a read
transaction and validate only.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..core.errors import StorageBusy, UnsupportedSchema
from ..core.models import ContentScope
from .database import Database
from .errors import BusyError, StorageError
from .records import EntryRecord
from .repository import (
    Repository,
    insert_branch,
    insert_workspace,
    lookup_default_main,
)
from .schema import SCHEMA_VERSION, create_schema

__all__ = [
    "DEFAULT_TOP_LEVEL_NAMES",
    "initialize_database",
    "validate_default_tree",
]

#: Protected top-level folders created under the workspace root, in order.
DEFAULT_TOP_LEVEL_NAMES = ("main", "admin", "resource", "bin")

_WORKSPACE_NAME = "default"
_BRANCH_NAME = "main"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remove_new_artifacts(path: Path) -> None:
    """Remove only the files a failed first-time initialization may have made."""

    for candidate in (
        path,
        Path(str(path) + "-journal"),
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError:  # pragma: no cover - best-effort cleanup
            continue


def _create_default_tree(path: Path) -> ContentScope:
    now = _utc_now()
    workspace_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())
    root_id = str(uuid.uuid4())
    top_level_ids = [str(uuid.uuid4()) for _ in DEFAULT_TOP_LEVEL_NAMES]

    database = Database(path)
    with database.management_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            create_schema(connection)
            insert_workspace(connection, workspace_id, _WORKSPACE_NAME, now)
            repo = Repository(
                connection, workspace_id=workspace_id, branch_id=branch_id
            )
            repo.insert_object(root_id, "folder", now)
            for object_id in top_level_ids:
                repo.insert_object(object_id, "folder", now)
            insert_branch(
                connection, workspace_id, branch_id, _BRANCH_NAME, root_id, now
            )
            repo.insert_entry(
                EntryRecord(
                    workspace_id=workspace_id,
                    branch_id=branch_id,
                    object_id=root_id,
                    kind="folder",
                    parent_id=None,
                    name="",
                    position=0,
                    version=1,
                    current_revision_id=None,
                    metadata_json="{}",
                    created_at=now,
                    modified_at=now,
                    deleted_at=None,
                )
            )
            for position, (name, object_id) in enumerate(
                zip(DEFAULT_TOP_LEVEL_NAMES, top_level_ids)
            ):
                repo.insert_entry(
                    EntryRecord(
                        workspace_id=workspace_id,
                        branch_id=branch_id,
                        object_id=object_id,
                        kind="folder",
                        parent_id=root_id,
                        name=name,
                        position=position,
                        version=1,
                        current_revision_id=None,
                        metadata_json="{}",
                        created_at=now,
                        modified_at=now,
                        deleted_at=None,
                    )
                )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    return ContentScope(
        workspace_id=workspace_id, branch_id=branch_id, root_id=root_id
    )


def _validate_existing(path: Path) -> ContentScope:
    database = Database(path)
    try:
        with database.management_connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise UnsupportedSchema(
                    "unsupported database protocol version",
                    details={
                        "expected": SCHEMA_VERSION,
                        "actual": version,
                        "path": str(path),
                    },
                )
            scope = validate_default_tree(connection)
    except UnsupportedSchema:
        raise
    except BusyError as exc:
        raise StorageBusy(str(exc), details=dict(exc.details)) from exc
    except (sqlite3.DatabaseError, StorageError) as exc:
        raise UnsupportedSchema(
            "database file is not a usable content database",
            details={"path": str(path)},
        ) from exc

    try:
        database.configure_runtime()
    except BusyError as exc:
        raise StorageBusy(str(exc), details=dict(exc.details)) from exc
    return scope


def initialize_database(database: Path) -> ContentScope:
    """Create a new content database, or validate and reconfigure an existing one.

    A brand-new file is created exclusively; the schema and default tree commit
    atomically before WAL is configured.  A failed first-time initialization
    removes only its own artifacts.  An existing target is validated against the
    protocol version and default tree and never has its content modified.
    """

    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
    except FileExistsError:
        return _validate_existing(path)
    os.close(descriptor)

    try:
        scope = _create_default_tree(path)
    except BusyError as exc:
        _remove_new_artifacts(path)
        raise StorageBusy(str(exc), details=dict(exc.details)) from exc
    except BaseException:
        _remove_new_artifacts(path)
        raise
    database = Database(path)
    try:
        database.configure_runtime()
    except BusyError as exc:
        # The committed database is complete and stays in place so a rerun can
        # finish the WAL configuration without rebuilding content.
        raise StorageBusy(str(exc), details=dict(exc.details)) from exc
    return scope


def validate_default_tree(connection: sqlite3.Connection) -> ContentScope:
    """Validate the default workspace, branch and protected top-level folders.

    Returns the workspace-root :class:`ContentScope`.  A missing, ambiguous or
    structurally invalid default tree raises :class:`UnsupportedSchema` and
    never repairs the database.
    """

    pair = lookup_default_main(connection)
    workspace_id, branch_id = pair
    repo = Repository(connection, workspace_id=workspace_id, branch_id=branch_id)
    root_id = repo.get_branch_root_id()
    if root_id is None:
        raise UnsupportedSchema(
            "main branch has no root object",
            details={"workspace_id": workspace_id, "branch_id": branch_id},
        )
    root = repo.get_entry(root_id)
    if (
        root is None
        or root.deleted_at is not None
        or root.kind != "folder"
        or root.parent_id is not None
        or root.name != ""
    ):
        raise UnsupportedSchema(
            "default workspace root is missing or invalid",
            details={"root_id": root_id},
        )
    for name in DEFAULT_TOP_LEVEL_NAMES:
        child = repo.find_child(root_id, name)
        if (
            child is None
            or child.deleted_at is not None
            or child.kind != "folder"
            or child.parent_id != root_id
        ):
            raise UnsupportedSchema(
                "protected top-level folder is missing or invalid",
                details={"name": name, "root_id": root_id},
            )
    return ContentScope(
        workspace_id=workspace_id, branch_id=branch_id, root_id=root_id
    )
