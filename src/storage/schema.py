"""Unified content database schema (protocol version 1).

``create_schema`` receives a management connection and never commits: the
management transaction that creates the tables and sets ``user_version`` must
succeed or roll back as a whole.  ``executescript`` is deliberately avoided
because it commits any open transaction before running.

Constraint responsibilities are split as in the design:

* foreign keys cover workspace/object/branch/revision reference scope,
* partial unique indexes cover active siblings and the single active root,
* ``CHECK`` constraints cover ``kind`` and integer position/version values,
* triggers keep object identity and document revisions immutable.
"""

from __future__ import annotations

import sqlite3

__all__ = ["SCHEMA_VERSION", "create_schema"]

SCHEMA_VERSION = 1

_TABLE_STATEMENTS = (
    """
    CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE objects (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (
            kind IN ('folder', 'document', 'executable', 'resource')
        ),
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, id),
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id)
    )
    """,
    """
    CREATE TABLE branches (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        name TEXT NOT NULL,
        root_object_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, id),
        UNIQUE (workspace_id, name),
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id),
        FOREIGN KEY (workspace_id, root_object_id)
            REFERENCES objects (workspace_id, id)
    )
    """,
    """
    CREATE TABLE document_revisions (
        id TEXT NOT NULL PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        parent_revision_id TEXT,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (workspace_id, object_id, id),
        FOREIGN KEY (workspace_id, object_id)
            REFERENCES objects (workspace_id, id),
        FOREIGN KEY (workspace_id, object_id, parent_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id)
    )
    """,
    """
    CREATE TABLE entries (
        workspace_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        object_id TEXT NOT NULL,
        parent_id TEXT,
        name TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (
            typeof(position) = 'integer' AND position >= 0
        ),
        version INTEGER NOT NULL CHECK (
            typeof(version) = 'integer' AND version >= 1
        ),
        current_revision_id TEXT,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL,
        deleted_at TEXT,
        PRIMARY KEY (workspace_id, branch_id, object_id),
        FOREIGN KEY (workspace_id, branch_id)
            REFERENCES branches (workspace_id, id),
        FOREIGN KEY (workspace_id, object_id)
            REFERENCES objects (workspace_id, id),
        FOREIGN KEY (workspace_id, branch_id, parent_id)
            REFERENCES entries (workspace_id, branch_id, object_id),
        FOREIGN KEY (workspace_id, object_id, current_revision_id)
            REFERENCES document_revisions (workspace_id, object_id, id)
    )
    """,
    """
    CREATE TABLE legacy_imports (
        source_digest TEXT PRIMARY KEY,
        imported_at TEXT NOT NULL,
        object_count INTEGER NOT NULL,
        document_count INTEGER NOT NULL,
        report_json TEXT NOT NULL
    )
    """,
)

_INDEX_STATEMENTS = (
    """
    CREATE UNIQUE INDEX idx_entries_active_sibling_name
        ON entries (workspace_id, branch_id, parent_id, name)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX idx_entries_active_sibling_position
        ON entries (workspace_id, branch_id, parent_id, position)
        WHERE deleted_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX idx_entries_active_root
        ON entries (workspace_id, branch_id)
        WHERE parent_id IS NULL AND deleted_at IS NULL
    """,
)

_TRIGGER_STATEMENTS = (
    """
    CREATE TRIGGER trg_objects_identity_immutable
    BEFORE UPDATE ON objects
    FOR EACH ROW
    WHEN NEW.id <> OLD.id
        OR NEW.workspace_id <> OLD.workspace_id
        OR NEW.kind <> OLD.kind
    BEGIN
        SELECT RAISE(ABORT, 'objects identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_entries_identity_immutable
    BEFORE UPDATE ON entries
    FOR EACH ROW
    WHEN NEW.workspace_id <> OLD.workspace_id
        OR NEW.branch_id <> OLD.branch_id
        OR NEW.object_id <> OLD.object_id
    BEGIN
        SELECT RAISE(ABORT, 'entries identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_branches_identity_immutable
    BEFORE UPDATE ON branches
    FOR EACH ROW
    WHEN NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
    BEGIN
        SELECT RAISE(ABORT, 'branches identity is immutable');
    END
    """,
    """
    CREATE TRIGGER trg_document_revisions_no_update
    BEFORE UPDATE ON document_revisions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'document_revisions are append-only');
    END
    """,
    """
    CREATE TRIGGER trg_document_revisions_no_delete
    BEFORE DELETE ON document_revisions
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'document_revisions are append-only');
    END
    """,
)

_SCHEMA_STATEMENTS = _TABLE_STATEMENTS + _INDEX_STATEMENTS + _TRIGGER_STATEMENTS


def create_schema(connection: sqlite3.Connection) -> None:
    """Create protocol version 1 tables in *connection* without committing."""

    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
