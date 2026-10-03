"""Application content facade; every public read resolves explicit identity.

Write authorization is integrated in the following approved tasks.
"""
from __future__ import annotations

import json
from ..core.errors import InvalidArgument
from ..core.json_values import thaw_json, validate_metadata
from ..core.paths import parse_path, validate_name
from .content_operations import ContentOperations

from ..core.models import ContentScope, DeleteSnapshot, DocumentSnapshot, NodeSnapshot, TreeItem
from ..storage.database import Database
from .unit_of_work import ApplicationUnitOfWork

__all__ = ["ContentService"]

class ContentService:
    def __init__(self, database: Database, *, clock=None) -> None:
        self._uow = ApplicationUnitOfWork(database, clock=clock)

    _require_str = staticmethod(ContentOperations._require_str)
    _require_positive_version = staticmethod(ContentOperations._require_positive_version)

    def _read(self):
        return self._uow.transaction()

    def _write(self):
        return self._uow.transaction(write=True)

    def default_scope(self, *, session_token: str | None) -> ContentScope:
        with self._uow.transaction() as work:
            principal = work.resolve_principal(session_token)
            scope = work.default_scope()
            work.authorized_content(scope, principal).get_node(scope, scope.root_id)
            return scope

    def get_node(self, scope: ContentScope, object_id: str, *, session_token: str | None) -> NodeSnapshot:
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).get_node(scope, object_id)

    def get_path(self, scope: ContentScope, object_id: str, *, session_token: str | None) -> str:
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).get_path(scope, object_id)

    def resolve_path(self, scope: ContentScope, path: str, *, cwd_id: str | None=None, session_token: str | None) -> NodeSnapshot:
        parse_path(path)
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).resolve_path(scope, path, cwd_id=cwd_id)

    def list_children(self, scope: ContentScope, folder_id: str, *, session_token: str | None) -> list[NodeSnapshot]:
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).list_children(scope, folder_id)

    def list_tree(self, scope: ContentScope, folder_id: str, *, max_depth: int | None=None, session_token: str | None) -> list[TreeItem]:
        if max_depth is not None and (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
        ):
            raise InvalidArgument(
                "max_depth must be a non-negative integer or None",
                details={"max_depth": max_depth},
            )
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).list_tree(scope, folder_id, max_depth=max_depth)

    def get_metadata(self, scope: ContentScope, object_id: str, *, session_token: str | None) -> dict:
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).get_metadata(scope, object_id)

    def create_folder(self, scope: ContentScope, parent_id: str, name: str) -> NodeSnapshot:
        self._require_str(parent_id, "parent_id")
        validate_name(name)
        with self._uow.name_conflicts({"parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).create_folder(scope, parent_id, name)

    def create_document(self, scope: ContentScope, parent_id: str, name: str, *, content: str='') -> DocumentSnapshot:
        self._require_str(parent_id, "parent_id")
        validate_name(name)
        self._require_str(content, "content")
        with self._uow.name_conflicts({"parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).create_document(scope, parent_id, name, content=content)

    def read_document(self, scope: ContentScope, object_id: str, *, session_token: str | None) -> DocumentSnapshot:
        self._require_str(object_id, "object_id")
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).read_document(scope, object_id)

    def save_document(self, scope: ContentScope, object_id: str, content: str, *, expected_revision_id: str) -> DocumentSnapshot:
        self._require_str(object_id, "object_id")
        self._require_str(content, "content")
        self._require_str(expected_revision_id, "expected_revision_id")
        with self._write() as work:
            return work.content(scope).save_document(scope, object_id, content, expected_revision_id=expected_revision_id)

    def set_metadata(self, scope: ContentScope, object_id: str, changes: dict, *, expected_version: int) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        self._require_positive_version(expected_version)
        validate_metadata(changes)
        try:
            changes = json.loads(json.dumps(
                thaw_json(changes), ensure_ascii=False, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            ).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise InvalidArgument("metadata is not JSON serialisable") from exc
        with self._write() as work:
            return work.content(scope).set_metadata(scope, object_id, changes, expected_version=expected_version)

    def rename_node(self, scope: ContentScope, object_id: str, name: str, *, expected_version: int) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        validate_name(name)
        self._require_positive_version(expected_version)
        with self._uow.name_conflicts({"object_id": object_id, "name": name}):
            with self._write() as work:
                return work.content(scope).rename_node(scope, object_id, name, expected_version=expected_version)

    def move_node(self, scope: ContentScope, object_id: str, parent_id: str, *, expected_version: int, name: str | None=None) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        self._require_str(parent_id, "parent_id")
        self._require_positive_version(expected_version)
        if name is not None:
            validate_name(name)
        with self._uow.name_conflicts({"object_id": object_id, "parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).move_node(scope, object_id, parent_id, expected_version=expected_version, name=name)

    def prepare_delete(self, scope: ContentScope, folder_id: str, *, session_token: str | None) -> DeleteSnapshot:
        self._require_str(folder_id, "folder_id")
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).prepare_delete(scope, folder_id)

    def delete_node(self, scope: ContentScope, object_id: str, *, expected_version: int, recursive: bool=False, expected_subtree_token: str | None=None) -> None:
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
        with self._write() as work:
            return work.content(scope).delete_node(scope, object_id, expected_version=expected_version, recursive=recursive, expected_subtree_token=expected_subtree_token)

    @staticmethod
    def _access_view(operations, scope, object_id):
        from ..access.models import ContentAccessView
        from ..access.policy import ACTIONS
        policy = operations._policy
        entry = operations.get_entry(scope, object_id)
        chain = operations.ancestor_chain(object_id)
        member = policy.role is not None
        lock = policy.locks.get(object_id)
        own_lock = member and lock == policy.principal.user_id
        actions = []
        protected = object_id in operations._protected_ids(operations._repo)
        for action in ACTIONS:
            if not policy.decide(chain, action).allowed:
                continue
            if action == 'edit' and entry.kind != 'document':
                continue
            if action in ('rename', 'move', 'delete') and protected:
                continue
            if action == 'move' and not policy.management:
                continue
            if action in ('edit', 'rename', 'move', 'delete'):
                records = (entry,) if entry.kind == 'document' else operations._repo.subtree(object_id)
                if action == 'delete' and any(not policy.decide(
                        tuple(reversed(operations._repo.ancestors(r.object_id))), 'delete').allowed
                        for r in records):
                    continue
                if any(r.kind == 'document' and r.object_id in policy.locks
                       and (not member or policy.locks[r.object_id] != policy.principal.user_id)
                       for r in records):
                    continue
            actions.append(action)
        can_freeze = (entry.kind == 'document' and member
            and policy.decide(chain, 'edit').allowed
            and (policy.management or policy.ownership.get(object_id) == policy.principal.user_id)
            and (lock is None or own_lock))
        can_unfreeze = (entry.kind == 'document' and member
            and (policy.management or own_lock))
        return ContentAccessView(policy.version, tuple(actions),
            'private' if object_id in policy.privacy else 'inherit',
            lock is not None, can_freeze, can_unfreeze)

    def describe_access(self, scope, object_id, *, session_token):
        with self._read() as work:
            operations = work.authorized_content(scope, work.resolve_principal(session_token))
            return self._access_view(operations, scope, object_id)

    def get_node_with_access(self, scope, object_id, *, session_token):
        from .views import NodeAccessView
        with self._read() as work:
            operations = work.authorized_content(scope, work.resolve_principal(session_token))
            return NodeAccessView(operations.get_node(scope, object_id),
                self._access_view(operations, scope, object_id))

    def read_document_with_access(self, scope, object_id, *, session_token):
        from .views import DocumentAccessView
        with self._read() as work:
            operations = work.authorized_content(scope, work.resolve_principal(session_token))
            return DocumentAccessView(operations.read_document(scope, object_id),
                self._access_view(operations, scope, object_id))

    def list_children_with_access(self, scope, folder_id, *, session_token):
        from .views import NodeAccessView
        with self._read() as work:
            operations = work.authorized_content(scope, work.resolve_principal(session_token))
            return tuple(NodeAccessView(node, self._access_view(operations, scope, node.id))
                for node in operations.list_children(scope, folder_id))

    def bootstrap(self, *, session_token):
        from .views import BootstrapView, NodeAccessView
        from ..identity.models import SessionView, user_view
        from ..identity.tokens import token_digest
        from ..core.errors import NotFound
        with self._read() as work:
            principal = work.resolve_principal(session_token)
            initialized = bool(work.identity.list_users())
            if principal.user_id is None:
                return BootstrapView(initialized, None, None, None, None)
            session = work.identity.get_session(token_digest(session_token))
            session_view = SessionView(user_view(work.identity.get_user(principal.user_id)),
                session.csrf_token, session.expires_at)
            scope = work.default_scope()
            operations = work.authorized_content(scope, principal)
            try:
                root = NodeAccessView(operations.get_node(scope, scope.root_id),
                    self._access_view(operations, scope, scope.root_id))
            except NotFound:
                root = None
            return BootstrapView(initialized, session_view, operations._policy.role,
                operations._policy.version, root)
