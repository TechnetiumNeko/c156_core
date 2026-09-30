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

import json
import sqlite3
from typing import Iterable

from ..core.errors import InvalidArgument, UnsupportedSchema
from ..core.json_values import json_equal, validate_metadata
from .errors import ConstraintError, StorageError
from .records import EntryRecord, RevisionRecord

__all__ = [
    "Repository",
    "get_import_report",
    "insert_branch",
    "insert_import_report",
    "insert_workspace",
    "is_sibling_name_conflict",
    "lookup_default_main",
    "verify_integrity",
]

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

#: Fixed host parameters in ``soft_delete_entries`` besides the id list:
#: ``modified_at``, ``deleted_at``, ``workspace_id`` and ``branch_id``.
_SOFT_DELETE_FIXED_PARAMETERS = 4

#: Conservative fallback batch size when the connection cannot report its
#: current SQLite host-variable limit.
_DEFAULT_VARIABLE_LIMIT = 999

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


def is_sibling_name_conflict(error: ConstraintError) -> bool:
    """Return whether *error* is the active-sibling NAME unique index.

    Only this one index means a duplicate name.  The sibling position index,
    the single-root index and the branch name index are different constraints
    and must never be reported as :class:`AlreadyExists`.
    """

    if getattr(error, "constraint", None) != "unique":
        return False
    details = getattr(error, "details", None) or {}
    message = str(details.get("sqlite_message", "")) or str(error)
    return "entries.name" in message


# -- management helpers -----------------------------------------------------
#
# These module-level functions receive a management connection and never begin,
# commit or roll back a transaction themselves.  They are the only place the
# initialization path inserts the workspace and branch rows.


def insert_workspace(
    connection: sqlite3.Connection,
    workspace_id: str,
    name: str,
    created_at: str,
) -> None:
    """Insert one workspace row on the caller's management connection."""

    connection.execute(
        "INSERT INTO workspaces (id, name, created_at) VALUES (?,?,?)",
        (workspace_id, name, created_at),
    )


def insert_branch(
    connection: sqlite3.Connection,
    workspace_id: str,
    branch_id: str,
    name: str,
    root_object_id: str,
    created_at: str,
) -> None:
    """Insert one branch row on the caller's management connection."""

    connection.execute(
        "INSERT INTO branches (id, workspace_id, name, root_object_id, created_at) "
        "VALUES (?,?,?,?,?)",
        (branch_id, workspace_id, name, root_object_id, created_at),
    )


def lookup_default_main(connection: sqlite3.Connection) -> tuple[str, str]:
    """Return the single workspace and its ``main`` branch.

    Zero or multiple workspaces, or a missing/duplicate ``main`` branch, raise
    :class:`UnsupportedSchema`; an arbitrary first row is never returned.
    """

    workspaces = connection.execute("SELECT id FROM workspaces").fetchall()
    if len(workspaces) != 1:
        raise UnsupportedSchema(
            "database does not contain exactly one workspace",
            details={"workspace_count": len(workspaces)},
        )
    workspace_id = workspaces[0][0]
    branches = connection.execute(
        "SELECT id FROM branches WHERE workspace_id = ? AND name = ?",
        (workspace_id, "main"),
    ).fetchall()
    if len(branches) != 1:
        raise UnsupportedSchema(
            "workspace does not contain exactly one main branch",
            details={"workspace_id": workspace_id, "main_count": len(branches)},
        )
    return (workspace_id, branches[0][0])


