"""Service-backed CLI browsing and runtime startup."""
import io
import sqlite3
from contextlib import closing, redirect_stderr
from unittest.mock import patch

from src.cli.app import CLI, main
from src.cli.commands import Command
from src.storage.errors import StorageError
from src.core.errors import PathOutsideRoot, NotFound
from src.access.models import AccessRule
from src.services.access import AccessService
from src.services.identity import IdentityService
from src.editor import EditorResult
from src.services.content import ContentService
from src.storage import Database
from src.storage.management import initialize_database
from src.identity.tokens import token_digest
from src.identity.passwords import PasswordHasher
from src.storage.identity_repository import IdentityRepository, PasswordCredentialRecord
from tests.helpers import seed_test_actors, TempPathTestCase


class CLIReadFixture(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        self.root_scope = initialize_database(self.path)
        self.service = ContentService(Database(self.path))
        self.actors = seed_test_actors(Database(self.path), self.root_scope.workspace_id)
        self.token = self.actors["owner"].session_token
        self.password = "test password of fifteen"
        encoded = PasswordHasher().hash(self.password)
        with Database(self.path).transaction(write=True) as connection:
            repo = IdentityRepository(connection)
            for actor in self.actors.values():
                repo.set_password_credential(PasswordCredentialRecord(actor.user_id, encoded, 1, "2026-10-03T00:00:00+00:00"))
        self.scope = self.service.default_scope(session_token=self.token)
        self.service.create_folder(self.scope, self.scope.root_id, "products", session_token=self.token)
        self.output = io.StringIO()
        self.cli = CLI(self.service, self.scope, output=lambda *a, **kw: print(*a, file=self.output, **kw), session_token=self.token)


class CLIReadTests(CLIReadFixture):
    def test_cli_cwd_is_per_instance(self):
        second = CLI(self.service, self.scope, session_token=self.token)
        self.cli.execute('cd products')
        self.assertEqual(self.cli.fs.display(), '/products')
        self.assertEqual(second.fs.display(), '/')
        self.cli.execute('pwd')
        self.assertEqual(self.output.getvalue(), '/products\n')

    def test_ls_file_hidden_name_and_chinese_cat(self):
        self.service.create_document(self.scope, self.scope.root_id, '.folder', content='中文正文', session_token=self.token)
        self.cli.execute('ls /.folder')
        self.cli.execute('ls')
        self.cli.execute('cat /.folder')
        self.assertEqual(self.output.getvalue().splitlines().count('.folder'), 2)
        self.assertTrue(self.output.getvalue().endswith('中文正文\n'))

    def test_tree_default_depth_and_explicit_zero(self):
        one = self.service.create_folder(self.scope, self.scope.root_id, 'one', session_token=self.token)
        two = self.service.create_folder(self.scope, one.id, 'two', session_token=self.token)
        self.service.create_document(self.scope, two.id, 'deep', session_token=self.token)
        self.cli.execute('tree')
        self.assertIn('two/', self.output.getvalue())
        self.assertNotIn('deep', self.output.getvalue())
        self.output.seek(0); self.output.truncate()
        self.cli.execute('tree /one -d 0')
        self.assertEqual(self.output.getvalue(), 'one\n')
        for line in ('tree -d -1', 'tree --max-depth nope', 'tree -d'):
            self.cli.execute(line)
        self.assertIn('非负整数', self.output.getvalue())

    def test_cwd_follows_move_and_resets_after_delete(self):
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work', session_token=self.token)
        products = self.cli.fs.resolve('/products')
        second = CLI(self.service, self.scope, session_token=self.token)
        self.cli.execute('cd work')
        moved = self.service.move_node(self.scope, folder.id, products.id, expected_version=folder.version, session_token=self.token)
        self.cli.execute('pwd')
        self.assertIn('/products/work', self.output.getvalue())
        self.assertEqual(self.cli.fs.cwd_id, folder.id)
        self.service.delete_node(self.scope, moved.id, expected_version=moved.version, session_token=self.token)
        self.cli.execute('pwd')
        self.assertEqual(self.cli.fs.display(), '/products')
        self.assertIn('当前目录', self.output.getvalue())
        self.assertEqual(second.fs.display(), '/')

    def test_deleted_cwd_resets_before_prompt(self):
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work', session_token=self.token)
        self.cli.execute('cd work')
        self.service.delete_node(self.scope, folder.id, expected_version=folder.version, session_token=self.token)
        prompts = []
        self.cli.context.prompt = lambda value: prompts.append(value) or 'exit'
        with patch('readline.set_completer'), patch('readline.set_completer_delims'), patch('readline.parse_and_bind'):
            self.assertEqual(self.cli.run(), 0)
        self.assertEqual(prompts, ['c156:/$ '])
        self.assertIn('当前目录', self.output.getvalue())

    def test_help_exit_type_and_scope_errors(self):
        self.cli.execute('help cd')
        self.assertIn('cd <path>', self.output.getvalue())
        self.assertFalse(self.cli.execute('exit'))
        self.assertTrue(self.cli.execute('exit unexpected'))
        self.cli.execute('cd /../admin')
        self.assertIn('超出', self.output.getvalue())
        with self.assertRaises(PathOutsideRoot):
            self.cli.fs.resolve('..')
        self.cli.execute('cat products')
        self.assertIn('文档', self.output.getvalue())

    def test_registry_and_unknown_storage_errors(self):
        self.cli.register(Command('custom', '扩展', 'custom', lambda context, args: context.output('扩展结果')))
        self.cli.execute('help custom')
        self.cli.execute('custom')
        self.assertIn('扩展结果', self.output.getvalue())
        with patch.object(self.service, 'list_children', side_effect=StorageError('unexpected failure')):
            with self.assertRaisesRegex(StorageError, 'unexpected failure'):
                self.cli.execute('ls')

    def test_cwd_moved_outside_scope_resets(self):
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work', session_token=self.token)
        self.cli.execute('cd work')
        outside = self.service.create_folder(self.root_scope, self.root_scope.root_id, 'outside', session_token=self.token)
        self.service.move_node(self.root_scope, folder.id, outside.id, expected_version=folder.version, session_token=self.token)
        self.cli.execute('pwd')
        self.assertEqual(self.cli.fs.cwd_id, self.scope.root_id)
        self.assertIn('当前目录', self.output.getvalue())

    def test_tree_uses_sibling_connectors(self):
        one = self.service.create_folder(self.scope, self.scope.root_id, 'one', session_token=self.token)
        self.service.create_folder(self.scope, one.id, 'a', session_token=self.token)
        self.service.create_document(self.scope, one.id, 'b', session_token=self.token)
        self.cli.execute('tree /one')
        self.assertEqual(self.output.getvalue(), 'one\n  ├── a/\n  └── b\n')

    def test_main_opens_valid_database_without_initializing(self):
        with patch.object(CLI, 'run', return_value=0) as run:
            self.assertEqual(main(['--database', str(self.path)], prompt=lambda _: 'test-owner',
                                  password_prompt=lambda _: self.password), 0)
        run.assert_called_once()

    def test_main_rejects_missing_empty_invalid_future_and_nonwal_without_modifying(self):
        paths = [self.temp_path('missing.db'), self.temp_path('empty.db'), self.temp_path('invalid.db'), self.temp_path('future.db'), self.path]
        paths[1].touch()
        paths[2].write_bytes(b'not sqlite')
        with closing(sqlite3.connect(paths[3])) as connection:
            connection.execute('PRAGMA user_version=999')
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute('PRAGMA journal_mode=DELETE')
        for path in paths:
            with self.subTest(path=path):
                before = path.read_bytes() if path.exists() else None
                error = io.StringIO()
                with redirect_stderr(error), patch.object(CLI, 'run') as run:
                    self.assertNotEqual(main(['--database', str(path)]), 0)
                run.assert_not_called()
                self.assertIn('python -m src.storage init --database', error.getvalue())
                self.assertIn('migrate-legacy --source data --database', error.getvalue())
                self.assertEqual(path.read_bytes() if path.exists() else None, before)


class CLIIdentityTests(CLIReadFixture):
    def setUp(self):
        super().setUp()
        self.token = IdentityService(Database(self.path)).login("test-owner", self.password, source="local").session_token

    def login(self, role, *answers):
        values = iter(['test-' + role, *answers])
        self.cli.context.prompt = lambda _: next(values)
        self.cli.password_prompt = lambda _: self.password
        self.cli.execute('login')

    def test_no_root_can_help_relogin_exit_and_recover_after_grant(self):
        access = AccessService(Database(self.path))
        rule = AccessRule(self.scope.root_id, 'user', self.actors['editor'].user_id, 'read', 'deny')
        version = self.service.describe_access(self.scope, self.scope.root_id, session_token=self.token).version
        denied = access.put_rule(self.scope, rule, session_token=self.token, expected_version=version)
        self.login('editor')
        self.assertFalse(self.cli.fs.available)
        self.cli.execute('help login')
        self.assertIn('login', self.output.getvalue())
        self.login('editor')
        self.assertFalse(self.cli.fs.available)
        self.cli.execute('ls')
        self.assertIn('无可用内容', self.output.getvalue())
        access.remove_rule(self.scope, rule, session_token=self.token, expected_version=denied.version)
        self.cli.execute('ls')
        self.assertTrue(self.cli.fs.available)
        self.assertIn('products/', self.output.getvalue())
        access.put_rule(self.scope, rule, session_token=self.token, expected_version=denied.version + 1)
        self.cli.context.prompt = lambda _: 'exit'
        self.assertEqual(self.cli.run(), 0)
        self.assertFalse(self.cli.fs.available)

    def test_cwd_read_loss_falls_back_to_nearest_readable_ancestor(self):
        folder = self.service.create_folder(self.scope, self.cli.fs.resolve('products').id, 'child', session_token=self.token)
        self.login('editor')
        self.cli.execute('cd products/child')
        access = AccessService(Database(self.path))
        rule = AccessRule(folder.id, 'user', self.actors['editor'].user_id, 'read', 'deny')
        version = self.service.describe_access(self.scope, folder.id, session_token=self.token).version
        access.put_rule(self.scope, rule, session_token=self.token, expected_version=version)
        self.cli.execute('pwd')
        self.assertEqual(self.cli.fs.display(), '/products')

    def test_private_cli_creation_hidden_from_other_editor_and_existing_rejected(self):
        self.login('editor')
        self.cli.execute('mkdir --private secret')
        class Substitute:
            def run(self):
                return EditorResult(True, 'private text', True)
        self.cli.context.editor_factory = lambda *a, **kw: Substitute()
        self.cli.context.prompt = lambda _: 'y'
        self.cli.execute('edit --private confidential')
        secret = self.cli.fs.resolve('confidential')
        self.assertEqual(self.service.read_document(self.scope, secret.id, session_token=self.cli.fs.session_token).content, 'private text')
        self.cli.execute('edit --private confidential')
        self.assertIn('仅用于新建', self.output.getvalue())
        # The other actor is given the editor role through the actual management API.
        access = AccessService(Database(self.path))
        version = self.service.describe_access(self.scope, self.scope.root_id, session_token=self.token).version
        access.set_member_role(self.scope, self.actors['reader'].user_id, 'editor', session_token=self.token, expected_version=version)
        other = CLI(self.service, self.scope, session_token=self.actors['reader'].session_token)
        self.assertNotIn('secret/', other.fs.completion_candidates(''))
        self.assertNotIn('confidential', other.fs.completion_candidates(''))
        with self.assertRaises(NotFound):
            other.fs.resolve('confidential')

    def test_expired_save_retains_draft_and_same_login_base_cross_login_discard(self):
        self.login('editor')
        doc = self.service.create_document(self.scope, self.scope.root_id, 'doc', content='base', session_token=self.cli.fs.session_token)
        test = self
        class ExpiringEditor:
            def run(self):
                with Database(test.path).transaction(write=True) as connection:
                    connection.execute("UPDATE sessions SET expires_at=? WHERE token_digest=?",
                                       ("2000-01-01T00:00:00+00:00", token_digest(test.cli.fs.session_token)))
                return EditorResult(True, 'unsaved', True)
        self.cli.context.editor_factory = lambda *a, **kw: ExpiringEditor()
        self.cli.execute('edit doc')
        pending = self.cli.context.pending_edit
        self.assertEqual((pending.content, pending.base_revision_id, pending.user_id),
                         ('unsaved', doc.revision_id, self.actors['editor'].user_id))
        self.cli.password_prompt = lambda _: 'wrong password'
        self.cli.context.prompt = lambda _: 'test-editor'
        self.cli.execute('login')
        self.assertIs(self.cli.context.pending_edit, pending)
        self.login('editor')
        self.assertIs(self.cli.context.pending_edit, pending)
        self.login('reader', 'later')
        self.assertEqual(self.cli.context.user_id, self.actors['editor'].user_id)
        self.assertIs(self.cli.context.pending_edit, pending)
        self.login('reader', 'discard')
        self.assertEqual(self.cli.context.user_id, self.actors['reader'].user_id)
        self.assertIsNone(self.cli.context.pending_edit)
        self.assertEqual(self.service.read_document(self.scope, doc.id, session_token=self.token).content, 'base')
        self.cli.execute('logout')
        self.cli.execute('ls')
        self.assertIsNone(self.cli.fs.session_token)
        self.assertEqual(self.cli.fs.completion_candidates(''), [])
        self.assertIn('请先 login', self.output.getvalue())

    def test_main_without_accounts_and_help_do_not_authenticate_or_initialize(self):
        empty = self.temp_path('no-accounts.db')
        initialize_database(empty)
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['--database', str(empty)], prompt=lambda _: self.fail('unexpected prompt')), 1)
        self.assertIn('bootstrap-admin', error.getvalue())
        with self.assertRaises(SystemExit) as result, patch('src.cli.app.Database') as database:
            main(['--help'])
        self.assertEqual(result.exception.code, 0)
        database.assert_not_called()

    def test_write_permission_loss_pauses_with_body_and_original_base(self):
        self.login('editor')
        doc = self.service.create_document(self.scope, self.scope.root_id, 'doc', content='base', session_token=self.cli.fs.session_token)
        test = self
        class RevokingEditor:
            def run(self):
                version = test.service.describe_access(test.scope, doc.id, session_token=test.token).version
                AccessService(Database(test.path)).put_rule(test.scope,
                    AccessRule(doc.id, 'user', test.actors['editor'].user_id, 'edit', 'deny'),
                    session_token=test.token, expected_version=version)
                return EditorResult(True, 'draft after revocation', True)
        self.cli.context.editor_factory = lambda *a, **kw: RevokingEditor()
        self.cli.context.prompt = lambda _: self.fail('permission loss must pause, not prompt a retry')
        self.cli.execute('edit doc')
        pending = self.cli.context.pending_edit
        self.assertEqual((pending.content, pending.base_revision_id), ('draft after revocation', doc.revision_id))
        self.assertEqual(self.service.read_document(self.scope, doc.id, session_token=self.token).content, 'base')
