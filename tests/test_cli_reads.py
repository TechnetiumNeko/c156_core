"""Service-backed CLI browsing and runtime startup."""
import io
import sqlite3
from contextlib import closing, redirect_stderr
from unittest.mock import patch

from src.cli.app import CLI, main
from src.cli.commands import Command
from src.storage.errors import StorageError
from src.core.errors import PathOutsideRoot
from src.services.content import ContentService
from src.storage import Database
from src.storage.management import initialize_database
from tests.helpers import TempPathTestCase


class CLIReadFixture(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.temp_path()
        self.root_scope = initialize_database(self.path)
        self.service = ContentService(Database(self.path))
        self.scope = self.service.default_scope()
        self.service.create_folder(self.scope, self.scope.root_id, "products")
        self.output = io.StringIO()
        self.cli = CLI(self.service, self.scope, output=lambda *a, **kw: print(*a, file=self.output, **kw))


class CLIReadTests(CLIReadFixture):
    def test_cli_cwd_is_per_instance(self):
        second = CLI(self.service, self.scope)
        self.cli.execute('cd products')
        self.assertEqual(self.cli.fs.display(), '/products')
        self.assertEqual(second.fs.display(), '/')
        self.cli.execute('pwd')
        self.assertEqual(self.output.getvalue(), '/products\n')

    def test_ls_file_hidden_name_and_chinese_cat(self):
        self.service.create_document(self.scope, self.scope.root_id, '.folder', content='中文正文')
        self.cli.execute('ls /.folder')
        self.cli.execute('ls')
        self.cli.execute('cat /.folder')
        self.assertEqual(self.output.getvalue().splitlines().count('.folder'), 2)
        self.assertTrue(self.output.getvalue().endswith('中文正文\n'))

    def test_tree_default_depth_and_explicit_zero(self):
        one = self.service.create_folder(self.scope, self.scope.root_id, 'one')
        two = self.service.create_folder(self.scope, one.id, 'two')
        self.service.create_document(self.scope, two.id, 'deep')
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
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work')
        products = self.cli.fs.resolve('/products')
        second = CLI(self.service, self.scope)
        self.cli.execute('cd work')
        moved = self.service.move_node(self.scope, folder.id, products.id, expected_version=folder.version)
        self.cli.execute('pwd')
        self.assertIn('/products/work', self.output.getvalue())
        self.assertEqual(self.cli.fs.cwd_id, folder.id)
        self.service.delete_node(self.scope, moved.id, expected_version=moved.version)
        self.cli.execute('pwd')
        self.assertEqual(self.cli.fs.display(), '/')
        self.assertIn('当前目录', self.output.getvalue())
        self.assertEqual(second.fs.display(), '/')

    def test_deleted_cwd_resets_before_prompt(self):
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work')
        self.cli.execute('cd work')
        self.service.delete_node(self.scope, folder.id, expected_version=folder.version)
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
        folder = self.service.create_folder(self.scope, self.scope.root_id, 'work')
        self.cli.execute('cd work')
        outside = self.service.create_folder(self.root_scope, self.root_scope.root_id, 'outside')
        self.service.move_node(self.root_scope, folder.id, outside.id, expected_version=folder.version)
        self.cli.execute('pwd')
        self.assertEqual(self.cli.fs.cwd_id, self.scope.root_id)
        self.assertIn('当前目录', self.output.getvalue())

    def test_tree_uses_sibling_connectors(self):
        one = self.service.create_folder(self.scope, self.scope.root_id, 'one')
        self.service.create_folder(self.scope, one.id, 'a')
        self.service.create_document(self.scope, one.id, 'b')
        self.cli.execute('tree /one')
        self.assertEqual(self.output.getvalue(), 'one\n  ├── a/\n  └── b\n')

    def test_main_opens_valid_database_without_initializing(self):
        with patch.object(CLI, 'run', return_value=0) as run:
            self.assertEqual(main(['--database', str(self.path)]), 0)
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
