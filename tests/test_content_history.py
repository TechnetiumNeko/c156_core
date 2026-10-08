"""History authorization and immutable provenance through real service transactions."""
import base64
import json
from dataclasses import asdict, replace
from contextlib import closing

from src.access.models import AccessRule
from src.core.errors import Forbidden, InvalidArgument, NotFound, UnsupportedSchema
from src.core.models import ContentScope
from src.services.access import AccessService
from src.services.content import ContentService
from src.storage.records import RevisionRecord
from src.storage.repository import Repository
from tests.helpers import TempPathTestCase, seed_content_read_fixture, seed_test_actors


class TestContentHistory(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.f = seed_content_read_fixture(self.temp_path())
        self.actors = seed_test_actors(self.f.database, self.f.workspace_id)
        self.service = ContentService(self.f.database)
        self.access = AccessService(self.f.database)
        self.scope = self.f.main_scope
        self.owner = self.actors['owner'].session_token
        self.editor = self.actors['editor'].session_token
        self.reader = self.actors['reader'].session_token
        self.doc = self.service.create_document(self.scope, self.f.products_id, 'history',
            content='first\n', session_token=self.editor)

    def version(self):
        return self.access.workspace_access(self.scope, session_token=self.owner).version

    def rule(self, object_id, action, effect, actor='reader', remove=False):
        method = self.access.remove_rule if remove else self.access.put_rule
        return method(self.scope, AccessRule(object_id, 'user', self.actors[actor].user_id,
            action, effect), expected_version=self.version(), session_token=self.owner)

    def page(self, **kwargs):
        return self.service.list_document_revisions(self.scope, self.doc.id,
            session_token=kwargs.pop('session_token', self.editor), **kwargs)

    def save(self, body):
        self.doc = self.service.save_document(self.scope, self.doc.id, body,
            expected_revision_id=self.doc.revision_id, session_token=self.editor)
        return self.doc.revision_id

    def test_history_independent_grant_revoke_and_current_membership(self):
        self.service.read_document(self.scope, self.doc.id, session_token=self.reader)
        with self.assertRaises(Forbidden):
            self.page(session_token=self.reader)
        self.assertEqual(len(self.page().revisions), 1)
        self.rule(self.doc.id, 'history_read', 'allow')
        self.assertEqual(self.page(session_token=self.reader).revisions[0].source_kind, 'save')
        self.rule(self.doc.id, 'history_read', 'allow', remove=True)
        with self.assertRaises(Forbidden):
            self.page(session_token=self.reader)
        self.rule(self.doc.id, 'history_read', 'deny', actor='editor')
        with self.assertRaises(Forbidden):
            self.page()
        self.rule(self.doc.id, 'history_read', 'deny', actor='editor', remove=True)
        self.access.set_read_scope(self.scope, 'everyone', expected_version=self.version(), session_token=self.owner)
        self.access.remove_member(self.scope, self.actors['editor'].user_id,
            expected_version=self.version(), session_token=self.owner)
        with self.assertRaises(Forbidden):
            self.page()

    def test_ancestor_read_private_and_scope_cannot_bypass_current_policy(self):
        self.rule(self.doc.id, 'history_read', 'allow')
        self.rule(self.f.products_id, 'read', 'deny')
        self.rule(self.doc.id, 'read', 'allow')
        deep = ContentScope(self.scope.workspace_id, self.scope.branch_id, self.f.products_id)
        with self.assertRaises(NotFound):
            self.service.read_document_revision(deep, self.doc.id, self.doc.revision_id, session_token=self.reader)
        self.rule(self.f.products_id, 'read', 'deny', remove=True)
        self.access.set_visibility(self.scope, self.f.products_id, 'private',
            expected_version=self.version(), session_token=self.owner)
        with self.assertRaises(NotFound):
            self.page(session_token=self.reader)
        self.assertEqual(self.page(session_token=self.owner).head_revision_id, self.doc.revision_id)

    def test_current_actor_name_create_save_unknown_and_summary_without_body(self):
        created = self.doc.revision_id
        saved = self.save('second\n')
        with self.f.database.transaction(write=True) as connection:
            connection.execute('UPDATE users SET display_name=? WHERE id=?',
                ('New display name', self.actors['editor'].user_id))
        page = self.page()
        self.assertEqual([r.source_kind for r in page.revisions], ['save', 'save'])
        self.assertEqual([r.actor_id for r in page.revisions], [self.actors['editor'].user_id] * 2)
        self.assertTrue(all(r.actor_display_name == 'New display name' for r in page.revisions))
        self.assertTrue(all('content' not in asdict(r) for r in page.revisions))
        view = self.service.read_document_revision(self.scope, self.doc.id, created, session_token=self.editor)
        self.assertEqual(view.content, 'first\n')
        diff = self.service.compare_document_revisions(self.scope, self.doc.id, created, saved, session_token=self.editor)
        self.assertIn('-first\n+second\n', diff.diff)
        unknown = self.service.read_document_revision(self.scope, self.f.draft_id,
            self.f.draft_revision_id, session_token=self.editor)
        self.assertEqual((unknown.source_kind, unknown.actor_id, unknown.actor_display_name), ('unknown', None, None))
        self.save('second\n')
        self.assertEqual(len(self.page().revisions), 2)

    def test_fixed_head_paging_rechecks_authorization(self):
        first = self.doc.revision_id
        second = self.save('second')
        third = self.save('third')
        page = self.page(limit=1)
        self.assertEqual([r.revision_id for r in page.revisions], [third])
        self.save('fourth')
        following = self.page(limit=1, cursor=page.next_cursor)
        self.assertEqual(following.head_revision_id, third)
        self.assertEqual([r.revision_id for r in following.revisions], [second])
        last = self.page(cursor=following.next_cursor)
        self.assertEqual([r.revision_id for r in last.revisions], [first])
        self.assertIsNone(last.next_cursor)
        self.rule(self.doc.id, 'history_read', 'deny', actor='editor')
        with self.assertRaises(Forbidden):
            self.page(cursor=page.next_cursor)

    def test_scope_revision_and_cursor_reachability(self):
        self.save('second')
        cursor = self.page(limit=1).next_cursor
        for revision in (self.f.draft_revision_id, self.f.dev_revision_id, self.f.foreign_revision_id):
            with self.assertRaises(NotFound):
                self.service.read_document_revision(self.scope, self.doc.id, revision, session_token=self.editor)
            with self.assertRaises(NotFound):
                self.service.compare_document_revisions(self.scope, self.doc.id,
                    self.doc.revision_id, revision, session_token=self.editor)
        payload = json.loads(base64.urlsafe_b64decode(cursor))
        for index, value in ((1, 'other-workspace'), (2, self.f.dev_branch_id),
                             (3, self.f.products_id), (4, self.f.draft_id),
                             (5, self.f.draft_revision_id), (6, self.f.draft_revision_id)):
            forged = payload.copy()
            forged[index] = value
            with self.assertRaises(InvalidArgument):
                self.page(cursor=base64.urlsafe_b64encode(json.dumps(forged).encode()).decode())
        # A real revision of this object can exist without belonging to this branch's chain.
        with self.f.database.transaction(write=True) as connection:
            repo = Repository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            repo.insert_revision(RevisionRecord('detached', self.scope.workspace_id, self.doc.id,
                None, 'another branch', self.doc.created_at))
        # The same object has a divergent head in another real branch.
        dev = ContentScope(self.scope.workspace_id, self.f.dev_branch_id, self.f.root_id)
        with self.f.database.transaction(write=True) as connection:
            source = Repository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            target = Repository(connection, workspace_id=dev.workspace_id, branch_id=dev.branch_id)
            target.insert_entry(replace(source.get_entry(self.doc.id), branch_id=dev.branch_id,
                parent_id=dev.root_id, current_revision_id='detached'))
        self.assertEqual(self.service.read_document_revision(dev, self.doc.id, 'detached',
            session_token=self.editor).content, 'another branch')
        with self.assertRaises(NotFound):
            self.service.read_document_revision(dev, self.doc.id, self.doc.revision_id,
                session_token=self.editor)
        with self.assertRaises(InvalidArgument):
            self.service.list_document_revisions(dev, self.doc.id, cursor=cursor, session_token=self.editor)
        with self.assertRaises(NotFound):
            self.service.read_document_revision(self.scope, self.doc.id, 'detached', session_token=self.editor)
        forged = payload.copy()
        forged[5] = 'detached'
        with self.assertRaises(InvalidArgument):
            self.page(cursor=base64.urlsafe_b64encode(json.dumps(forged).encode()).decode())
        # Even a previously valid fixed head must still be reachable from the current branch head.
        with self.f.database.transaction(write=True) as connection:
            connection.execute('UPDATE entries SET current_revision_id=? WHERE object_id=? AND branch_id=?',
                ('detached', self.doc.id, self.scope.branch_id))
        with self.assertRaises(InvalidArgument):
            self.page(cursor=cursor)

    def test_invalid_limits_and_cursor(self):
        for limit in (0, 101, True, 1.5, '2', None):
            with self.assertRaises(InvalidArgument):
                self.page(limit=limit)
            with self.assertRaises(InvalidArgument):
                self.service.list_deleted_documents(self.scope, limit=limit, session_token=self.owner)
        invalid_scope = ContentScope(self.scope.workspace_id, self.scope.branch_id, self.doc.id)
        with self.assertRaises(NotFound):
            self.service.list_document_revisions(invalid_scope, self.doc.id, session_token=self.editor)
        for cursor in ('', '!', 'e30=', 42):
            with self.assertRaises(InvalidArgument):
                self.page(cursor=cursor)

    def test_move_and_rename_use_current_ancestry_keep_own_rules_and_private(self):
        self.rule(self.f.products_id, 'history_read', 'deny')
        self.rule(self.f.chinese_folder_id, 'history_read', 'allow')
        with self.assertRaises(Forbidden):
            self.page(session_token=self.reader)
        moved = self.service.move_node(self.scope, self.doc.id, self.f.chinese_folder_id,
            expected_version=self.doc.version, session_token=self.owner)
        self.assertEqual(moved.id, self.doc.id)
        self.page(session_token=self.reader)
        self.rule(self.doc.id, 'history_read', 'deny')
        moved = self.service.move_node(self.scope, moved.id, self.f.products_id,
            expected_version=moved.version, session_token=self.owner)
        moved = self.service.move_node(self.scope, moved.id, self.f.chinese_folder_id,
            expected_version=moved.version, session_token=self.owner)
        with self.assertRaises(Forbidden):
            self.page(session_token=self.reader)
        self.access.set_visibility(self.scope, moved.id, 'private', expected_version=self.version(), session_token=self.owner)
        renamed = self.service.rename_node(self.scope, moved.id, 'renamed', expected_version=moved.version, session_token=self.owner)
        self.assertEqual(renamed.id, self.doc.id)
        self.assertEqual(self.service.describe_access(self.scope, moved.id, session_token=self.owner).visibility, 'private')
        with self.assertRaises(NotFound):
            self.page(session_token=self.reader)

    def test_deleted_ancestor_listing_scope_pagination_and_active_refusal(self):
        # Deliberately retain an active child under a deleted ancestor: H07 includes both states.
        with self.f.database.transaction(write=True) as connection:
            connection.execute('UPDATE entries SET deleted_at=? WHERE object_id=?', ('deleted', self.f.products_id))
        page = self.service.list_deleted_documents(self.scope, limit=1, session_token=self.owner)
        documents = list(page.documents)
        cursor = page.next_cursor
        while cursor:
            following = self.service.list_deleted_documents(self.scope, cursor=cursor, limit=1, session_token=self.owner)
            documents.extend(following.documents)
            cursor = following.next_cursor
        self.assertIn(self.doc.id, [d.object_id for d in documents])
        self.assertEqual(next(d.path for d in documents if d.object_id == self.doc.id), '/products/history')
        self.page(session_token=self.owner)
        for actor in (self.owner, self.editor):
            with self.assertRaises(NotFound):
                self.service.read_document(self.scope, self.doc.id, session_token=actor)
            with self.assertRaises(NotFound):
                self.service.save_document(self.scope, self.doc.id, 'denied',
                    expected_revision_id=self.doc.revision_id, session_token=actor)
        with self.assertRaises(NotFound):
            self.page()
        other_scope = ContentScope(self.scope.workspace_id, self.scope.branch_id, self.f.chinese_folder_id)
        self.assertEqual(self.service.list_deleted_documents(other_scope, session_token=self.owner).documents, ())
        with self.assertRaises(NotFound):
            self.service.read_document_revision(other_scope, self.doc.id, self.doc.revision_id, session_token=self.owner)
        with self.assertRaises(InvalidArgument):
            self.service.list_deleted_documents(other_scope, cursor=page.next_cursor, session_token=self.owner)
        self.access.set_member_role(self.scope, self.actors['editor'].user_id, 'admin',
            expected_version=self.version(), session_token=self.owner)
        self.page()
        self.access.set_member_role(self.scope, self.actors['editor'].user_id, 'editor',
            expected_version=self.version(), session_token=self.owner)
        with self.assertRaises(Forbidden):
            self.service.list_deleted_documents(self.scope, cursor=page.next_cursor, session_token=self.editor)
        with self.f.database.transaction(write=True) as connection:
            connection.execute('UPDATE users SET site_admin=1 WHERE id=?', (self.actors['reader'].user_id,))
        self.access.remove_member(self.scope, self.actors['reader'].user_id, expected_version=self.version(), session_token=self.owner)
        with self.assertRaises(NotFound):
            self.page(session_token=self.reader)
        with self.assertRaises(Forbidden):
            self.service.list_deleted_documents(self.scope, session_token=self.reader)

    def test_cyclic_and_missing_revision_chains_rejected(self):
        with self.f.database.transaction(write=True) as connection:
            repo = Repository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            repo.insert_revision(RevisionRecord('cycle', self.scope.workspace_id, self.doc.id,
                'cycle', 'corrupt', self.doc.created_at))
            connection.execute('UPDATE entries SET current_revision_id=? WHERE object_id=?', ('cycle', self.doc.id))
        with self.assertRaises(UnsupportedSchema):
            self.page()
        with self.assertRaises(UnsupportedSchema):
            self.service.read_document_revision(self.scope, self.doc.id, 'cycle', session_token=self.editor)
        # Raw maintenance corruption is intentionally outside the repository's FK contract.
        import sqlite3
        with closing(sqlite3.connect(self.f.path)) as connection, connection:
            connection.execute('UPDATE entries SET current_revision_id=? WHERE object_id=?', ('missing', self.doc.id))
        with self.assertRaises(UnsupportedSchema):
            self.page()
