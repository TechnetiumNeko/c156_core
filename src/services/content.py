"""Shared ContentService reads over the virtual filesystem.

The service owns scope authorization, active-ancestor checks, per-segment path
resolution and snapshot assembly.  Every public call opens exactly one read
transaction and passes that same connection to each repository method; the
service never opens a connection itself and contains no SQL.  Write operations
are intentionally absent from this task.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Iterator

from ..core.errors import (
    InvalidArgument,
    NotDirectory,
    NotFound,
    PathOutsideRoot,
    StorageBusy,
    UnsupportedSchema,
)
from ..core.json_values import validate_metadata
from ..core.models import ContentScope, NodeSnapshot, TreeItem
from ..core.paths import parse_path
from ..storage.database import Database
from ..storage.errors import BusyError, SchemaError
from ..storage.management import validate_default_tree
from ..storage.records import EntryRecord
from ..storage.repository import Repository

__all__ = ["ContentService"]


def _reject_json_constant(token: str) -> None:
    """Reject ``NaN`` / ``Infinity`` / ``-Infinity`` while parsing stored JSON."""

    raise ValueError(f"invalid JSON constant: {token}")


class ContentService:
    """Read-only virtual filesystem access for one lazy database handle."""

    def __init__(self, database: Database) -> None:
        self._database = database

    # -- access root --------------------------------------------------------

    def default_scope(self) -> ContentScope:
        """Return the CLI ``main`` access root for the default workspace."""

        with self._read() as connection:
            root_scope = validate_default_tree(connection)
            repo = self._repository(connection, root_scope)
            main = repo.find_child(root_scope.root_id, "main")
            if (
                main is None
                or main.deleted_at is not None
                or main.kind != "folder"
            ):
                raise UnsupportedSchema(
                    "default main folder is missing or invalid",
                    details={"root_id": root_scope.root_id},
                )
            return ContentScope(
                workspace_id=root_scope.workspace_id,
                branch_id=root_scope.branch_id,
                root_id=main.object_id,
            )

    # -- public reads -------------------------------------------------------

    def get_node(self, scope: ContentScope, object_id: str) -> NodeSnapshot:
        with self._read() as connection:
            repo = self._repository(connection, scope)
            self._require_scope_root(repo, scope)
            entry = self._require_in_scope(repo, scope, object_id)
            return self._snapshot(repo, scope, entry)

    def get_path(self, scope: ContentScope, object_id: str) -> str:
        with self._read() as connection:
            repo = self._repository(connection, scope)
            self._require_scope_root(repo, scope)
            entry = self._require_in_scope(repo, scope, object_id)
            return self._display_path(repo, scope, entry)

    def resolve_path(
        self,
        scope: ContentScope,
        path: str,
        *,
        cwd_id: str | None = None,
    ) -> NodeSnapshot:
        parsed = parse_path(path)
        with self._read() as connection:
            repo = self._repository(connection, scope)
            root_entry = self._require_scope_root(repo, scope)
            if parsed.absolute:
                current = root_entry
            else:
                start_id = scope.root_id if cwd_id is None else cwd_id
                current = self._resolve_cwd(repo, scope, start_id)
            for part in parsed.parts:
                if current.kind != "folder":
                    raise NotDirectory(
                        "path segment requires a folder",
                        details={"object_id": current.object_id, "segment": part},
                    )
                if part == ".":
                    continue
                if part == "..":
                    current = self._resolve_parent(repo, scope, current)
                    continue
                child = repo.find_child(current.object_id, part)
                if child is None:
                    raise NotFound(
                        "virtual path segment does not exist",
                        details={"parent_id": current.object_id, "segment": part},
                    )
                current = child
            if parsed.trailing_slash and current.kind != "folder":
                raise NotDirectory(
                    "trailing slash requires a folder",
                    details={"object_id": current.object_id},
                )
            return self._snapshot(repo, scope, current)

    def list_children(
        self, scope: ContentScope, folder_id: str
    ) -> list[NodeSnapshot]:
        with self._read() as connection:
            repo = self._repository(connection, scope)
            self._require_scope_root(repo, scope)
            entry = self._require_in_scope(repo, scope, folder_id)
            if entry.kind != "folder":
                raise NotDirectory(
                    "list_children requires a folder",
                    details={"object_id": folder_id},
                )
            return [
                self._snapshot(repo, scope, child)
                for child in repo.list_children(folder_id)
            ]

    def list_tree(
        self,
        scope: ContentScope,
        folder_id: str,
        *,
        max_depth: int | None = None,
    ) -> list[TreeItem]:
        if max_depth is not None and (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
        ):
            raise InvalidArgument(
                "max_depth must be a non-negative integer or None",
                details={"max_depth": max_depth},
            )
        with self._read() as connection:
            repo = self._repository(connection, scope)
            self._require_scope_root(repo, scope)
            entry = self._require_in_scope(repo, scope, folder_id)
            if entry.kind != "folder":
                raise NotDirectory(
                    "list_tree requires a folder",
                    details={"object_id": folder_id},
                )
            items: list[TreeItem] = []
            visited = {entry.object_id}

            def walk(record: EntryRecord, depth: int) -> None:
                items.append(TreeItem(self._snapshot(repo, scope, record), depth))
                if max_depth is not None and depth >= max_depth:
                    return
                for child in repo.list_children(record.object_id):
                    if child.object_id in visited:
                        continue
                    visited.add(child.object_id)
                    walk(child, depth + 1)

            walk(entry, 0)
            return items

    def get_metadata(self, scope: ContentScope, object_id: str) -> dict:
        with self._read() as connection:
            repo = self._repository(connection, scope)
            self._require_scope_root(repo, scope)
            entry = self._require_in_scope(repo, scope, object_id)
            return self._metadata(entry)

    # -- transaction and scope helpers -------------------------------------

    @contextmanager
    def _read(self) -> Iterator:
        try:
            with self._database.transaction() as connection:
                yield connection
        except BusyError as exc:
            raise StorageBusy(str(exc), details=dict(exc.details)) from exc
        except SchemaError as exc:
            raise UnsupportedSchema(str(exc), details=dict(exc.details)) from exc

    @staticmethod
    def _repository(connection, scope: ContentScope) -> Repository:
        return Repository(
            connection,
            workspace_id=scope.workspace_id,
            branch_id=scope.branch_id,
        )

    def _require_scope_root(
        self, repo: Repository, scope: ContentScope
    ) -> EntryRecord:
        return self._require_valid_entry(
            repo, scope, scope.root_id, require_folder=True
        )

    def _require_valid_entry(
        self,
        repo: Repository,
        scope: ContentScope,
        object_id: str,
        *,
        require_folder: bool = False,
    ) -> EntryRecord:
        """Return an active entry whose whole ancestor chain is authorizable.

        The target must exist, be active, be inside the access root and have a
        complete chain ending at the branch root.  Every ancestor must be an
        active folder: a deleted ancestor is ``NotFound`` and a non-folder
        ancestor is ``NotDirectory``.  Corrupt or cyclic chains terminate in a
        domain error instead of looping.
        """

        entry = repo.get_entry(object_id)
        if entry is None or entry.deleted_at is not None:
            raise NotFound(
                "object does not exist in scope",
                details={"object_id": object_id},
            )
        chain = repo.ancestors(object_id)
        if not chain:
            raise NotFound(
                "object has no ancestor chain",
                details={"object_id": object_id},
            )
        branch_root = repo.get_branch_root_id()
        top = chain[-1]
        if (
            branch_root is None
            or top.object_id != branch_root
            or top.parent_id is not None
        ):
            raise NotFound(
                "object chain is not anchored at the branch root",
                details={
                    "object_id": object_id,
                    "branch_root_id": branch_root,
                },
            )
        if object_id != scope.root_id and not any(
            record.object_id == scope.root_id for record in chain
        ):
            raise PathOutsideRoot(
                "object is outside the access root",
                details={"object_id": object_id, "root_id": scope.root_id},
            )
        if require_folder and entry.kind != "folder":
            raise NotDirectory(
                "object is not a folder",
                details={"object_id": object_id},
            )
        for record in chain[1:]:
            if record.deleted_at is not None:
                raise NotFound(
                    "object has a deleted ancestor",
                    details={
                        "object_id": object_id,
                        "ancestor_id": record.object_id,
                    },
                )
            if record.kind != "folder":
                raise NotDirectory(
                    "object has a non-folder ancestor",
                    details={
                        "object_id": object_id,
                        "ancestor_id": record.object_id,
                    },
                )
        return entry

    def _require_in_scope(
        self, repo: Repository, scope: ContentScope, object_id: str
    ) -> EntryRecord:
        return self._require_valid_entry(repo, scope, object_id)

    def _resolve_cwd(
        self, repo: Repository, scope: ContentScope, cwd_id: str
    ) -> EntryRecord:
        return self._require_valid_entry(
            repo, scope, cwd_id, require_folder=True
        )

    def _resolve_parent(
        self, repo: Repository, scope: ContentScope, current: EntryRecord
    ) -> EntryRecord:
        if current.object_id == scope.root_id:
            raise PathOutsideRoot(
                "cannot move above the access root",
                details={"root_id": scope.root_id},
            )
        parent_id = current.parent_id
        if parent_id is None:
            raise PathOutsideRoot(
                "cannot move above the access root",
                details={"object_id": current.object_id},
            )
        return self._require_valid_entry(
            repo, scope, parent_id, require_folder=True
        )

    # -- snapshot assembly --------------------------------------------------

    def _snapshot(
        self, repo: Repository, scope: ContentScope, entry: EntryRecord
    ) -> NodeSnapshot:
        entry = self._require_valid_entry(repo, scope, entry.object_id)
        return NodeSnapshot(
            id=entry.object_id,
            kind=entry.kind,
            name=entry.name,
            parent_id=entry.parent_id,
            position=entry.position,
            version=entry.version,
            path=self._display_path(repo, scope, entry),
            created_at=entry.created_at,
            modified_at=entry.modified_at,
            deleted_at=entry.deleted_at,
            metadata=self._metadata(entry),
        )

    def _display_path(
        self, repo: Repository, scope: ContentScope, entry: EntryRecord
    ) -> str:
        names: list[str] = []
        for record in repo.ancestors(entry.object_id):
            if record.object_id == scope.root_id:
                break
            names.append(record.name)
        else:
            raise PathOutsideRoot(
                "object is outside the access root",
                details={"object_id": entry.object_id, "root_id": scope.root_id},
            )
        return "/" + "/".join(reversed(names))

    @staticmethod
    def _metadata(entry: EntryRecord) -> dict:
        try:
            value = json.loads(
                entry.metadata_json, parse_constant=_reject_json_constant
            )
        except (TypeError, ValueError) as exc:
            raise UnsupportedSchema(
                "stored metadata is not valid JSON",
                details={"object_id": entry.object_id},
            ) from exc
        if not isinstance(value, dict):
            raise UnsupportedSchema(
                "stored metadata is not a JSON object",
                details={"object_id": entry.object_id},
            )
        try:
            validate_metadata(value)
        except InvalidArgument as exc:
            raise UnsupportedSchema(
                "stored metadata violates the extension metadata rules",
                details={"object_id": entry.object_id, "reason": exc.message},
            ) from exc
        return value
