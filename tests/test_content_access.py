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
        # A corrupt protected sibling must not preempt the unreadable-root gate.
        with database.transaction(write=True) as connection:
            connection.execute("UPDATE entries SET deleted_at=? WHERE parent_id=? AND name='admin'",
                ('2026-01-01T00:00:00+00:00', root.root_id))
        blocked = self.service.bootstrap(session_token=self.reader)
        self.assertIsNotNone(blocked.session)
        self.assertEqual(blocked.workspace_role, "reader")
        self.assertEqual(blocked.workspace_access_version, bootstrap.workspace_access_version)
        self.assertIsNone(blocked.root)
        self.hidden(lambda: self.service.default_scope(session_token=self.reader))
        self.hidden(lambda: self.service.default_scope(session_token=None))
        self.assertIsNone(self.service.bootstrap(session_token=None).root)
        from src.core.errors import UnsupportedSchema
        from src.services.unit_of_work import ApplicationUnitOfWork
        with self.assertRaises(UnsupportedSchema) as public_error:
            self.service.default_scope(session_token=self.actors['owner'].session_token)
        self.assertEqual(dict(public_error.exception.details), {})
        self.assertEqual(public_error.exception.__cause__.details['name'], 'admin')
        with ApplicationUnitOfWork(database).transaction() as work:
            with self.assertRaises(UnsupportedSchema) as internal_error:
                work.default_scope()
            self.assertEqual(internal_error.exception.details['name'], 'admin')


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

