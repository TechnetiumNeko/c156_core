"""Scoped SQLite data access for entries and document revisions.

The repository receives an already-open connection from the service layer and
only issues parameterised SQL for one explicit ``(workspace_id, branch_id)``
scope.  It never owns a transaction boundary, never opens or closes a
connection, and never creates schema objects.  Conditional writes return the
affected row count so the service can turn a stale expectation into a domain
conflict.

Recursive lookups carry a visited path so a contradictory parent chain cannot
loop forever on damaged data.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from .records import EntryRecord, RevisionRecord

__all__ = ["Repository"]

_ENTRY_COLUMNS = (
    "e.workspace_id, e.branch_id, e.object_id, o.kind, e.parent_id, e.name, "
    "e.position, e.version, e.current_revision_id, e.metadata_json, "
    "e.created_at, e.modified_at, e.deleted_at"
)

_ENTRY_JOIN = (
    "FROM entries AS e JOIN objects AS o "
    "ON o.workspace_id = e.workspace_id AND o.id = e.object_id"
)

#: Fixed, internal whitelist.  Callers can never inject an arbitrary column.
_UPDATABLE_COLUMNS = (
    "parent_id",
    "name",
    "position",
    "version",
    "current_revision_id",
    "metadata_json",
    "modified_at",
    "deleted_at",
)

_ANCESTORS_SQL = """
WITH RECURSIVE chain (
    workspace_id, branch_id, object_id, parent_id, name, position, version,
    current_revision_id, metadata_json, created_at, modified_at, deleted_at,
    kind, depth, visited
) AS (
    SELECT e.workspace_id, e.branch_id, e.object_id, e.parent_id, e.name,
           e.position, e.version, e.current_revision_id, e.metadata_json,
           e.created_at, e.modified_at, e.deleted_at, o.kind, 0,
           ',' || e.object_id || ','
    FROM entries AS e
    JOIN objects AS o
      ON o.workspace_id = e.workspace_id AND o.id = e.object_id
    WHERE e.workspace_id = ? AND e.branch_id = ? AND e.object_id = ?
    UNION ALL
    SELECT p.workspace_id, p.branch_id, p.object_id, p.parent_id, p.name,
           p.position, p.version, p.current_revision_id, p.metadata_json,
           p.created_at, p.modified_at, p.deleted_at, po.kind, c.depth + 1,
           c.visited || p.object_id || ','
    FROM chain AS c
    JOIN entries AS p
      ON p.workspace_id = c.workspace_id
     AND p.branch_id = c.branch_id
     AND p.object_id = c.parent_id
    JOIN objects AS po
      ON po.workspace_id = p.workspace_id AND po.id = p.object_id
    WHERE instr(c.visited, ',' || p.object_id || ',') = 0
)
SELECT * FROM chain ORDER BY depth
"""

_SUBTREE_SQL = """
WITH RECURSIVE tree (
    workspace_id, branch_id, object_id, parent_id, name, position, version,
    current_revision_id, metadata_json, created_at, modified_at, deleted_at,
    kind, sort_path, visited
) AS (
    SELECT e.workspace_id, e.branch_id, e.object_id, e.parent_id, e.name,
           e.position, e.version, e.current_revision_id, e.metadata_json,
           e.created_at, e.modified_at, e.deleted_at, o.kind,
           printf('%020d', e.position), ',' || e.object_id || ','
    FROM entries AS e
    JOIN objects AS o
      ON o.workspace_id = e.workspace_id AND o.id = e.object_id
    WHERE e.workspace_id = ? AND e.branch_id = ? AND e.object_id = ?
      AND e.deleted_at IS NULL
    UNION ALL
    SELECT ch.workspace_id, ch.branch_id, ch.object_id, ch.parent_id, ch.name,
           ch.position, ch.version, ch.current_revision_id, ch.metadata_json,
           ch.created_at, ch.modified_at, ch.deleted_at, co.kind,
           c.sort_path || '/' || printf('%020d', ch.position),
           c.visited || ch.object_id || ','
    FROM tree AS c
    JOIN entries AS ch
      ON ch.workspace_id = c.workspace_id
     AND ch.branch_id = c.branch_id
     AND ch.parent_id = c.object_id
     AND ch.deleted_at IS NULL
    JOIN objects AS co
      ON co.workspace_id = ch.workspace_id AND co.id = ch.object_id
    WHERE instr(c.visited, ',' || ch.object_id || ',') = 0
)
SELECT * FROM tree ORDER BY sort_path, object_id
"""


def _entry_record(row: sqlite3.Row) -> EntryRecord:
    return EntryRecord(
        workspace_id=row["workspace_id"],
        branch_id=row["branch_id"],
        object_id=row["object_id"],
        kind=row["kind"],
        parent_id=row["parent_id"],
        name=row["name"],
        position=row["position"],
        version=row["version"],
        current_revision_id=row["current_revision_id"],
        metadata_json=row["metadata_json"],
        created_at=row["created_at"],
        modified_at=row["modified_at"],
        deleted_at=row["deleted_at"],
    )


class Repository:
    """Parameterised queries and conditional writes for one branch scope."""

    def __init__(
        self, connection: sqlite3.Connection, *, workspace_id: str, branch_id: str
    ) -> None:
        self._connection = connection
        self._workspace_id = workspace_id
        self._branch_id = branch_id

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def branch_id(self) -> str:
        return self._branch_id

    # -- scoped reads -------------------------------------------------------

    def get_entry(self, object_id: str) -> EntryRecord | None:
        """Return the entry for *object_id* in this scope, deleted or not."""

        row = self._connection.execute(
            f"SELECT {_ENTRY_COLUMNS} {_ENTRY_JOIN} "
            "WHERE e.workspace_id = ? AND e.branch_id = ? AND e.object_id = ?",
            (self._workspace_id, self._branch_id, object_id),
        ).fetchone()
        return _entry_record(row) if row is not None else None

    def get_branch_root_id(self) -> str | None:
        """Return this branch's root object id, or ``None`` when absent."""

        row = self._connection.execute(
            "SELECT root_object_id FROM branches WHERE workspace_id = ? AND id = ?",
            (self._workspace_id, self._branch_id),
        ).fetchone()
        return row["root_object_id"] if row is not None else None

    def find_child(self, parent_id: str | None, name: str) -> EntryRecord | None:
        """Return the active child of *parent_id* named *name*, if any."""

        row = self._connection.execute(
            f"SELECT {_ENTRY_COLUMNS} {_ENTRY_JOIN} "
            "WHERE e.workspace_id = ? AND e.branch_id = ? AND e.parent_id IS ? "
            "AND e.name = ? AND e.deleted_at IS NULL",
            (self._workspace_id, self._branch_id, parent_id, name),
        ).fetchone()
        return _entry_record(row) if row is not None else None

    def list_children(self, parent_id: str | None) -> list[EntryRecord]:
        """Return active children of *parent_id* ordered by position."""

        rows = self._connection.execute(
            f"SELECT {_ENTRY_COLUMNS} {_ENTRY_JOIN} "
            "WHERE e.workspace_id = ? AND e.branch_id = ? AND e.parent_id IS ? "
            "AND e.deleted_at IS NULL ORDER BY e.position, e.object_id",
            (self._workspace_id, self._branch_id, parent_id),
        ).fetchall()
        return [_entry_record(row) for row in rows]

    def ancestors(self, object_id: str) -> list[EntryRecord]:
        """Return the parent chain from *object_id* up to the branch root."""

        rows = self._connection.execute(
            _ANCESTORS_SQL,
            (self._workspace_id, self._branch_id, object_id),
        ).fetchall()
        return [_entry_record(row) for row in rows]

    def subtree(self, object_id: str) -> list[EntryRecord]:
        """Return the active subtree of *object_id* in pre-order.

        Deleted nodes and their descendants are excluded.  A deleted start node
        yields an empty list.
        """

        rows = self._connection.execute(
            _SUBTREE_SQL,
            (self._workspace_id, self._branch_id, object_id),
        ).fetchall()
        return [_entry_record(row) for row in rows]

    def get_revision(
        self, object_id: str, revision_id: str
    ) -> RevisionRecord | None:
        """Return a revision by workspace and object, independent of branch."""

        row = self._connection.execute(
            "SELECT id, workspace_id, object_id, parent_revision_id, content, "
            "created_at FROM document_revisions "
            "WHERE workspace_id = ? AND object_id = ? AND id = ?",
            (self._workspace_id, object_id, revision_id),
        ).fetchone()
        if row is None:
            return None
        return RevisionRecord(
            id=row["id"],
            workspace_id=row["workspace_id"],
            object_id=row["object_id"],
            parent_revision_id=row["parent_revision_id"],
            content=row["content"],
            created_at=row["created_at"],
        )

    # -- writes -------------------------------------------------------------

    def insert_object(self, object_id: str, kind: str, created_at: str) -> None:
        """Insert a stable object identity into this repository's workspace."""

        self._connection.execute(
            "INSERT INTO objects (id, workspace_id, kind, created_at) "
            "VALUES (?,?,?,?)",
            (object_id, self._workspace_id, kind, created_at),
        )

    def insert_entry(self, record: EntryRecord) -> None:
        """Insert one entry row from *record*.

        The record must belong to this repository's exact workspace and branch;
        mismatched input raises :class:`ValueError` before any SQL runs.  The
        repository never rewrites the record scope silently.
        """

        if (
            record.workspace_id != self._workspace_id
            or record.branch_id != self._branch_id
        ):
            raise ValueError(
                "insert_entry record scope "
                f"({record.workspace_id!r}, {record.branch_id!r}) does not match "
                f"repository scope ({self._workspace_id!r}, {self._branch_id!r})"
            )
        self._connection.execute(
            "INSERT INTO entries (workspace_id, branch_id, object_id, parent_id, "
            "name, position, version, current_revision_id, metadata_json, "
            "created_at, modified_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.workspace_id,
                record.branch_id,
                record.object_id,
                record.parent_id,
                record.name,
                record.position,
                record.version,
                record.current_revision_id,
                record.metadata_json,
                record.created_at,
                record.modified_at,
                record.deleted_at,
            ),
        )

    def insert_revision(self, record: RevisionRecord) -> None:
        """Append one immutable document revision from *record*.

        Revisions are scoped by workspace and object, not by branch, so the
        record workspace must match this repository's workspace.  Mismatched
        input raises :class:`ValueError` before any SQL runs and is never
        rewritten to the repository workspace.
        """

        if record.workspace_id != self._workspace_id:
            raise ValueError(
                "insert_revision record workspace "
                f"{record.workspace_id!r} does not match repository workspace "
                f"{self._workspace_id!r}"
            )
        self._connection.execute(
            "INSERT INTO document_revisions "
            "(id, workspace_id, object_id, parent_revision_id, content, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                record.id,
                record.workspace_id,
                record.object_id,
                record.parent_revision_id,
                record.content,
                record.created_at,
            ),
        )

    def update_entry(
        self,
        object_id: str,
        changes: dict,
        *,
        expected_version: int | None = None,
        expected_revision_id: str | None = None,
    ) -> int:
        """Conditionally update one active entry and return the row count.

        Only the fixed column whitelist is accepted.  The statement always
        requires ``deleted_at IS NULL`` and adds the version and revision
        conditions only when the caller supplies the corresponding expectation.
        """

        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_entry requires a non-empty changes mapping")
        unknown = [key for key in changes if key not in _UPDATABLE_COLUMNS]
        if unknown:
            raise ValueError(
                "update_entry does not accept columns: " + ", ".join(sorted(unknown))
            )

        assignments = []
        parameters: list[object] = []
        for column in _UPDATABLE_COLUMNS:
            if column in changes:
                assignments.append(f"{column} = ?")
                parameters.append(changes[column])

        sql = (
            f"UPDATE entries SET {', '.join(assignments)} "
            "WHERE workspace_id = ? AND branch_id = ? AND object_id = ? "
            "AND deleted_at IS NULL"
        )
        parameters.extend((self._workspace_id, self._branch_id, object_id))
        if expected_version is not None:
            sql += " AND version = ?"
            parameters.append(expected_version)
        if expected_revision_id is not None:
            sql += " AND current_revision_id = ?"
            parameters.append(expected_revision_id)

        cursor = self._connection.execute(sql, parameters)
        return cursor.rowcount

    def touch_entries(self, object_ids: Iterable[str], modified_at: str) -> int:
        """Increment ``version`` once per distinct active id and stamp the time."""

        unique_ids = list(dict.fromkeys(object_ids))
        if not unique_ids:
            return 0
        placeholders = ", ".join("?" for _ in unique_ids)
        sql = (
            "UPDATE entries SET version = version + 1, modified_at = ? "
            "WHERE workspace_id = ? AND branch_id = ? AND deleted_at IS NULL "
            f"AND object_id IN ({placeholders})"
        )
        parameters: list[object] = [
            modified_at,
            self._workspace_id,
            self._branch_id,
            *unique_ids,
        ]
        return self._connection.execute(sql, parameters).rowcount

    def next_position(self, parent_id: str | None) -> int:
        """Return ``MAX(position) + 1`` over the active children of ``parent_id``."""

        row = self._connection.execute(
            "SELECT MAX(position) AS max_position FROM entries "
            "WHERE workspace_id = ? AND branch_id = ? AND parent_id IS ? "
            "AND deleted_at IS NULL",
            (self._workspace_id, self._branch_id, parent_id),
        ).fetchone()
        if row is None or row["max_position"] is None:
            return 0
        return int(row["max_position"]) + 1
