"""Atomic immutable operation requests, current authorization, and restore semantics."""
from dataclasses import asdict, replace
from unittest.mock import patch

from src.access.models import AccessRule
from src.core.errors import Conflict, Forbidden, Frozen, NotFound, Unauthenticated
from src.core.models import ContentScope
from src.services.access import AccessService
from src.services.content import ContentService
from src.storage.operation_repository import OperationRepository
from src.storage.records import RevisionRecord
from src.storage.repository import Repository
from tests.helpers import TempPathTestCase, seed_content_read_fixture, seed_test_actors


class TestContentOperations(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.f = seed_content_read_fixture(self.temp_path())
        self.actors = seed_test_actors(self.f.database, self.f.workspace_id)
        self.service = ContentService(self.f.database)
        self.access = AccessService(self.f.database)
        self.scope = self.f.main_scope
        self.editor = self.actors['editor'].session_token
        self.owner = self.actors['owner'].session_token
        self.doc = self.service.create_document(self.scope, self.f.products_id, 'operations',
            content='first\n', session_token=self.editor)

    def version(self):
        return self.access.workspace_access(self.scope, session_token=self.owner).version

    def rule(self, action, effect):
        self.access.put_rule(self.scope, AccessRule(self.doc.id, 'user',
            self.actors['editor'].user_id, action, effect),
            expected_version=self.version(), session_token=self.owner)

    def save(self, content='second\n', key='save', **kwargs):
        return self.service.save_document_operation(kwargs.pop('scope', self.scope),
            kwargs.pop('object_id', self.doc.id), content,
            expected_revision_id=kwargs.pop('expected', self.doc.revision_id), operation_id=key,
            session_token=kwargs.pop('token', self.editor), **kwargs)

    def restore(self, source=None, expected=None, key='restore', token=None):
        return self.service.restore_document_revision(self.scope, self.doc.id,
            source or self.doc.revision_id, expected_revision_id=expected or self.doc.revision_id,
            operation_id=key, session_token=token or self.editor)

    def status(self, key='save', token=None):
        return self.service.get_operation_status(self.scope, self.doc.id, key,
            session_token=token or self.editor)

    def page(self):
        return self.service.list_document_revisions(self.scope, self.doc.id, session_token=self.owner)

    def current(self):
        return self.service.read_document(self.scope, self.doc.id, session_token=self.owner)

    def test_replay_one_revision_and_independent_current_head_without_body(self):
        first = self.save()
        self.assertEqual(self.save(), first)
        self.assertTrue(first.operation.changed)
        self.assertEqual(len(self.page().revisions), 2)
        later = self.service.save_document(self.scope, self.doc.id, 'third',
            expected_revision_id=first.current_revision_id, session_token=self.owner)
        confirmed = self.save()
        self.assertEqual(confirmed.operation, first.operation)
        self.assertEqual(confirmed.current_revision_id, later.revision_id)
        self.assertEqual(self.status(), confirmed)
        self.assertEqual(set(asdict(confirmed)), {'operation', 'current_revision_id'})
        self.assertEqual(set(asdict(confirmed.operation)), {'operation_id', 'operation_type',
            'result_revision_id', 'changed', 'created_at'})
        self.assertEqual(len(self.page().revisions), 3)

    def test_key_binds_exact_body_base_type_and_scope_and_actor(self):
        first = self.save()
        for kwargs in ({'content': 'second'}, {'content': 'second\r\n'},
                       {'expected': first.current_revision_id},
                       {'scope': ContentScope(self.scope.workspace_id, self.scope.branch_id, self.f.products_id)},
                       {'object_id': self.f.draft_id}):
            with self.assertRaises(Conflict):
                self.save(**kwargs)
        with self.assertRaises(Conflict):
            self.restore(key='save')
        # Same object/revision can be present in another branch, but the request cannot move there.
        dev = ContentScope(self.scope.workspace_id, self.f.dev_branch_id, self.f.root_id)
        with self.f.database.transaction(write=True) as connection:
            repo = Repository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            other = Repository(connection, workspace_id=dev.workspace_id, branch_id=dev.branch_id)
            other.insert_entry(replace(repo.get_entry(self.doc.id), branch_id=dev.branch_id, parent_id=dev.root_id))
        with self.assertRaises(Conflict):
            self.save(scope=dev)
        self.assertIsNone(self.status(token=self.owner))
        owned = self.save('owner', token=self.owner, expected=first.current_revision_id)
        self.assertNotEqual(first.operation.result_revision_id, owned.operation.result_revision_id)
        self.assertEqual(self.status(token=self.owner), owned)
        with self.assertRaises(Conflict):
            self.service.get_operation_status(self.scope, self.f.draft_id, 'save', session_token=self.editor)

    def test_no_change_receipts_and_stale_same_content_conflict(self):
        noop = self.save(self.doc.content)
        self.assertFalse(noop.operation.changed)
        self.assertEqual(noop.current_revision_id, self.doc.revision_id)
        self.assertEqual(self.status(), noop)
        unchanged_restore = self.restore()
        self.assertFalse(unchanged_restore.operation.changed)
        self.assertEqual(len(self.page().revisions), 1)
        saved = self.save('new', key='next')
        with self.assertRaises(Conflict):
            self.save('new', key='stale')
        with self.assertRaises(Conflict):
            self.restore(source=saved.current_revision_id, key='stale-restore')
        self.assertIsNone(self.status('stale'))
        self.assertIsNone(self.status('stale-restore'))

    def test_restore_appends_parent_and_source_preserves_current_properties(self):
        second = self.save()
        third = self.save('third', key='third', expected=second.current_revision_id)
        current = self.current()
        node = self.service.rename_node(self.scope, self.doc.id, 'renamed',
            expected_version=current.version, session_token=self.owner)
        node = self.service.move_node(self.scope, self.doc.id, self.f.chinese_folder_id,
            expected_version=node.version, session_token=self.owner)
        self.service.set_metadata(self.scope, self.doc.id, {'custom': 'retained'},
            expected_version=node.version, session_token=self.owner)
        self.rule('history_read', 'allow')
        before, access = self.current(), self.service.describe_access(self.scope, self.doc.id, session_token=self.editor)
        restored = self.restore(expected=third.current_revision_id)
        after = self.current()
        self.assertEqual(after.content, self.doc.content)
        self.assertEqual((after.name, after.path, after.parent_id, after.position, after.metadata),
            (before.name, before.path, before.parent_id, before.position, before.metadata))
        self.assertEqual(self.service.describe_access(self.scope, self.doc.id, session_token=self.editor), access)
        page = self.page()
        self.assertEqual(len(page.revisions), 4)
        self.assertEqual((page.revisions[0].parent_revision_id, page.revisions[0].restored_from_revision_id,
                          page.revisions[0].source_kind, page.revisions[0].actor_id),
            (third.current_revision_id, self.doc.revision_id, 'restore', self.actors['editor'].user_id))
        self.assertEqual(restored.current_revision_id, after.revision_id)
        self.assertEqual(self.restore(expected=third.current_revision_id), restored)

    def test_restore_sources_must_be_same_object_workspace_and_reachable(self):
        with self.f.database.transaction(write=True) as connection:
            repo = Repository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            repo.insert_revision(RevisionRecord('detached', self.scope.workspace_id, self.doc.id,
                None, 'detached', self.doc.created_at))
        for source in (self.f.draft_revision_id, self.f.foreign_revision_id, 'detached'):
            with self.assertRaises(NotFound):
                self.restore(source=source)
        self.assertIsNone(self.status('restore'))
        self.assertEqual(len(self.page().revisions), 1)

    def test_first_restore_requires_edit_history_and_unfrozen(self):
        self.rule('history_read', 'deny')
        with self.assertRaises(Forbidden):
            self.restore()
        self.rule('history_read', 'allow')
        self.rule('edit', 'deny')
        with self.assertRaises(Forbidden):
            self.restore()
        self.rule('edit', 'allow')
        self.access.freeze_document(self.scope, self.doc.id,
            expected_version=self.version(), session_token=self.owner)
        with self.assertRaises(Frozen):
            self.restore()
        with self.assertRaises(Frozen):
            self.save()
        self.assertIsNone(self.status('restore'))
        self.assertEqual(len(self.page().revisions), 1)

    def test_confirmation_ignores_later_edit_history_freeze_but_requires_read_identity(self):
        saved = self.save()
        restored = self.restore(expected=saved.current_revision_id)
        self.rule('edit', 'deny')
        self.rule('history_read', 'deny')
        self.access.freeze_document(self.scope, self.doc.id,
            expected_version=self.version(), session_token=self.owner)
        self.assertEqual(self.restore(expected=saved.current_revision_id), restored)
        self.assertEqual(self.save().operation, saved.operation)
        with self.assertRaises(Forbidden):
            self.save(key='first')
        self.rule('read', 'deny')
        for action in (self.save, self.status, lambda: self.restore(expected=saved.current_revision_id)):
            with self.assertRaises(NotFound):
                action()
        with self.assertRaises(Unauthenticated):
            self.service.get_operation_status(self.scope, self.doc.id, 'save', session_token=None)
        with self.assertRaises(Unauthenticated):
            self.save(token='invalid')

    def test_retained_confirmation_management_only_and_first_writes_always_rejected(self):
        for ancestor in (False, True):
            with self.subTest(ancestor=ancestor):
                saved = self.save(self.current().content, key='retained-' + str(ancestor), token=self.owner,
                    expected=self.current().revision_id)
                target = self.f.products_id if ancestor else self.doc.id
                with self.f.database.transaction(write=True) as connection:
                    connection.execute('UPDATE entries SET deleted_at=? WHERE object_id=?', ('deleted', target))
                self.assertEqual(self.status(saved.operation.operation_id, self.owner), saved)
                self.assertEqual(self.save(self.doc.content, key=saved.operation.operation_id,
                    token=self.owner), saved)
                for action in (lambda: self.save(key='new', token=self.owner),
                               lambda: self.restore(token=self.owner), self.status):
                    with self.assertRaises(NotFound):
                        action()
                with self.f.database.transaction(write=True) as connection:
                    connection.execute('UPDATE entries SET deleted_at=NULL WHERE object_id=?', (target,))

    def test_receipt_insertion_failure_rolls_back_body_pointer_and_receipt(self):
        before, revisions = self.current(), self.page()
        original = OperationRepository.insert
        def fail(repository, record):
            original(repository, record)
            raise RuntimeError('injected after receipt insertion')
        with patch.object(OperationRepository, 'insert', fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                self.save()
        self.assertEqual(self.current(), before)
        self.assertEqual(self.page(), revisions)
        self.assertIsNone(self.status())
        self.assertTrue(self.save().operation.changed)
