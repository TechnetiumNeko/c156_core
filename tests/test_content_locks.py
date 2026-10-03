"""Real transaction checks for structural authorization and persistent freezes."""
import json
from unittest.mock import patch
from src.core.errors import Conflict, Forbidden, Frozen, NotFound, Unauthenticated
from src.services import ContentService
from src.services.access import AccessService
from src.storage.audit_repository import AuditRepository
from tests.helpers import TempPathTestCase, seed_content_read_fixture, seed_test_actors, entry_state


class TestContentLocks(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.f = seed_content_read_fixture(self.temp_path())
        self.actors = seed_test_actors(self.f.database, self.f.workspace_id)
        self.scope = self.f.main_scope
        self.content = ContentService(self.f.database)
        self.access = AccessService(self.f.database)
        self.owner = self.actors['owner'].session_token
        self.editor = self.actors['editor'].session_token
        self.reader = self.actors['reader'].session_token

    def version(self):
        return self.content.describe_access(self.scope, self.f.products_id, session_token=self.owner).version

    def rule(self, object_id, user, action, effect):
        with self.f.database.transaction(write=True) as connection:
            connection.execute('INSERT OR REPLACE INTO access_rules VALUES (?,?,?,?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, object_id, 'user', user,
                 user, action, effect))

    def document(self, *, private=False):
        return self.content.create_document(self.scope, self.f.products_id, 'actor draft',
            visibility='private' if private else 'inherit', session_token=self.editor)

    def test_freeze_versions_audit_atomicity_and_normal_permission(self):
        doc = self.document()
        version = self.version()
        with patch.object(AuditRepository, 'append', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=version)
        self.assertEqual(self.version(), version)
        self.assertFalse(self.content.describe_access(self.scope, doc.id, session_token=self.editor).frozen)
        view = self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=version)
        self.assertEqual(view.version, version + 1)
        self.assertTrue(view.frozen)
        self.assertTrue(view.can_unfreeze)
        with self.assertRaises(Conflict):
            self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=version)
        repeated = self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=view.version)
        self.assertEqual(repeated.version, view.version)
        with self.assertRaises(Frozen):
            self.access.freeze_document(self.scope, doc.id, session_token=self.owner, expected_version=view.version)
        self.rule(doc.id, self.actors['editor'].user_id, 'edit', 'deny')
        with self.assertRaises(Forbidden):
            self.content.save_document(self.scope, doc.id, 'forbidden', expected_revision_id=doc.revision_id,
                session_token=self.editor)
        unlocked = self.access.unfreeze_document(self.scope, doc.id, session_token=self.editor, expected_version=view.version)
        self.assertEqual(unlocked.version, view.version + 1)
        self.assertFalse(unlocked.frozen)
        with self.assertRaises(Conflict):
            self.access.unfreeze_document(self.scope, doc.id, session_token=self.editor, expected_version=view.version)
        self.assertEqual(self.access.unfreeze_document(self.scope, doc.id, session_token=self.owner,
            expected_version=unlocked.version).version, unlocked.version)
        with self.f.database.transaction() as connection:
            audits = connection.execute("SELECT event_type,before_json,after_json FROM audit_events WHERE event_type LIKE 'access.%freeze_document' ORDER BY rowid").fetchall()
        self.assertEqual(len(audits), 2)
        self.assertEqual(json.loads(audits[0]['before_json']), {'locked_by': None, 'version': version})
        self.assertEqual(json.loads(audits[0]['after_json']),
            {'locked_by': self.actors['editor'].user_id, 'version': version + 1})
        self.assertEqual(json.loads(audits[1]['after_json']), {'locked_by': None, 'version': version + 2})

    def test_other_actor_lock_blocks_document_and_all_ancestor_changes_even_manager(self):
        doc = self.document(private=True)
        self.access.set_member_role(self.scope, self.actors['reader'].user_id, 'editor', session_token=self.owner,
            expected_version=self.version())
        preview = self.content.prepare_delete(self.scope, self.f.products_id, session_token=self.owner)
        self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=self.version())
        folder = self.content.get_node(self.scope, self.f.products_id, session_token=self.owner)
        # The ordinary editor cannot read the private child, but ancestor rename
        # must still inspect its lock using the complete, unfiltered subtree.
        with self.assertRaises(Frozen):
            self.content.rename_node(self.scope, folder.id, 'hidden lock', expected_version=folder.version,
                session_token=self.reader)
        token = self.owner
        calls = [
            lambda: self.content.rename_node(self.scope, doc.id, 'renamed', expected_version=doc.version, session_token=token),
            lambda: self.content.rename_node(self.scope, folder.id, 'renamed', expected_version=folder.version, session_token=token),
            lambda: self.content.move_node(self.scope, folder.id, self.f.chinese_folder_id, expected_version=folder.version, session_token=token),
            lambda: self.content.prepare_delete(self.scope, folder.id, session_token=token),
            lambda: self.content.delete_node(self.scope, folder.id, expected_version=preview.version, recursive=True,
                expected_subtree_token=preview.subtree_token, session_token=token),
        ]
        for call in calls:
            with self.assertRaises(Frozen) as caught:
                call()
            self.assertEqual(dict(caught.exception.details), {})
        self.access.remove_member(self.scope, self.actors['editor'].user_id, session_token=self.owner, expected_version=self.version())
        with self.assertRaises(Frozen):
            self.content.rename_node(self.scope, folder.id, 'renamed', expected_version=folder.version, session_token=self.owner)
        with self.assertRaises(NotFound):
            self.content.save_document(self.scope, doc.id, 'removed', expected_revision_id=doc.revision_id, session_token=self.editor)
        self.access.unfreeze_document(self.scope, doc.id, session_token=self.owner, expected_version=self.version())
        self.assertEqual(self.content.rename_node(self.scope, folder.id, 'renamed', expected_version=folder.version,
            session_token=self.owner).name, 'renamed')

    def test_creator_reader_with_explicit_edit_can_freeze_and_unfreeze(self):
        doc = self.document()
        self.access.set_member_role(self.scope, self.actors['editor'].user_id, 'reader', session_token=self.owner,
            expected_version=self.version())
        self.rule(doc.id, self.actors['editor'].user_id, 'edit', 'allow')
        view = self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=self.version())
        self.assertTrue(view.frozen)
        with self.assertRaises(Forbidden):
            self.access.freeze_document(self.scope, doc.id, session_token=self.reader, expected_version=view.version)
        self.access.unfreeze_document(self.scope, doc.id, session_token=self.editor, expected_version=view.version)

    def test_full_delete_private_descendant_denial_and_revocation(self):
        doc = self.document(private=True)
        # Give another actual member normal editor abilities, with no private access.
        self.access.set_member_role(self.scope, self.actors['reader'].user_id, 'editor', session_token=self.owner,
            expected_version=self.version())
        folder = self.content.get_node(self.scope, self.f.products_id, session_token=self.reader)
        preview = self.content.prepare_delete(self.scope, folder.id, session_token=self.owner)
        for call in (
            lambda: self.content.prepare_delete(self.scope, folder.id, session_token=self.reader),
            lambda: self.content.delete_node(self.scope, folder.id, expected_version=preview.version,
                recursive=True, expected_subtree_token=preview.subtree_token, session_token=self.reader),
        ):
            with self.assertRaises(Forbidden) as caught:
                call()
            self.assertEqual(dict(caught.exception.details), {})
        for item in preview.items:
            self.assertIsNone(entry_state(self.f.path, item.node.id)['deleted_at'])
        with self.assertRaises(NotFound):
            self.content.delete_node(self.scope, doc.id, expected_version=doc.version, session_token=self.reader)
        # Creator can preview, but a subsequent child delete denial invalidates authorization.
        preview = self.content.prepare_delete(self.scope, folder.id, session_token=self.editor)
        self.rule(doc.id, self.actors['editor'].user_id, 'delete', 'deny')
        with self.assertRaises(Forbidden):
            self.content.delete_node(self.scope, folder.id, expected_version=preview.version,
                recursive=True, expected_subtree_token=preview.subtree_token, session_token=self.editor)
        self.assertIsNone(entry_state(self.f.path, doc.id)['deleted_at'])

    def test_read_delete_without_other_actions_and_same_parent_rename(self):
        user = self.actors['reader'].user_id
        self.rule(self.f.products_id, user, 'delete', 'allow')
        for action in ('edit', 'create', 'review', 'publish', 'move'):
            self.rule(self.f.products_id, user, action, 'deny')
        self.rule(self.f.concretecream_id, user, 'rename', 'allow')
        doc = self.content.get_node(self.scope, self.f.concretecream_id, session_token=self.reader)
        self.content.move_node(self.scope, doc.id, self.f.products_id, expected_version=doc.version,
            name='same parent rename', session_token=self.reader)
        preview = self.content.prepare_delete(self.scope, self.f.products_id, session_token=self.reader)
        self.content.delete_node(self.scope, preview.object_id, expected_version=preview.version,
            recursive=True, expected_subtree_token=preview.subtree_token, session_token=self.reader)
        for item in preview.items:
            self.assertIsNotNone(entry_state(self.f.path, item.node.id)['deleted_at'])

    def test_private_move_keeps_records_and_new_inheritance_immediate(self):
        doc = self.document(private=True)
        user = self.actors['editor'].user_id
        self.rule(doc.id, user, 'review', 'allow')
        def records():
            with self.f.database.transaction() as connection:
                return tuple(tuple(connection.execute('SELECT * FROM '+table+' WHERE object_id=?', (doc.id,)).fetchall())
                    for table in ('content_privacy', 'content_ownership', 'access_rules'))
        before = records()
        self.rule(self.f.chinese_folder_id, user, 'edit', 'deny')
        moved = self.content.move_node(self.scope, doc.id, self.f.chinese_folder_id, expected_version=doc.version,
            session_token=self.owner)
        self.assertEqual(moved.id, doc.id)
        self.assertEqual(records(), before)
        view = self.content.describe_access(self.scope, doc.id, session_token=self.editor)
        self.assertNotIn('edit', view.actions)
        self.assertIn('review', view.actions)
        with self.assertRaises(Forbidden):
            self.content.save_document(self.scope, doc.id, 'denied', expected_revision_id=doc.revision_id,
                session_token=self.editor)

    def test_soft_deleted_locked_document_does_not_block_ancestor(self):
        doc = self.document()
        self.access.freeze_document(self.scope, doc.id, session_token=self.editor, expected_version=self.version())
        # A lock can outlive its active document; only active descendants constrain changes.
        self.content.delete_node(self.scope, doc.id, expected_version=doc.version, session_token=self.editor)
        folder = self.content.get_node(self.scope, self.f.products_id, session_token=self.owner)
        self.assertEqual(self.content.rename_node(self.scope, folder.id, 'active only', expected_version=folder.version,
            session_token=self.owner).name, 'active only')

    def test_structural_identity_is_required(self):
        node = self.content.get_node(self.scope, self.f.products_id, session_token=self.owner)
        for token in (None, '', 'invalid'):
            for call in (
                lambda: self.content.rename_node(self.scope, node.id, 'x', expected_version=node.version, session_token=token),
                lambda: self.content.move_node(self.scope, node.id, node.parent_id, expected_version=node.version, session_token=token),
                lambda: self.content.prepare_delete(self.scope, node.id, session_token=token),
                lambda: self.content.delete_node(self.scope, node.id, expected_version=node.version, session_token=token),
                lambda: self.access.freeze_document(self.scope, self.f.concretecream_id, expected_version=self.version(), session_token=token),
            ):
                with self.assertRaises(Unauthenticated):
                    call()
