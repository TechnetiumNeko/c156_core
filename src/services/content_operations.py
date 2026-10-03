"""Internal content algorithms over an injected transaction-owned Repository."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from ..core.errors import (
    AlreadyExists,
    Conflict,
    DirectoryNotEmpty,
    InvalidArgument,
    InvalidMove,
    NotDirectory,
    NotDocument,
    NotFound,
    PathOutsideRoot,
    ProtectedNode,
    StorageBusy,
    UnsupportedSchema,
)
from ..core.json_values import json_equal, thaw_json, validate_metadata
from ..core.models import (
    ContentScope,
    DeleteSnapshot,
    DocumentSnapshot,
    NodeSnapshot,
    TreeItem,
)
from ..core.paths import parse_path, validate_name
from ..storage.management import DEFAULT_TOP_LEVEL_NAMES
from ..storage.records import EntryRecord, RevisionRecord
from ..storage.repository import Repository

__all__ = ["ContentOperations"]


def _utc_now() -> str:
    """Return the single UTC timestamp used by one service operation."""

    return datetime.now(timezone.utc).isoformat()


def _reject_json_constant(token: str) -> None:
    """Reject ``NaN`` / ``Infinity`` / ``-Infinity`` while parsing stored JSON."""

    raise ValueError(f"invalid JSON constant: {token}")


class ContentOperations:
    """Content operations that never open or finish a transaction."""

    def __init__(self, repository: Repository, *, policy=None) -> None:
        self._repo = repository
        self._policy = policy

    def _repository(self, scope: ContentScope) -> Repository:
        if (scope.workspace_id, scope.branch_id) != (self._repo.workspace_id, self._repo.branch_id):
            raise PathOutsideRoot("scope does not match the content repository", details={"workspace_id": scope.workspace_id, "branch_id": scope.branch_id})
        return self._repo

    def get_node(self, scope: ContentScope, object_id: str) -> NodeSnapshot:
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_in_scope(repo, scope, object_id)
        return self._snapshot(repo, scope, entry)


    def get_path(self, scope: ContentScope, object_id: str) -> str:
        repo = self._repository(scope)
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
        repo = self._repository(scope)
        root_entry = self._require_scope_root(repo, scope)
        if parsed.absolute:
            current = root_entry
        else:
            start_id = scope.root_id if cwd_id is None else cwd_id
            current = self._resolve_cwd(repo, scope, start_id)
        self._authorize(current.object_id)
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
                    "Object not found",
                )
            self._authorize(child.object_id)
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
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_in_scope(repo, scope, folder_id)
        if entry.kind != "folder":
            raise NotDirectory(
                "list_children requires a folder",
                details={"object_id": folder_id},
            )
        return [
            self._snapshot(repo, scope, child)
            for child in repo.list_children(folder_id) if self._visible(child.object_id)
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
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_in_scope(repo, scope, folder_id)
        if entry.kind != "folder":
            raise NotDirectory(
                "list_tree requires a folder",
                details={"object_id": folder_id},
            )
        items: list[TreeItem] = []
        visited = {entry.object_id}
        pending = [(entry, 0)]
        while pending:
            record, depth = pending.pop()
            items.append(TreeItem(self._snapshot(repo, scope, record), depth))
            if max_depth is not None and depth >= max_depth:
                continue
            children = []
            for child in repo.list_children(record.object_id):
                if child.object_id not in visited and self._visible(child.object_id):
                    visited.add(child.object_id)
                    children.append((child, depth + 1))
            pending.extend(reversed(children))
        return items


    def get_metadata(self, scope: ContentScope, object_id: str) -> dict:
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_in_scope(repo, scope, object_id)
        return self._metadata(entry)


    def create_folder(
        self, scope: ContentScope, parent_id: str, name: str
    ) -> NodeSnapshot:
        """Create one folder under an active parent and touch that parent."""

        self._require_str(parent_id, "parent_id")
        validate_name(name)
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        parent = self._require_valid_entry(
            repo, scope, parent_id, require_folder=True
        )
        self._reject_active_sibling(repo, parent_id, name)
        object_id = str(uuid.uuid4())
        position = repo.next_position(parent_id)
        repo.insert_object(object_id, "folder", now)
        repo.insert_entry(
            self._new_entry(
                scope,
                object_id,
                "folder",
                parent_id,
                name,
                position,
                None,
                now,
            )
        )
        repo.touch_entries([parent.object_id], now)
        return self._snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def create_document(
        self,
        scope: ContentScope,
        parent_id: str,
        name: str,
        *,
        content: str = "",
    ) -> DocumentSnapshot:
        """Create a document and its first revision under an active parent."""

        self._require_str(parent_id, "parent_id")
        validate_name(name)
        self._require_str(content, "content")
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        parent = self._require_valid_entry(
            repo, scope, parent_id, require_folder=True
        )
        self._reject_active_sibling(repo, parent_id, name)
        object_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        position = repo.next_position(parent_id)
        repo.insert_object(object_id, "document", now)
        repo.insert_revision(
            RevisionRecord(
                id=revision_id,
                workspace_id=scope.workspace_id,
                object_id=object_id,
                parent_revision_id=None,
                content=content,
                created_at=now,
            )
        )
        repo.insert_entry(
            self._new_entry(
                scope,
                object_id,
                "document",
                parent_id,
                name,
                position,
                revision_id,
                now,
            )
        )
        repo.touch_entries([parent.object_id], now)
        return self._document_snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def read_document(
        self, scope: ContentScope, object_id: str
    ) -> DocumentSnapshot:
        """Return the current content and revision of one active document."""

        self._require_str(object_id, "object_id")
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        return self._document_snapshot(repo, scope, entry)


    def save_document(
        self,
        scope: ContentScope,
        object_id: str,
        content: str,
        *,
        expected_revision_id: str,
    ) -> DocumentSnapshot:
        """Append an immutable revision after checking the caller's base.

        The expected revision is compared before the same-content no-op and
        before any new revision is appended, so a stale writer always gets a
        :class:`Conflict` even when it submits the current bytes again.
        """

        self._require_str(object_id, "object_id")
        self._require_str(content, "content")
        self._require_str(expected_revision_id, "expected_revision_id")
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        self._require_document(entry)
        current_id = entry.current_revision_id
        if not isinstance(current_id, str) or current_id == "":
            raise UnsupportedSchema(
                "document has no current revision",
                details={"object_id": object_id},
            )
        if current_id != expected_revision_id:
            raise Conflict(
                "document revision does not match the expected base",
                details={
                    "object_id": object_id,
                    "expected_revision_id": expected_revision_id,
                    "current_revision_id": current_id,
                },
            )
        current = repo.get_revision(object_id, current_id)
        if current is None:
            raise UnsupportedSchema(
                "current document revision is missing",
                details={"object_id": object_id, "revision_id": current_id},
            )
        if content == current.content:
            return self._document_snapshot(repo, scope, entry)
        revision_id = str(uuid.uuid4())
        repo.insert_revision(
            RevisionRecord(
                id=revision_id,
                workspace_id=scope.workspace_id,
                object_id=object_id,
                parent_revision_id=current_id,
                content=content,
                created_at=now,
            )
        )
        affected = repo.update_entry(
            object_id,
            {
                "current_revision_id": revision_id,
                "version": entry.version + 1,
                "modified_at": now,
            },
            expected_version=entry.version,
            expected_revision_id=current_id,
        )
        if affected != 1:
            raise Conflict(
                "document changed while saving",
                details={"object_id": object_id},
            )
        return self._document_snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def set_metadata(
        self,
        scope: ContentScope,
        object_id: str,
        changes: dict,
        *,
        expected_version: int,
    ) -> NodeSnapshot:
        """Merge extension keys into one entry after checking its version.

        Only extension keys are accepted; structural fields are rejected before
        the transaction.  The expected version is checked before the no-op
        comparison, so a stale version always conflicts even for equal values.
        """

        self._require_str(object_id, "object_id")
        self._require_positive_version(expected_version)
        validate_metadata(changes)
        # Validate encoding and detach caller-owned containers; the application
        # boundary may also prepare these values before opening its transaction.
        try:
            changes = json.loads(json.dumps(
                thaw_json(changes), ensure_ascii=False, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            ).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise InvalidArgument("metadata is not JSON serialisable") from exc
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        if entry.version != expected_version:
            raise Conflict(
                "entry version does not match the expected version",
                details={
                    "object_id": object_id,
                    "expected_version": expected_version,
                    "current_version": entry.version,
                },
            )
        current = self._metadata(entry)
        merged = thaw_json(current)
        for key, value in changes.items():
            merged[key] = thaw_json(value)
        if json_equal(merged, current):
            return self._snapshot(repo, scope, entry)
        try:
            payload = json.dumps(
                merged,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidArgument(
                "metadata is not JSON serialisable",
                details={"object_id": object_id},
            ) from exc
        affected = repo.update_entry(
            object_id,
            {
                "metadata_json": payload,
                "version": entry.version + 1,
                "modified_at": now,
            },
            expected_version=entry.version,
        )
        if affected != 1:
            raise Conflict(
                "entry changed while updating metadata",
                details={"object_id": object_id},
            )
        return self._snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def rename_node(
        self,
        scope: ContentScope,
        object_id: str,
        name: str,
        *,
        expected_version: int,
    ) -> NodeSnapshot:
        """Rename one active node, preserving identity, position and body.

        Scope, active type, expected version, protected identity and the target
        sibling name are checked in one ``BEGIN IMMEDIATE`` transaction before
        the no-op decision, so a stale or protected rename always fails even
        when the requested name equals the current one.
        """

        self._require_str(object_id, "object_id")
        validate_name(name)
        self._require_positive_version(expected_version)
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        if entry.version != expected_version:
            raise Conflict(
                "entry version does not match the expected version",
                details={
                    "object_id": object_id,
                    "expected_version": expected_version,
                    "current_version": entry.version,
                },
            )
        self._require_unprotected(repo, entry)
        if entry.name == name:
            return self._snapshot(repo, scope, entry)
        parent_id = entry.parent_id
        if parent_id is None:  # pragma: no cover - branch root is protected
            raise ProtectedNode(
                "branch root cannot be renamed",
                details={"object_id": object_id},
            )
        self._reject_other_active_sibling(repo, parent_id, name, object_id)
        affected = repo.update_entry(
            object_id,
            {"name": name, "version": entry.version + 1, "modified_at": now},
            expected_version=entry.version,
        )
        if affected != 1:
            raise Conflict(
                "entry changed while renaming",
                details={"object_id": object_id},
            )
        repo.touch_entries([parent_id], now)
        return self._snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def move_node(
        self,
        scope: ContentScope,
        object_id: str,
        parent_id: str,
        *,
        expected_version: int,
        name: str | None = None,
    ) -> NodeSnapshot:
        """Move one active node, optionally renaming it, in one transaction.

        A move to a different parent appends the node to that parent.  A move
        within the same parent that only changes the name behaves like a rename
        and keeps the position.  A move to the same parent with the same name is
        a true no-op after all scope, version, protection and cycle checks.
        """

        self._require_str(object_id, "object_id")
        self._require_str(parent_id, "parent_id")
        self._require_positive_version(expected_version)
        if name is not None:
            validate_name(name)
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        self._require_valid_entry(
            repo, scope, parent_id, require_folder=True
        )
        if entry.version != expected_version:
            raise Conflict(
                "entry version does not match the expected version",
                details={
                    "object_id": object_id,
                    "expected_version": expected_version,
                    "current_version": entry.version,
                },
            )
        self._require_unprotected(repo, entry)
        for record in repo.ancestors(parent_id):
            if record.object_id == object_id:
                raise InvalidMove(
                    "cannot move a node into itself or a descendant",
                    details={"object_id": object_id, "parent_id": parent_id},
                )
        new_name = entry.name if name is None else name
        if parent_id == entry.parent_id and new_name == entry.name:
            return self._snapshot(repo, scope, entry)
        self._reject_other_active_sibling(
            repo, parent_id, new_name, object_id
        )
        changes: dict = {
            "name": new_name,
            "version": entry.version + 1,
            "modified_at": now,
        }
        parents = [entry.parent_id]
        if parent_id != entry.parent_id:
            changes["parent_id"] = parent_id
            changes["position"] = repo.next_position(parent_id)
            parents.append(parent_id)
        affected = repo.update_entry(
            object_id, changes, expected_version=entry.version
        )
        if affected != 1:
            raise Conflict(
                "entry changed while moving",
                details={"object_id": object_id},
            )
        repo.touch_entries(
            [parent for parent in parents if parent is not None], now
        )
        return self._snapshot(
            repo, scope, self._require_created(repo, object_id)
        )


    def prepare_delete(
        self, scope: ContentScope, folder_id: str
    ) -> DeleteSnapshot:
        """Return an immutable snapshot of a folder's active subtree.

        One read transaction checks scope, folder type and protection, then
        returns the pre-order active subtree (start included) and a canonical
        token over the scope, target id and sorted ``(object_id, version)``
        pairs.  The token is a concurrency credential only; scope and
        protection checks run again inside the delete transaction.
        """

        self._require_str(folder_id, "folder_id")
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(
            repo, scope, folder_id, require_folder=True
        )
        self._require_unprotected(repo, entry)
        records = repo.subtree(folder_id)
        for record in records:
            self._authorize(record.object_id, scope=scope)
        if not records or records[0].object_id != folder_id:
            raise UnsupportedSchema(
                "active folder has no active subtree",
                details={"object_id": folder_id},
            )
        return DeleteSnapshot(
            object_id=folder_id,
            version=entry.version,
            items=self._subtree_items(repo, scope, records),
            subtree_token=self._subtree_token(scope, folder_id, records),
        )


    def delete_node(
        self,
        scope: ContentScope,
        object_id: str,
        *,
        expected_version: int,
        recursive: bool = False,
        expected_subtree_token: str | None = None,
    ) -> None:
        """Soft delete one node, or one folder's whole active subtree.

        A non-recursive delete accepts no token and refuses a folder that still
        has active children.  A recursive delete requires a non-empty token and
        a folder target, then re-reads the active subtree inside one
        ``BEGIN IMMEDIATE`` transaction and compares both the target version and
        the freshly computed token before deleting.  A stale token is never
        refreshed or retried: it raises :class:`Conflict` and the transaction
        rolls back with no partial deletion.
        """

        self._require_str(object_id, "object_id")
        self._require_positive_version(expected_version)
        if not isinstance(recursive, bool):
            raise InvalidArgument(
                "recursive must be a boolean",
                details={"recursive": recursive},
            )
        if recursive:
            if (
                not isinstance(expected_subtree_token, str)
                or expected_subtree_token == ""
            ):
                raise InvalidArgument(
                    "recursive delete requires a non-empty expected_subtree_token",
                    details={"object_id": object_id},
                )
        elif expected_subtree_token is not None:
            raise InvalidArgument(
                "non-recursive delete does not accept an expected_subtree_token",
                details={"object_id": object_id},
            )
        now = _utc_now()
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        entry = self._require_valid_entry(repo, scope, object_id)
        if entry.version != expected_version:
            raise Conflict(
                "entry version does not match the expected version",
                details={
                    "object_id": object_id,
                    "expected_version": expected_version,
                    "current_version": entry.version,
                },
            )
        self._require_unprotected(repo, entry)
        if recursive:
            if entry.kind != "folder":
                raise InvalidArgument(
                    "recursive delete requires a folder",
                    details={"object_id": object_id, "kind": entry.kind},
                )
            self._delete_subtree(
                repo, scope, entry, expected_subtree_token, now
            )
        else:
            self._delete_single(repo, entry, now)


    def _delete_single(self, repo: Repository, entry: EntryRecord, now: str) -> None:
        """Soft delete one active node and bump its external parent once."""

        if entry.kind == "folder" and repo.list_children(entry.object_id):
            raise DirectoryNotEmpty(
                "folder still has active children",
                details={"object_id": entry.object_id},
            )
        affected = repo.soft_delete_entries({entry.object_id}, now)
        if affected != 1:
            raise Conflict(
                "entry changed while deleting",
                details={"object_id": entry.object_id},
            )
        if entry.parent_id is not None:
            repo.touch_entries([entry.parent_id], now)


    def _delete_subtree(
        self,
        repo: Repository,
        scope: ContentScope,
        entry: EntryRecord,
        expected_subtree_token: str,
        now: str,
    ) -> None:
        """Delete an active subtree after re-checking its canonical token.

        Internal nodes are only touched by ``soft_delete_entries`` so each gets
        exactly one version bump; the external parent is touched once.  A
        mismatch between the active row count and the prepared subtree rolls
        the whole transaction back.
        """

        records = repo.subtree(entry.object_id)
        if not records or records[0].object_id != entry.object_id:
            raise Conflict(
                "subtree changed while deleting",
                details={"object_id": entry.object_id},
            )
        if self._subtree_token(scope, entry.object_id, records) != expected_subtree_token:
            raise Conflict(
                "subtree token does not match the prepared snapshot",
                details={"object_id": entry.object_id},
            )
        active_ids = {record.object_id for record in records}
        affected = repo.soft_delete_entries(active_ids, now)
        if affected != len(active_ids):
            raise Conflict(
                "subtree changed while deleting",
                details={
                    "object_id": entry.object_id,
                    "expected": len(active_ids),
                    "affected": affected,
                },
            )
        if entry.parent_id is not None:
            repo.touch_entries([entry.parent_id], now)


    def _subtree_items(
        self,
        repo: Repository,
        scope: ContentScope,
        records: list[EntryRecord],
    ) -> tuple[TreeItem, ...]:
        """Build pre-order tree items with depths from the flat subtree list."""

        by_id = {record.object_id: record for record in records}
        items: list[TreeItem] = []
        for record in records:
            depth = 0
            seen = {record.object_id}
            parent_id = record.parent_id
            while parent_id is not None and parent_id in by_id:
                if parent_id in seen:  # pragma: no cover - damaged cyclic data
                    break
                seen.add(parent_id)
                depth += 1
                parent_id = by_id[parent_id].parent_id
            items.append(
                TreeItem(self._snapshot(repo, scope, record), depth)
            )
        return tuple(items)


    @staticmethod
    def _subtree_token(
        scope: ContentScope,
        object_id: str,
        records: list[EntryRecord],
    ) -> str:
        """Return the canonical SHA-256 token for one active subtree.

        The payload is the fixed array
        ``[workspace_id, branch_id, root_id, object_id, [[id, version], ...]]``
        with pairs sorted by the id's BINARY order, serialised with compact
        separators, non-ASCII preserved and no NaN.  The token is never stored.
        """

        pairs = [
            [record.object_id, record.version]
            for record in sorted(records, key=lambda item: item.object_id)
        ]
        canonical = json.dumps(
            [
                scope.workspace_id,
                scope.branch_id,
                scope.root_id,
                object_id,
                pairs,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


    @staticmethod
    def _require_str(value, label: str) -> None:
        if not isinstance(value, str):
            raise InvalidArgument(
                label + " must be a string",
                details={label + "_type": type(value).__name__},
            )


    @staticmethod
    def _require_positive_version(value) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidArgument(
                "expected_version must be a positive integer",
                details={"expected_version": value},
            )


    @staticmethod
    def _require_document(entry: EntryRecord) -> None:
        if entry.kind != "document":
            raise NotDocument(
                "operation requires a document",
                details={"object_id": entry.object_id},
            )


    @staticmethod
    def _new_entry(
        scope: ContentScope,
        object_id: str,
        kind: str,
        parent_id: str,
        name: str,
        position: int,
        revision_id: str | None,
        now: str,
    ) -> EntryRecord:
        return EntryRecord(
            workspace_id=scope.workspace_id,
            branch_id=scope.branch_id,
            object_id=object_id,
            kind=kind,
            parent_id=parent_id,
            name=name,
            position=position,
            version=1,
            current_revision_id=revision_id,
            metadata_json="{}",
            created_at=now,
            modified_at=now,
            deleted_at=None,
        )


    @staticmethod
    def _reject_active_sibling(repo: Repository, parent_id: str, name: str) -> None:
        if repo.find_child(parent_id, name) is not None:
            raise AlreadyExists(
                "an active sibling already uses this name",
                details={"parent_id": parent_id, "name": name},
            )


    @staticmethod
    def _reject_other_active_sibling(
        repo: Repository, parent_id: str | None, name: str, object_id: str
    ) -> None:
        """Reject a sibling name held by a different active object."""

        sibling = repo.find_child(parent_id, name)
        if sibling is not None and sibling.object_id != object_id:
            raise AlreadyExists(
                "an active sibling already uses this name",
                details={"parent_id": parent_id, "name": name},
            )


    @staticmethod
    def _protected_ids(repo: Repository) -> set[str]:
        """Return the branch root plus its fixed top-level folder identities.

        Protection follows object identity, not the name currently stored on an
        arbitrary node, so a folder called ``main`` somewhere else stays
        editable while the real ``/main`` and the other fixed folders do not.
        """

        branch_root = repo.get_branch_root_id()
        if branch_root is None:
            raise UnsupportedSchema(
                "branch has no root object",
                details={"branch_id": repo.branch_id},
            )
        protected = {branch_root}
        for name in DEFAULT_TOP_LEVEL_NAMES:
            child = repo.find_child(branch_root, name)
            if (
                child is not None
                and child.deleted_at is None
                and child.kind == "folder"
            ):
                protected.add(child.object_id)
        return protected


    def _require_unprotected(
        self, repo: Repository, entry: EntryRecord
    ) -> None:
        if entry.object_id in self._protected_ids(repo):
            raise ProtectedNode(
                "protected object cannot be renamed, moved or deleted",
                details={"object_id": entry.object_id},
            )


    @staticmethod
    def _require_created(repo: Repository, object_id: str) -> EntryRecord:
        entry = repo.get_entry(object_id)
        if entry is None:  # pragma: no cover - defensive, insert just succeeded
            raise UnsupportedSchema(
                "created entry is missing",
                details={"object_id": object_id},
            )
        return entry


    def _authorize(self, object_id, *, scope=None):
        if self._policy is None:
            return
        repo = self._repo
        chain = tuple(reversed(repo.ancestors(object_id)))
        root = repo.get_branch_root_id()
        if (not chain or chain[0].object_id != root or chain[0].parent_id is not None
                or any(r.deleted_at is not None for r in chain)
                or (scope is not None and not any(r.object_id == scope.root_id for r in chain))
                or not self._policy.can_read(chain)):
            raise NotFound("Object not found")

    def _visible(self, object_id):
        try:
            self._authorize(object_id)
            return True
        except NotFound:
            return False

    def _presentation_position(self, scope, entry):
        if self._policy is None:
            return entry.position
        if entry.object_id == scope.root_id:
            return 0
        visible = [r.object_id for r in self._repo.list_children(entry.parent_id)
                   if self._visible(r.object_id)]
        return visible.index(entry.object_id)

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

        self._authorize(object_id, scope=scope)
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
                    details={"object_id": object_id} if self._policy is not None else {
                        "object_id": object_id,
                        "ancestor_id": record.object_id,
                    },
                )
            if record.kind != "folder":
                raise NotDirectory(
                    "object has a non-folder ancestor",
                    details={"object_id": object_id} if self._policy is not None else {
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


    def _snapshot(
        self, repo: Repository, scope: ContentScope, entry: EntryRecord
    ) -> NodeSnapshot:
        entry = self._require_valid_entry(repo, scope, entry.object_id)
        return NodeSnapshot(
            id=entry.object_id,
            kind=entry.kind,
            name=entry.name,
            parent_id=None if self._policy is not None and entry.object_id == scope.root_id else entry.parent_id,
            position=self._presentation_position(scope, entry),
            version=entry.version,
            path=self._display_path(repo, scope, entry),
            created_at=entry.created_at,
            modified_at=entry.modified_at,
            deleted_at=entry.deleted_at,
            metadata=self._metadata(entry),
        )


    def _document_snapshot(
        self, repo: Repository, scope: ContentScope, entry: EntryRecord
    ) -> DocumentSnapshot:
        self._require_document(entry)
        node = self._snapshot(repo, scope, entry)
        revision_id = entry.current_revision_id
        if not isinstance(revision_id, str) or revision_id == "":
            raise UnsupportedSchema(
                "document has no current revision",
                details={"object_id": entry.object_id},
            )
        revision = repo.get_revision(entry.object_id, revision_id)
        if revision is None:
            raise UnsupportedSchema(
                "document revision is missing",
                details={
                    "object_id": entry.object_id,
                    "revision_id": revision_id,
                },
            )
        return DocumentSnapshot(
            id=node.id,
            kind=node.kind,
            name=node.name,
            parent_id=node.parent_id,
            position=node.position,
            version=node.version,
            path=node.path,
            created_at=node.created_at,
            modified_at=node.modified_at,
            deleted_at=node.deleted_at,
            metadata=node.metadata,
            content=revision.content,
            revision_id=revision.id,
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


    def default_scope(self) -> ContentScope:
        """Validate this default branch and return its main access root."""
        repo = self._repo
        root_id = repo.get_branch_root_id()
        if root_id is None:
            raise UnsupportedSchema("main branch has no root object", details={"workspace_id": repo.workspace_id, "branch_id": repo.branch_id})
        root = repo.get_entry(root_id)
        if root is None or root.deleted_at is not None or root.kind != "folder" or root.parent_id is not None or root.name != "":
            raise UnsupportedSchema("default workspace root is missing or invalid", details={"root_id": root_id})
        for name in DEFAULT_TOP_LEVEL_NAMES:
            child = repo.find_child(root_id, name)
            if child is None or child.deleted_at is not None or child.kind != "folder" or child.parent_id != root_id:
                raise UnsupportedSchema("protected top-level folder is missing or invalid", details={"name": name, "root_id": root_id})
        main = repo.find_child(root_id, "main")
        return ContentScope(repo.workspace_id, repo.branch_id, main.object_id)

    def get_entry(self, scope: ContentScope, object_id: str) -> EntryRecord:
        repo = self._repository(scope)
        self._require_scope_root(repo, scope)
        return self._require_valid_entry(repo, scope, object_id)

    def ancestor_chain(self, object_id: str) -> tuple[EntryRecord, ...]:
        """Return the complete validated real-root-to-target chain."""
        repo = self._repo
        root_id = repo.get_branch_root_id()
        scope = ContentScope(repo.workspace_id, repo.branch_id, root_id)
        self._require_valid_entry(repo, scope, object_id)
        return tuple(reversed(repo.ancestors(object_id)))

    def active_subtree(self, object_id: str) -> tuple[EntryRecord, ...]:
        """Return active records after validating their complete ancestry."""
        self.ancestor_chain(object_id)
        records = self._repo.subtree(object_id)
        for record in records:
            self.ancestor_chain(record.object_id)
        return tuple(records)