def get_import_report(
    connection: sqlite3.Connection, source_digest: str
) -> dict | None:
    """Return the persisted migration report for *source_digest*, or ``None``.

    The report is the canonical strict-JSON payload written by
    :func:`insert_import_report`; it is parsed back into an independent plain
    dictionary.  A missing row means the source was never imported, while an
    unreadable stored payload is a storage integrity failure.
    """

    if not isinstance(source_digest, str) or source_digest == "":
        raise ValueError("source_digest must be a non-empty string")
    row = connection.execute(
        "SELECT source_digest, imported_at, object_count, document_count, report_json "
        "FROM legacy_imports WHERE source_digest = ?",
        (source_digest,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row["report_json"])
    except (TypeError, ValueError) as exc:
        raise StorageError(
            "stored legacy import report is not valid JSON",
            details={"source_digest": source_digest},
        ) from exc
    if not isinstance(value, dict):
        raise StorageError(
            "stored legacy import report is not a JSON object",
            details={"source_digest": source_digest},
        )
    try:
        validate_metadata(value, reject_reserved=False)
        counts = value.get("counts")
        if not isinstance(counts, dict) or not all((
            value.get("source_digest") == row["source_digest"],
            value.get("imported_at") == row["imported_at"],
            json_equal(counts.get("objects"), row["object_count"]),
            json_equal(counts.get("documents"), row["document_count"]),
        )):
            raise ValueError("report does not match its import record")
    except (InvalidArgument, ValueError) as exc:
        raise StorageError(
            "stored legacy import report is inconsistent or not strict JSON",
            details={"source_digest": source_digest},
        ) from exc
    return value


def insert_import_report(
    connection: sqlite3.Connection,
    source_digest: str,
    imported_at: str,
    report: dict,
) -> None:
    """Persist one completed import in ``legacy_imports`` on the caller's connection.

    The caller owns the transaction.  The report is serialised as canonical,
    sort-keyed strict JSON so a later read round-trips to an equal dictionary.
    """

    if not isinstance(source_digest, str) or source_digest == "":
        raise ValueError("source_digest must be a non-empty string")
    if not isinstance(imported_at, str) or imported_at == "":
        raise ValueError("imported_at must be a non-empty string")
    if not isinstance(report, dict):
        raise ValueError("report must be a dictionary")
    counts = report.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("report must contain a counts mapping")
    object_count = counts.get("objects")
    document_count = counts.get("documents")
    if (
        isinstance(object_count, bool)
        or not isinstance(object_count, int)
        or object_count < 0
    ):
        raise ValueError("report counts.objects must be a non-negative integer")
    if (
        isinstance(document_count, bool)
        or not isinstance(document_count, int)
        or document_count < 0
    ):
        raise ValueError("report counts.documents must be a non-negative integer")
    try:
        payload = json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("report is not strict JSON") from exc
    connection.execute(
        "INSERT INTO legacy_imports "
        "(source_digest, imported_at, object_count, document_count, report_json) "
        "VALUES (?,?,?,?,?)",
        (source_digest, imported_at, object_count, document_count, payload),
    )


def verify_integrity(connection: sqlite3.Connection) -> None:
    """Raise :class:`StorageError` unless the database is structurally sound.

    The caller supplies a transaction connection.  Checks run in order:
    ``PRAGMA integrity_check`` must return ``ok``, ``PRAGMA foreign_key_check``
    must report no violations, and every entry must be reachable from its
    branch root with active entries hanging off active folder parents. Every
    retained entry, including soft-deleted entries, must have strict extension
    metadata and valid current revision state. Any object without an entry in
    any branch is treated as an orphan.
    """

    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise StorageError(
            "database integrity_check failed", details={"result": result}
        )
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise StorageError(
            "database foreign_key_check failed",
            details={"violations": len(violations)},
        )
    branch_rows = connection.execute(
        "SELECT workspace_id, id, root_object_id FROM branches"
    ).fetchall()
    if not branch_rows:
        raise StorageError("database has no branch to anchor its directory tree")
    covered: set[str] = set()
    for branch in branch_rows:
        workspace_id = branch["workspace_id"]
        branch_id = branch["id"]
        root_id = branch["root_object_id"]
        rows = connection.execute(
            "SELECT e.object_id, e.parent_id, e.deleted_at, o.kind, "
            "e.current_revision_id, e.metadata_json, r.id AS matched_revision_id "
            "FROM entries AS e JOIN objects AS o "
            "ON o.workspace_id = e.workspace_id AND o.id = e.object_id "
            "LEFT JOIN document_revisions AS r "
            "ON r.id = e.current_revision_id AND r.workspace_id = e.workspace_id "
            "AND r.object_id = e.object_id "
            "WHERE e.workspace_id = ? AND e.branch_id = ?",
            (workspace_id, branch_id),
        ).fetchall()
        by_id = {row["object_id"]: row for row in rows}
        root = by_id.get(root_id)
        if root is None or root["parent_id"] is not None:
            raise StorageError(
                "branch root entry is missing or has a parent",
                details={"workspace_id": workspace_id, "branch_id": branch_id},
            )
        if root["kind"] != "folder":
            raise StorageError(
                "branch root entry is not a folder",
                details={"workspace_id": workspace_id, "branch_id": branch_id},
            )
        children: dict[str | None, list[str]] = {}
        for row in rows:
            details = {"workspace_id": workspace_id, "branch_id": branch_id,
                       "object_id": row["object_id"]}
            if row["kind"] == "document" and row["matched_revision_id"] is None:
                raise StorageError("document entry has no valid current revision",
                                   details=details)
            if row["kind"] == "folder" and row["current_revision_id"] is not None:
                raise StorageError("folder entry must not carry a current revision",
                                   details=details)
            try:
                metadata = json.loads(row["metadata_json"])
                validate_metadata(metadata)
            except (InvalidArgument, TypeError, ValueError) as exc:
                raise StorageError("entry extension metadata is not a strict JSON object",
                                   details=details) from exc
            if row["parent_id"] is not None:
                parent = by_id.get(row["parent_id"])
                if parent is None:
                    raise StorageError(
                        "entry parent is missing",
                        details={"object_id": row["object_id"]},
                    )
                if row["deleted_at"] is None and (
                    parent["deleted_at"] is not None or parent["kind"] != "folder"
                ):
                    raise StorageError(
                        "active entry has a non-active folder parent",
                        details={
                            "object_id": row["object_id"],
                            "parent_id": row["parent_id"],
                        },
                    )
            children.setdefault(row["parent_id"], []).append(row["object_id"])
        seen: set[str] = set()
        stack = [root_id]
        while stack:
            current = stack.pop()
            if current in seen:
                raise StorageError(
                    "directory tree contains a cycle",
                    details={"object_id": current},
                )
            seen.add(current)
            stack.extend(children.get(current, ()))
        if seen != set(by_id):
            missing = sorted(set(by_id) - seen)
            raise StorageError(
                "entries are not reachable from the branch root",
                details={"count": len(missing), "object_ids": missing[:10]},
            )
        covered.update(by_id)
    objects = {
        row[0] for row in connection.execute("SELECT id FROM objects").fetchall()
    }
    orphans = sorted(objects - covered)
    if orphans:
        raise StorageError(
            "objects have no entry in any branch",
            details={"count": len(orphans), "object_ids": orphans[:10]},
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

    def soft_delete_entries(
        self, object_ids: Iterable[str], deleted_at: str
    ) -> int:
        """Soft delete distinct active entries and return the affected row count.

        Every touched row gets exactly one ``version`` bump and the same
        ``deleted_at`` / ``modified_at`` value; already deleted rows are never
        touched.  Ids are split into batches sized to the connection's current
        SQLite host-variable limit, so a large subtree cannot fail with "too
        many SQL variables" while still sharing one caller-owned transaction.
        """

        unique_ids = list(dict.fromkeys(object_ids))
        if not unique_ids:
            return 0
        batch_size = self._soft_delete_batch_size()
        total = 0
        for start in range(0, len(unique_ids), batch_size):
            batch = unique_ids[start : start + batch_size]
            placeholders = ", ".join("?" for _ in batch)
            sql = (
                "UPDATE entries SET version = version + 1, modified_at = ?, "
                "deleted_at = ? "
                "WHERE workspace_id = ? AND branch_id = ? AND deleted_at IS NULL "
                f"AND object_id IN ({placeholders})"
            )
            parameters: list[object] = [
                deleted_at,
                deleted_at,
                self._workspace_id,
                self._branch_id,
                *batch,
            ]
            total += self._connection.execute(sql, parameters).rowcount
        return total

    def _soft_delete_batch_size(self) -> int:
        """Return a safe id batch size for the connection's variable limit."""

        limit = None
        getlimit = getattr(self._connection, "getlimit", None)
        limit_constant = getattr(sqlite3, "SQLITE_LIMIT_VARIABLE_NUMBER", None)
        if getlimit is not None and limit_constant is not None:
            try:
                limit = getlimit(limit_constant)
            except (sqlite3.Error, TypeError, ValueError):  # pragma: no cover
                limit = None
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            limit = _DEFAULT_VARIABLE_LIMIT
        return max(1, limit - _SOFT_DELETE_FIXED_PARAMETERS)

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
