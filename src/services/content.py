"""Application content facade with transaction-scoped identity and authorization."""
from __future__ import annotations

import json
from hashlib import sha256
from ..core.errors import InvalidArgument, Unauthenticated, Forbidden, Conflict, NotFound, UnsupportedSchema
from ..core.json_values import thaw_json, validate_metadata
from ..core.paths import parse_path, validate_name
from .content_operations import ContentOperations
from .views import RevisionPage, RevisionView, RevisionDiff, DeletedDocumentPage, OperationReceipt, OperationResult

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

    @staticmethod
    def _configured_scope(work):
        from ..core.errors import UnsupportedSchema
        try:
            return work.configured_scope()
        except UnsupportedSchema as exc:
            raise UnsupportedSchema(exc.message) from exc

    @staticmethod
    def _validate_public_default(work):
        from ..core.errors import UnsupportedSchema
        try:
            work.default_scope()
        except UnsupportedSchema as exc:
            # Sibling names and the real branch root are outside display scope.
            # The original diagnostic remains available as the local cause.
            raise UnsupportedSchema(exc.message) from exc

    def default_scope(self, *, session_token: str | None) -> ContentScope:
        with self._uow.transaction() as work:
            principal = work.resolve_principal(session_token)
            scope = self._configured_scope(work)
            work.authorized_content(scope, principal).get_node(scope, scope.root_id)
            self._validate_public_default(work)
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

    def create_folder(self, scope: ContentScope, parent_id: str, name: str, *, visibility: str='inherit', session_token: str | None) -> NodeSnapshot:
        return self._create_public(scope, parent_id, name, visibility, session_token, document=False)

    def create_document(self, scope: ContentScope, parent_id: str, name: str, *, content: str='', visibility: str='inherit', session_token: str | None) -> DocumentSnapshot:
        return self._create_public(scope, parent_id, name, visibility, session_token, document=True, content=content)

    def create_folder_with_access(self, scope, parent_id, name, *, visibility='inherit', session_token):
        return self._create_public(scope, parent_id, name, visibility, session_token, document=False, with_access=True)

    def create_document_with_access(self, scope, parent_id, name, *, content='', visibility='inherit', session_token):
        return self._create_public(scope, parent_id, name, visibility, session_token, document=True, content=content, with_access=True)

    def _create_public(self, scope, parent_id, name, visibility, token, *, document, content='', with_access=False):
        self._require_str(parent_id, "parent_id")
        validate_name(name)
        if document:
            self._require_str(content, "content")
        with self._uow.name_conflicts({}):
            with self._write() as work:
                return self._create(work, scope, parent_id, name, visibility, token,
                                    document=document, content=content, with_access=with_access)

    @staticmethod
    def _writer(work, scope, token):
        actor = work.resolve_principal(token)
        if actor.user_id is None:
            raise Unauthenticated('authentication required')
        return work.authorized_content(scope, actor), actor

    def _create(self, work, scope, parent_id, name, visibility, token, *, document, content='', with_access=False):
        from uuid import uuid4
        from ..storage.access_repository import OwnershipRecord, PrivacyRecord
        from ..storage.audit_repository import AuditEventRecord
        operations, actor = self._writer(work, scope, token)
        operations.require_write(scope, parent_id, 'create')
        if visibility not in ('inherit', 'private'):
            raise InvalidArgument('invalid visibility')
        if visibility == 'private' and operations._policy.role not in ('editor', 'admin', 'owner'):
            raise Forbidden('private creation requires editor role')
        node = (operations.create_document(scope, parent_id, name, content=content, actor_id=actor.user_id)
                if document else operations.create_folder(scope, parent_id, name))
        repo = work.access(scope)
        repo.insert_ownership(OwnershipRecord(scope.workspace_id, node.id, actor.user_id))
        version = repo.get_settings().version
        if visibility == 'private':
            repo.insert_privacy(PrivacyRecord(scope.workspace_id, scope.branch_id, node.id,
                actor.user_id, work.now.isoformat()))
            if not repo.update_settings(expected_version=version):
                raise Conflict('workspace authorization changed')
        state = {'visibility': visibility, 'creator_id': actor.user_id,
                 'private_owner_id': actor.user_id if visibility == 'private' else None,
                 'version': repo.get_settings().version}
        work.audit.append(AuditEventRecord(str(uuid4()), actor.user_id, scope.workspace_id,
            'content.create', 'object', node.id, None, json.dumps(state, sort_keys=True),
            work.now.isoformat()))
        refreshed = work.authorized_content(scope, actor)
        snapshot = (refreshed.read_document(scope, node.id) if document
                    else refreshed.get_node(scope, node.id))
        if with_access:
            from .views import NodeAccessView, DocumentAccessView
            view = DocumentAccessView if document else NodeAccessView
            return view(snapshot, self._access_view(refreshed, scope, node.id))
        return snapshot

    def read_document(self, scope: ContentScope, object_id: str, *, session_token: str | None) -> DocumentSnapshot:
        self._require_str(object_id, "object_id")
        with self._read() as work:
            return work.authorized_content(scope, work.resolve_principal(session_token)).read_document(scope, object_id)

    def list_document_revisions(self, scope: ContentScope, object_id: str, *,
                                cursor: str | None = None, limit: int = 50,
                                session_token: str | None) -> RevisionPage:
        with self._read() as work:
            return work.history(scope, work.resolve_principal(session_token)).list_document_revisions(
                scope, object_id, cursor=cursor, limit=limit)

    def read_document_revision(self, scope: ContentScope, object_id: str, revision_id: str,
                               *, session_token: str | None) -> RevisionView:
        with self._read() as work:
            return work.history(scope, work.resolve_principal(session_token)).read_document_revision(
                scope, object_id, revision_id)

    def compare_document_revisions(self, scope: ContentScope, object_id: str,
                                   from_revision_id: str, to_revision_id: str,
                                   *, session_token: str | None) -> RevisionDiff:
        with self._read() as work:
            return work.history(scope, work.resolve_principal(session_token)).compare_document_revisions(
                scope, object_id, from_revision_id, to_revision_id)

    def list_deleted_documents(self, scope: ContentScope, *, cursor: str | None = None,
                               limit: int = 50, session_token: str | None) -> DeletedDocumentPage:
        with self._read() as work:
            return work.history(scope, work.resolve_principal(session_token)).list_deleted_documents(
                scope, cursor=cursor, limit=limit)

    def save_document(self, scope: ContentScope, object_id: str, content: str, *, expected_revision_id: str, session_token: str | None) -> DocumentSnapshot:
        return self._save_public(scope, object_id, content, expected_revision_id, session_token)

    def save_document_with_access(self, scope, object_id, content, *, expected_revision_id, session_token):
        return self._save_public(scope, object_id, content, expected_revision_id, session_token, with_access=True)

    def _save_public(self, scope, object_id, content, expected_revision_id, token, *, with_access=False):
        self._require_str(object_id, "object_id")
        self._require_str(content, "content")
        self._require_str(expected_revision_id, "expected_revision_id")
        with self._write() as work:
            operations, actor = self._writer(work, scope, token)
            operations.require_write(scope, object_id, 'edit', unfrozen=True)
            snapshot = operations.save_document(scope, object_id, content, expected_revision_id=expected_revision_id, actor_id=actor.user_id)
            if with_access:
                from .views import DocumentAccessView
                return DocumentAccessView(snapshot, self._access_view(operations, scope, object_id))
            return snapshot

    @staticmethod
    def _operation_document(work, scope, object_id, operations, actor):
        # Active confirmation needs read only; retained confirmation is H07 management-only.
        try:
            entry = operations.get_entry(scope, object_id)
            operations._require_document(entry)
        except NotFound:
            if not operations._policy.management:
                raise
            entry = work.history(scope, actor).require_document(scope, object_id)
        if not entry.current_revision_id:
            raise UnsupportedSchema('document has no current revision')
        return entry

    @staticmethod
    def _operation_result(record, current_revision_id):
        return OperationResult(OperationReceipt(record.operation_id, record.operation_type,
            record.result_revision_id, record.changed, record.created_at), current_revision_id)

    def save_document_operation(self, scope: ContentScope, object_id: str, content: str, *,
                                expected_revision_id: str, operation_id: str,
                                session_token: str | None) -> OperationResult:
        self._require_str(content, 'content')
        return self._document_operation(scope, object_id, 'save', content,
            expected_revision_id, operation_id, session_token)

    def restore_document_revision(self, scope: ContentScope, object_id: str,
                                  source_revision_id: str, *, expected_revision_id: str,
                                  operation_id: str, session_token: str | None) -> OperationResult:
        self._require_str(source_revision_id, 'source_revision_id')
        if not source_revision_id:
            raise InvalidArgument('source_revision_id must not be empty')
        return self._document_operation(scope, object_id, 'restore', source_revision_id,
            expected_revision_id, operation_id, session_token)

    def _document_operation(self, scope, object_id, kind, payload, expected, operation_id, token):
        from ..storage.operation_repository import OperationRecord
        for name, value in (('object_id', object_id), ('expected_revision_id', expected),
                            ('operation_id', operation_id)):
            self._require_str(value, name)
            if not value:
                raise InvalidArgument(name + ' must not be empty')
        # JSON escapes preserve the exact string (including newlines and Unicode form).
        digest = sha256(json.dumps([scope.workspace_id, scope.branch_id, scope.root_id,
            object_id, kind, expected, payload], ensure_ascii=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        with self._write() as work:
            operations, actor = self._writer(work, scope, token)
            entry = self._operation_document(work, scope, object_id, operations, actor)
            previous = work.operations.get(actor.user_id, operation_id)
            if previous is not None:
                if previous.request_digest != digest:
                    raise Conflict('operation_id is already bound to another request')
                return self._operation_result(previous, entry.current_revision_id)
            operations.require_write(scope, object_id, 'edit', unfrozen=True)
            content = payload
            if kind == 'restore':
                content = work.history(scope, actor).read_revision(scope, object_id, payload).content
            snapshot = operations.save_document(scope, object_id, content,
                expected_revision_id=expected, actor_id=actor.user_id, source_kind=kind,
                restored_from_revision_id=payload if kind == 'restore' else None)
            record = OperationRecord(actor.user_id, operation_id, scope.workspace_id,
                scope.branch_id, object_id, kind, digest, snapshot.revision_id,
                snapshot.revision_id != entry.current_revision_id, work.now.isoformat())
            work.operations.insert(record)
            return self._operation_result(record, snapshot.revision_id)

    def get_operation_status(self, scope: ContentScope, object_id: str, operation_id: str, *,
                             session_token: str | None) -> OperationResult | None:
        for name, value in (('object_id', object_id), ('operation_id', operation_id)):
            self._require_str(value, name)
            if not value:
                raise InvalidArgument(name + ' must not be empty')
        with self._read() as work:
            operations, actor = self._writer(work, scope, session_token)
            entry = self._operation_document(work, scope, object_id, operations, actor)
            record = work.operations.get(actor.user_id, operation_id)
            if record is None:
                return None
            if (record.workspace_id, record.branch_id, record.object_id) != (
                    scope.workspace_id, scope.branch_id, object_id):
                raise Conflict('operation_id is already bound to another resource')
            return self._operation_result(record, entry.current_revision_id)

    def set_metadata(self, scope: ContentScope, object_id: str, changes: dict, *, expected_version: int, session_token: str | None) -> NodeSnapshot:
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
            operations, _ = self._writer(work, scope, session_token)
            operations.require_write(scope, object_id, 'edit', unfrozen=True)
            return operations.set_metadata(scope, object_id, changes, expected_version=expected_version)

    def rename_node(self, scope: ContentScope, object_id: str, name: str, *, expected_version: int, session_token: str | None) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        validate_name(name)
        self._require_positive_version(expected_version)
        with self._uow.name_conflicts({}):
            with self._write() as work:
                operations, actor = self._writer(work, scope, session_token)
                operations.require_structure(scope, object_id, 'rename')
                operations.rename_node(scope, object_id, name, expected_version=expected_version)
                return work.authorized_content(scope, actor).get_node(scope, object_id)

    def move_node(self, scope: ContentScope, object_id: str, parent_id: str, *, expected_version: int, name: str | None=None, session_token: str | None) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        self._require_str(parent_id, "parent_id")
        self._require_positive_version(expected_version)
        if name is not None:
            validate_name(name)
        with self._uow.name_conflicts({}):
            with self._write() as work:
                operations, actor = self._writer(work, scope, session_token)
                entry = operations.get_entry(scope, object_id)
                if entry.parent_id == parent_id:
                    operations.require_structure(scope, object_id, 'rename')
                else:
                    operations.require_structure(scope, object_id, 'move')
                    operations.require_write(scope, parent_id, 'create')
                    if not operations._policy.management:
                        raise Forbidden('workspace administrator required')
                operations.move_node(scope, object_id, parent_id, expected_version=expected_version, name=name)
                return work.authorized_content(scope, actor).get_node(scope, object_id)

    def prepare_delete(self, scope: ContentScope, folder_id: str, *, session_token: str | None) -> DeleteSnapshot:
        self._require_str(folder_id, "folder_id")
        with self._read() as work:
            operations, _ = self._writer(work, scope, session_token)
            operations.qualify_delete(scope, folder_id, raw=work.content(scope))
            return operations.prepare_delete(scope, folder_id)

    def delete_node(self, scope: ContentScope, object_id: str, *, expected_version: int, recursive: bool=False, expected_subtree_token: str | None=None, session_token: str | None) -> None:
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
            operations, _ = self._writer(work, scope, session_token)
            operations.qualify_delete(scope, object_id, raw=work.content(scope))
            return operations.delete_node(scope, object_id, expected_version=expected_version, recursive=recursive, expected_subtree_token=expected_subtree_token)

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
        protected_ids = operations._protected_ids(operations._repo)
        protected = object_id in protected_ids
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
                if action == 'delete' and any(r.object_id in protected_ids for r in records):
                    continue
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
            scope = self._configured_scope(work)
            operations = work.authorized_content(scope, principal)
            try:
                node = operations.get_node(scope, scope.root_id)
                self._validate_public_default(work)
                root = NodeAccessView(node,
                    self._access_view(operations, scope, scope.root_id))
            except NotFound:
                root = None
            return BootstrapView(initialized, session_view, operations._policy.role,
                operations._policy.version, root, scope)