class TestContentWriteAccess(TempPathTestCase):
    """Real write boundaries; policy permutations live in policy tests."""

    hidden = TestContentAccess.hidden

    def setUp(self):
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.actors = seed_test_actors(self.fixture.database, self.fixture.workspace_id)
        self.service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope
        self.reader = self.actors["reader"].session_token
        from src.services.access import AccessService
        self.access = AccessService(self.fixture.database)
        self.owner = self.actors['owner'].session_token
        self.editor = self.actors['editor'].session_token

    def version(self):
        return self.access.workspace_access(self.scope, session_token=self.owner).version

    def put(self, object_id, action, effect, actor='reader'):
        from src.access.models import AccessRule
        return self.access.put_rule(self.scope, AccessRule(object_id, 'user',
            self.actors[actor].user_id, action, effect), session_token=self.owner,
            expected_version=self.version())

    def test_private_creation_reader_exception_and_parent_gate(self):
        from src.core.errors import Forbidden
        from src.storage.access_repository import AccessRepository
        f = self.fixture
        self.put(f.products_id, 'create', 'allow')
        version = self.version()
        public = self.service.create_folder(self.scope, f.products_id, 'reader-created',
            session_token=self.reader)
        self.assertEqual(self.version(), version)
        with self.assertRaises(Forbidden):
            self.service.create_document(self.scope, f.products_id, 'reader-private',
                visibility='private', session_token=self.reader)
        private = self.service.create_document(self.scope, f.products_id, 'editor-private',
            content='private body', visibility='private', session_token=self.editor)
        self.assertEqual(private.content, 'private body')
        self.assertEqual(self.version(), version + 1)
        view = self.service.describe_access(self.scope, private.id, session_token=self.editor)
        self.assertEqual(view.visibility, 'private')
        self.assertEqual(view.version, version + 1)
        self.assertTrue(view.can_freeze)
        saved = self.service.save_document(self.scope, private.id, 'updated private body',
            expected_revision_id=private.revision_id, session_token=self.editor)
        self.service.set_metadata(self.scope, private.id, {'label': 'draft'},
            expected_version=saved.version, session_token=self.editor)
        self.assertEqual(self.service.describe_access(self.scope, private.id,
            session_token=self.editor).visibility, 'private')
        self.hidden(lambda: self.service.read_document(self.scope, private.id, session_token=self.reader))
        with f.database.transaction() as connection:
            repo = AccessRepository(connection, workspace_id=self.scope.workspace_id, branch_id=self.scope.branch_id)
            self.assertEqual(repo.get_ownership(public.id).creator_id, self.actors['reader'].user_id)
            self.assertEqual(repo.get_ownership(private.id).creator_id, self.actors['editor'].user_id)
            self.assertEqual(repo.get_privacy(private.id).owner_id, self.actors['editor'].user_id)
            event = connection.execute('SELECT actor_id,after_json FROM audit_events WHERE target_id=?',
                (private.id,)).fetchone()
            self.assertEqual(event['actor_id'], self.actors['editor'].user_id)
            self.assertNotIn('private body', event['after_json'])
        self.put(f.products_id, 'read', 'deny', actor='editor')
        self.hidden(lambda: self.service.create_document(self.scope, f.products_id, 'blocked',
            visibility='private', session_token=self.editor))
        with self.assertRaises(TypeError):
            self.service.create_folder(self.scope, f.main_id, 'no-token')
        with self.assertRaises(Unauthenticated):
            self.service.create_folder(self.scope, f.main_id, 'guest', session_token=None)

    def test_private_audit_failure_rolls_back_all_creation_state(self):
        from unittest.mock import patch
        from src.storage.audit_repository import AuditRepository
        from tests.helpers import entry_state
        def counts():
            with self.fixture.database.transaction() as connection:
                return {table: connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                    for table in ('objects', 'entries', 'document_revisions', 'content_ownership',
                                  'content_privacy', 'audit_events')}
        before, parent, version = counts(), entry_state(self.fixture.path, self.fixture.products_id), self.version()
        append = AuditRepository.append
        def fail(repo, event):
            append(repo, event)
            raise RuntimeError('audit failure')
        for method in ('create_folder', 'create_document'):
            with patch.object(AuditRepository, 'append', fail):
                with self.assertRaises(RuntimeError):
                    getattr(self.service, method)(self.scope, self.fixture.products_id, 'rollback',
                        visibility='private', session_token=self.editor)
            self.assertEqual(counts(), before)
            self.assertEqual(entry_state(self.fixture.path, self.fixture.products_id), parent)
            self.assertEqual(self.version(), version)

        with patch.object(AuditRepository, 'append', fail):
            with self.assertRaises(RuntimeError):
                self.access.set_visibility(self.scope, self.fixture.products_id, 'private',
                    session_token=self.owner, expected_version=version)
        self.assertEqual(counts(), before)
        self.assertEqual(self.version(), version)

    def test_visibility_original_creator_retention_and_independent_child(self):
        from src.core.errors import Conflict, Forbidden
        from src.services.accounts import AccountService
        # Use a fixed single-workspace database for the real account lifecycle service.
        from types import SimpleNamespace
        from src.storage import Database
        from src.storage.management import initialize_database
        from src.services.access import AccessService
        path = self.temp_path('retention.sqlite')
        initialize_database(path)
        database = Database(path)
        with database.transaction() as connection:
            workspace = connection.execute('SELECT id FROM workspaces').fetchone()[0]
        self.actors = seed_test_actors(database, workspace)
        self.owner, self.editor, self.reader = (self.actors[role].session_token
            for role in ('owner', 'editor', 'reader'))
        self.service, self.access = ContentService(database), AccessService(database)
        self.scope = self.service.default_scope(session_token=self.owner)
        self.fixture = SimpleNamespace(database=database, path=path, products_id=self.scope.root_id)
        parent = self.service.create_folder(self.scope, self.scope.root_id, 'personal',
            visibility='private', session_token=self.editor)
        child = self.service.create_document(self.scope, parent.id, 'child', visibility='private',
            session_token=self.editor)
        stale = self.version() - 1
        with self.assertRaises(Conflict):
            self.access.set_visibility(self.scope, parent.id, 'private', session_token=self.owner,
                expected_version=stale)
        with self.assertRaises(Forbidden):
            self.access.set_visibility(self.scope, parent.id, 'inherit', session_token=self.editor,
                expected_version=self.version())
        before = self.version()
        same = self.access.set_visibility(self.scope, parent.id, 'private', session_token=self.owner,
            expected_version=before)
        self.assertEqual(same.version, before)
        restored = self.access.set_visibility(self.scope, parent.id, 'inherit', session_token=self.owner,
            expected_version=before)
        self.assertEqual(restored.version, before + 1)
        self.service.get_node(self.scope, parent.id, session_token=self.reader)
        self.hidden(lambda: self.service.get_node(self.scope, child.id, session_token=self.reader))
        private_again = self.access.set_visibility(self.scope, parent.id, 'private', session_token=self.owner,
            expected_version=self.version())
        self.assertEqual(private_again.private_owner_id, self.actors['editor'].user_id)
        self.access.remove_member(self.scope, self.actors['editor'].user_id, session_token=self.owner,
            expected_version=self.version())
        self.hidden(lambda: self.service.get_node(self.scope, parent.id, session_token=self.editor))
        account = AccountService(self.fixture.database)
        editor_user = next(u for u in account.list_users(session_token=self.owner)
                           if u.id == self.actors['editor'].user_id)
        account.disable_user(editor_user.id, session_token=self.owner, expected_version=editor_user.version)
        self.hidden(lambda: self.service.get_node(self.scope, parent.id, session_token=self.reader))
        unchanged = self.access.object_access(self.scope, parent.id, session_token=self.owner)
        self.assertEqual(unchanged.private_owner_id, self.actors['editor'].user_id)
        self.assertEqual(self.access.object_access(self.scope, child.id, session_token=self.owner).visibility, 'private')
        # Legacy/imported NULL creator uses this administrator without changing ownership.
        fallback = self.access.set_visibility(self.scope, self.fixture.products_id, 'private',
            session_token=self.owner, expected_version=self.version())
        self.assertEqual(fallback.private_owner_id, self.actors['owner'].user_id)
        with self.fixture.database.transaction() as connection:
            self.assertIsNone(connection.execute('SELECT creator_id FROM content_ownership WHERE object_id=?',
                (self.fixture.products_id,)).fetchone()[0])

    def test_edit_revocation_locks_and_reserved_metadata(self):
        from src.core.errors import Forbidden, Frozen, InvalidArgument
        from tests.helpers import entry_state, revision_state
        f = self.fixture
        doc = self.service.read_document(self.scope, f.concretecream_id, session_token=self.editor)
        before = revision_state(f.path, doc.id)
        self.put(doc.id, 'edit', 'deny', actor='editor')
        with self.assertRaises(Forbidden):
            self.service.save_document(self.scope, doc.id, 'revoked',
                expected_revision_id=doc.revision_id, session_token=self.editor)
        with self.assertRaises(Forbidden):
            self.service.set_metadata(self.scope, doc.id, {}, expected_version=doc.version,
                session_token=self.editor)
        self.assertEqual(revision_state(f.path, doc.id), before)
        with f.database.transaction(write=True) as connection:
            connection.execute('INSERT INTO content_locks VALUES (?,?,?,?,?)',
                (self.scope.workspace_id, self.scope.branch_id, doc.id, self.actors['editor'].user_id,
                 datetime.now(timezone.utc).isoformat()))
        # Own lock cannot compensate for revoked normal rights.
        with self.assertRaises(Forbidden):
            self.service.save_document(self.scope, doc.id, doc.content,
                expected_revision_id=doc.revision_id, session_token=self.editor)
        for method, args, kwargs in (
                ('save_document', (doc.content,), {'expected_revision_id': doc.revision_id}),
                ('set_metadata', ({},), {'expected_version': doc.version})):
            with self.assertRaises(Frozen):
                getattr(self.service, method)(self.scope, doc.id, *args, session_token=self.owner, **kwargs)
        state = entry_state(f.path, doc.id)
        for key in ('acl', 'access_rules', 'visibility', 'private_owner_id', 'creator_id', 'locked_by'):
            with self.assertRaises(InvalidArgument):
                self.service.set_metadata(self.scope, doc.id, {key: None},
                    expected_version=doc.version, session_token=self.owner)
        self.assertEqual(entry_state(f.path, doc.id), state)
        from src.access.models import AccessRule
        self.access.remove_rule(self.scope, AccessRule(doc.id, 'user', self.actors['editor'].user_id,
            'edit', 'deny'), session_token=self.owner, expected_version=self.version())
        saved = self.service.save_document(self.scope, doc.id, 'own lock edit',
            expected_revision_id=doc.revision_id, session_token=self.editor)
        self.assertEqual(saved.content, 'own lock edit')

    def test_site_admin_nonmember_and_hidden_collision(self):
        from src.core.errors import AlreadyExists, Forbidden
        f = self.fixture
        private = self.service.create_folder(self.scope, f.products_id, 'hidden-sibling',
            visibility='private', session_token=self.editor)
        self.put(f.products_id, 'create', 'allow')
        with self.assertRaises(AlreadyExists) as caught:
            self.service.create_document(self.scope, f.products_id, 'hidden-sibling', session_token=self.reader)
        self.assertEqual(dict(caught.exception.details), {})
        self.assertNotIn('hidden-sibling', str(caught.exception))
        self.assertNotIn(private.id, str(caught.exception))
        # A site administrator without membership follows the public read baseline only.
        self.access.set_read_scope(self.scope, 'everyone', session_token=self.owner,
            expected_version=self.version())
        with f.database.transaction(write=True) as connection:
            connection.execute('UPDATE users SET site_admin=1 WHERE id=?', (self.actors['reader'].user_id,))
        self.access.remove_member(self.scope, self.actors['reader'].user_id, session_token=self.owner,
            expected_version=self.version())
        self.service.get_node(self.scope, f.products_id, session_token=self.reader)
        self.hidden(lambda: self.service.get_node(self.scope, private.id, session_token=self.reader))
        with self.assertRaises(Forbidden):
            self.service.create_folder(self.scope, f.products_id, 'nonmember-write', session_token=self.reader)
        with self.assertRaises(Forbidden):
            self.service.save_document(self.scope, f.concretecream_id, 'nonmember edit',
                expected_revision_id=f.concretecream_revision_id, session_token=self.reader)
        with self.assertRaises(Forbidden):
            self.service.set_metadata(self.scope, f.concretecream_id, {}, expected_version=1,
                session_token=self.reader)
        self.access.set_read_scope(self.scope, 'members', session_token=self.owner,
            expected_version=self.version())
        self.hidden(lambda: self.service.get_node(self.scope, f.products_id, session_token=self.reader))
