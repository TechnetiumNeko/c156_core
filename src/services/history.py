"""Transaction-local historical access; retained entries never enter active reads."""
from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from difflib import unified_diff
import json

from ..core.errors import Forbidden, InvalidArgument, NotDocument, NotFound, UnsupportedSchema
from ..storage.records import EntryRecord, RevisionRecord
from .views import (DeletedDocumentPage, DeletedDocumentSummary, RevisionDiff,
                    RevisionPage, RevisionSummary, RevisionView)


class HistoryOperations:
    def __init__(self, repository, policy, identity_repository):
        self._repo = repository
        self._policy = policy
        self._identity = identity_repository

    def _chain(self, scope, object_id):
        if (scope.workspace_id != self._repo.workspace_id
                or scope.branch_id != self._repo.branch_id):
            raise NotFound('Object not found')
        chain = tuple(reversed(self._repo.ancestors(object_id)))
        if (not chain or chain[0].object_id != self._repo.get_branch_root_id()
                or chain[0].parent_id is not None
                or not any(r.object_id == scope.root_id and r.kind == 'folder' for r in chain)
                or any(r.kind != 'folder' for r in chain[:-1])):
            raise NotFound('Object not found')
        return chain

    def require_document(self, scope, object_id) -> EntryRecord:
        self._require_id(object_id)
        chain = self._chain(scope, object_id)
        if any(r.deleted_at is not None for r in chain) and not self._policy.management:
            raise NotFound('Object not found')
        self._policy.require_action(chain, 'history_read')
        entry = chain[-1]
        if entry.kind != 'document':
            raise NotDocument('operation requires a document')
        return entry

    def _revisions(self, entry):
        """Validate the entire branch head chain, including beyond a requested page."""
        revision_id = entry.current_revision_id
        if not revision_id:
            raise UnsupportedSchema('document has no current revision')
        seen = set()
        revisions = []
        while revision_id is not None:
            if revision_id in seen:
                raise UnsupportedSchema('document revision chain contains a cycle')
            seen.add(revision_id)
            revision = self._repo.get_revision(entry.object_id, revision_id)
            if revision is None:
                raise UnsupportedSchema('document revision chain is incomplete')
            revisions.append(revision)
            revision_id = revision.parent_revision_id
        return revisions

    def read_revision(self, scope, object_id, revision_id) -> RevisionRecord:
        self._require_id(revision_id)
        entry = self.require_document(scope, object_id)
        for revision in self._revisions(entry):
            if revision.id == revision_id:
                return revision
        raise NotFound('Revision not found')

    def _summary(self, revision):
        actor = self._identity.get_user(revision.actor_id) if revision.actor_id else None
        return RevisionSummary(revision.id, revision.parent_revision_id,
            revision.actor_id, actor.display_name if actor else None,
            revision.source_kind, revision.restored_from_revision_id, revision.created_at)

    def read_document_revision(self, scope, object_id, revision_id):
        revision = self.read_revision(scope, object_id, revision_id)
        return RevisionView(**asdict(self._summary(revision)), content=revision.content)

    def list_document_revisions(self, scope, object_id, *, cursor=None, limit=50):
        self._require_limit(limit)
        entry = self.require_document(scope, object_id)
        revisions = self._revisions(entry)
        ids = [r.id for r in revisions]
        binding = self._binding('revisions', scope) + [object_id]
        head, offset = entry.current_revision_id, 0
        if cursor is not None:
            head, next_id = self._decode(cursor, binding, 2)
            if head not in ids or next_id not in ids:
                raise InvalidArgument('Invalid history cursor')
            offset = ids.index(next_id)
            if offset < ids.index(head):
                raise InvalidArgument('Invalid history cursor')
        page = revisions[offset:offset + limit]
        next_cursor = (self._encode(binding + [head, ids[offset + limit]])
                       if offset + limit < len(ids) else None)
        return RevisionPage(tuple(self._summary(r) for r in page), head, next_cursor)

    def compare_document_revisions(self, scope, object_id, from_revision_id, to_revision_id):
        before = self.read_revision(scope, object_id, from_revision_id)
        after = self.read_revision(scope, object_id, to_revision_id)
        # Explicit newline markers keep changes to final newlines readable.
        lines = unified_diff(before.content.splitlines(keepends=True),
            after.content.splitlines(keepends=True), fromfile=before.id, tofile=after.id)
        diff = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
                       for line in lines)
        return RevisionDiff(before.id, after.id, diff)

    def list_deleted_documents(self, scope, *, cursor=None, limit=50):
        self._require_limit(limit)
        if not self._policy.management:
            raise Forbidden('workspace administrator required')
        root_chain = self._chain(scope, scope.root_id)
        if root_chain[-1].kind != 'folder':
            raise NotFound('Object not found')
        binding = self._binding('deleted', scope)
        after = self._decode(cursor, binding, 1)[0] if cursor is not None else None
        documents = []
        for entry in self._repo.list_document_entries():
            try:
                chain = self._chain(scope, entry.object_id)
            except NotFound:
                continue
            if not any(r.deleted_at is not None for r in chain):
                continue
            root_index = next(i for i, r in enumerate(chain) if r.object_id == scope.root_id)
            path = '/' + '/'.join(r.name for r in chain[root_index + 1:])
            documents.append(DeletedDocumentSummary(entry.object_id, entry.name, path))
        ids = [d.object_id for d in documents]
        if after is not None and after not in ids:
            raise InvalidArgument('Invalid history cursor')
        offset = ids.index(after) + 1 if after is not None else 0
        page = documents[offset:offset + limit]
        next_cursor = (self._encode(binding + [page[-1].object_id])
                       if offset + limit < len(documents) else None)
        return DeletedDocumentPage(tuple(page), next_cursor)

    @staticmethod
    def _require_id(value):
        if not isinstance(value, str) or not value:
            raise InvalidArgument('identifier must be a non-empty string')

    @staticmethod
    def _require_limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InvalidArgument('limit must be an integer between 1 and 100')

    @staticmethod
    def _binding(kind, scope):
        return [kind, scope.workspace_id, scope.branch_id, scope.root_id]

    @staticmethod
    def _encode(payload):
        return base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()

    @staticmethod
    def _decode(cursor, binding, extra):
        try:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 8192:
                raise ValueError
            payload = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
            if (not isinstance(payload, list) or len(payload) != len(binding) + extra
                    or payload[:len(binding)] != binding
                    or any(not isinstance(v, str) or not v for v in payload)):
                raise ValueError
            return payload[len(binding):]
        except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError) as exc:
            raise InvalidArgument('Invalid history cursor') from exc
