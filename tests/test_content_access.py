"""SQLite integration checks for authorization and public information boundaries."""
from datetime import datetime, timedelta, timezone
from src.core.errors import NotFound, Unauthenticated
from src.core.models import ContentScope
from src.services import ContentService
from src.identity.tokens import token_digest
from tests.helpers import TempPathTestCase, seed_content_read_fixture, seed_test_actors


class TestContentAccess(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.actors = seed_test_actors(self.fixture.database, self.fixture.workspace_id)
        self.service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope
        self.reader = self.actors['reader'].session_token

    def rule(self, object_id, effect):
        with self.fixture.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO access_rules VALUES (?,?,?,?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, object_id,
                 'everyone', '', None, 'read', effect))

    def hidden(self, call):
        with self.assertRaises(NotFound) as caught:
            call()
        self.assertEqual(str(caught.exception), 'Object not found')
        self.assertEqual(dict(caught.exception.details), {})

    def test_hidden_ancestor_blocks_id_path_metadata_and_deep_root(self):
        f = self.fixture
        self.rule(f.products_id, 'deny')
        self.rule(f.concretecream_id, 'allow')
        deep = ContentScope(self.scope.workspace_id, self.scope.branch_id, f.products_id)
        for scope in (self.scope, deep):
            for name in ('get_node', 'get_path', 'get_metadata', 'read_document',
                         'get_node_with_access', 'read_document_with_access', 'describe_access'):
                self.hidden(lambda name=name, scope=scope: getattr(self.service, name)(
                    scope, f.concretecream_id, session_token=self.reader))
        for path in ('products/concretecream', 'products/../中文 空格', 'products/.'):
            self.hidden(lambda path=path: self.service.resolve_path(self.scope, path,
                session_token=self.reader))
        self.assertEqual([n.id for n in self.service.list_children(self.scope, self.scope.root_id,
            session_token=self.reader)], [f.chinese_folder_id])
        self.assertEqual([i.node.id for i in self.service.list_tree(self.scope, self.scope.root_id,
            session_token=self.reader)], [f.main_id, f.chinese_folder_id, f.notes_id])

    def test_hidden_document_kind_and_corrupt_metadata_do_not_leak(self):
        f = self.fixture
        self.rule(f.concretecream_id, 'deny')
        with f.database.transaction(write=True) as connection:
            connection.execute("UPDATE entries SET metadata_json='invalid' WHERE object_id=?",
                (f.concretecream_id,))
        self.hidden(lambda: self.service.resolve_path(self.scope, 'products/concretecream/next',
            session_token=self.reader))
        self.hidden(lambda: self.service.list_children(self.scope, f.concretecream_id,
            session_token=self.reader))
        self.hidden(lambda: self.service.get_node(self.scope, f.concretecream_id,
            session_token=self.reader))

    def test_positions_are_visible_ordinals_in_all_snapshots(self):
        f = self.fixture
        self.rule(f.concretecream_id, 'deny')
        with f.database.transaction(write=True) as connection:
            connection.execute('UPDATE entries SET position=9 WHERE object_id=?', (f.draft_id,))
        children = self.service.list_children_with_access(self.scope, f.products_id,
            session_token=self.reader)
        self.assertEqual([(n.node.id, n.node.position) for n in children], [(f.draft_id, 0)])
        self.assertEqual(self.service.get_node(self.scope, f.draft_id,
            session_token=self.reader).position, 0)
        self.assertEqual(self.service.read_document_with_access(self.scope, f.draft_id,
            session_token=self.reader).document.position, 0)
        root = self.service.get_node(self.scope, f.main_id, session_token=self.reader)
        self.assertEqual((root.parent_id, root.position), (None, 0))
        with f.database.transaction() as connection:
            self.assertEqual(connection.execute('SELECT position FROM entries WHERE object_id=?',
                (f.draft_id,)).fetchone()[0], 9)

    def test_private_ancestor_remains_closed_with_public_child(self):
        f = self.fixture
        with f.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO content_privacy VALUES (?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, f.products_id,
                 self.actors['editor'].user_id, '2026-01-01T00:00:00+00:00'))
        self.rule(f.concretecream_id, 'allow')
        self.hidden(lambda: self.service.read_document(self.scope, f.concretecream_id,
            session_token=self.reader))
        self.assertEqual(self.service.read_document(self.scope, f.concretecream_id,
            session_token=self.actors['editor'].session_token).content, f.concretecream_content)

    def test_explicit_guest_and_nonmember_read_never_accept_invalid_tokens(self):
        f = self.fixture
        self.rule(f.root_id, 'allow')
        with f.database.transaction(write=True) as connection:
            connection.execute('DELETE FROM workspace_memberships WHERE user_id=?',
                (self.actors['reader'].user_id,))
        for token in (None, self.reader):
            self.assertEqual(self.service.get_node(self.scope, f.products_id,
                session_token=token).id, f.products_id)
        with self.assertRaises(TypeError):
            self.service.get_node(self.scope, f.products_id)
        for token in ('invalid', '', 42):
            with self.assertRaises(Unauthenticated):
                self.service.get_node(self.scope, f.products_id, session_token=token)
        with f.database.transaction(write=True) as connection:
            connection.execute('UPDATE sessions SET expires_at=? WHERE token_digest=?',
                ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(), token_digest(self.reader)))
        with self.assertRaises(Unauthenticated):
            self.service.bootstrap(session_token=self.reader)

    def test_scope_hidden_and_missing_ids_share_error(self):
        for object_id in ('unknown', self.fixture.admin_id, self.fixture.foreign_document_id):
            self.hidden(lambda object_id=object_id: self.service.get_node(self.scope, object_id,
                session_token=self.reader))

    def test_bootstrap_and_capabilities_are_detached_and_consistent(self):
        f = self.fixture
        from src.storage.management import initialize_database
        from src.storage import Database
        root = initialize_database(self.temp_path('bootstrap.sqlite'))
        database = Database(self.temp_path('bootstrap.sqlite'))
        self.actors = seed_test_actors(database, root.workspace_id)
        self.reader = self.actors['reader'].session_token
        self.service = ContentService(database)
        self.scope = self.service.default_scope(session_token=self.reader)
        anonymous = self.service.bootstrap(session_token=None)
        self.assertTrue(anonymous.initialized)
        self.assertIsNone(anonymous.session)
        self.assertIsNone(anonymous.root)
        bootstrap = self.service.bootstrap(session_token=self.reader)
        self.assertEqual(bootstrap.workspace_role, 'reader')
        self.assertEqual(bootstrap.root.node.id, self.scope.root_id)
        with database.transaction(write=True) as connection:
            connection.execute('INSERT INTO access_rules VALUES (?,?,?,?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, self.scope.root_id,
                 'everyone', '', None, 'read', 'deny'))
        blocked = self.service.bootstrap(session_token=self.reader)
        self.assertIsNotNone(blocked.session)
        self.assertIsNone(blocked.root)
        self.hidden(lambda: self.service.default_scope(session_token=self.reader))

    def test_freeze_capabilities_require_member_creator_edit_and_lock_authority(self):
        f = self.fixture
        editor = self.actors['editor']
        with f.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO content_ownership VALUES (?,?,?)',
                (self.scope.workspace_id, f.draft_id, editor.user_id))
        access = self.service.describe_access(self.scope, f.draft_id, session_token=editor.session_token)
        self.assertTrue(access.can_freeze)
        self.assertFalse(access.can_unfreeze)
        self.assertIn('edit', access.actions)
        with f.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO content_locks VALUES (?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, f.draft_id,
                 self.actors['reader'].user_id, '2026-01-01T00:00:00+00:00'))
        access = self.service.describe_access(self.scope, f.draft_id, session_token=editor.session_token)
        self.assertTrue(access.frozen)
        self.assertFalse(access.can_freeze)
        self.assertFalse(access.can_unfreeze)
        self.assertNotIn('edit', access.actions)
        owner = self.service.describe_access(self.scope, f.draft_id,
            session_token=self.actors['owner'].session_token)
        self.assertTrue(owner.can_unfreeze)
        self.assertNotIn('edit', owner.actions)
