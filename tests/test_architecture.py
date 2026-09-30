"""AST boundaries for the shared runtime kernel and retired container API."""
import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports(path, tree):
    package = '.'.join(path.relative_to(ROOT).with_suffix('').parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if node.level:
                module = importlib.util.resolve_name('.' * node.level + module, package)
            yield module
            yield from (module + '.' + alias.name for alias in node.names)


class TestArchitecture(unittest.TestCase):
    def test_layer_import_boundaries(self):
        for layer, forbidden in (
            ('cli', ('src.file', 'src.storage.repository', 'sqlite3')),
            ('editor', ('src.file', 'src.storage', 'sqlite3')),
            ('web', ('src.cli', 'src.editor', 'src.file', 'src.storage.repository', 'sqlite3')),
            ('services', ('src.cli', 'src.editor', 'src.web', 'src.file', 'sqlite3')),
            ('core', ('src.storage', 'src.services', 'src.cli', 'src.editor', 'src.web', 'src.file', 'sqlite3')),
        ):
            for path in (ROOT / 'src' / layer).glob('*.py'):
                with self.subTest(path=path):
                    tree = ast.parse(path.read_text())
                    for imported in imports(path, tree):
                        if layer in ('cli', 'editor', 'web'):
                            self.assertFalse(imported.endswith('.Repository'), imported)
                        self.assertFalse(any(imported == name or imported.startswith(name + '.')
                                             for name in forbidden), imported)
        path = ROOT / 'src/storage/repository.py'
        tree = ast.parse(path.read_text())
        for imported in imports(path, tree):
            self.assertFalse(imported.startswith(('src.services', 'src.cli', 'src.editor', 'src.web')))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ('commit', 'rollback', 'connect', 'transaction',
                                                  'management_connection', 'create_schema'))
                if node.func.attr in ('execute', 'executescript') and node.args:
                    sql = node.args[0]
                    if isinstance(sql, ast.Constant) and isinstance(sql.value, str):
                        self.assertNotIn(sql.value.lstrip().split()[0].upper(),
                                         ('BEGIN', 'COMMIT', 'ROLLBACK', 'CREATE', 'ALTER'))
        for path in (ROOT / 'src/services').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ('execute', 'executemany', 'executescript'))

    def test_runtime_paths_do_not_scan_host_content(self):
        # app may locate its database, and editor may read its own static help.md.
        for filename in ('paths.py', 'commands.py', 'completion.py'):
            path = ROOT / 'src/cli' / filename
            tree = ast.parse(path.read_text())
            self.assertFalse(any(name == 'pathlib' or name.startswith(('pathlib.', 'os'))
                                 for name in imports(path, tree)))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ('rglob', 'glob', 'iterdir', 'walk', 'listdir',
                                                      'read_text', 'write_text', 'read_bytes', 'write_bytes'))

    def test_static_editor_help_and_app_database_location_are_allowed(self):
        for filename in ('src/editor/editor.py', 'src/cli/app.py'):
            tree = ast.parse((ROOT / filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ('rglob', 'glob', 'iterdir', 'walk', 'listdir',
                                                      'write_text', 'write_bytes'))
                    if filename.endswith('editor.py') and node.func.attr == 'read_text':
                        # The sole read target is statically anchored to this module's help file.
                        self.assertIsInstance(node.func.value, ast.Call)
                        self.assertIsInstance(node.func.value.func, ast.Attribute)
                        self.assertEqual(node.func.value.func.attr, 'with_name')
                        self.assertEqual(node.func.value.args[0].value, 'help.md')

    def test_old_runtime_api_is_retired(self):
        import src.file
        for name in ('Document', 'Folder', 'SqlFile', 'AbstractFileRecord'):
            self.assertFalse(hasattr(src.file, name), name)
        for name in ('sql.py', 'document.py', 'folder.py'):
            self.assertFalse((ROOT / 'src/file' / name).exists(), name)
        tree = ast.parse((ROOT / 'src/file/bootstrap.py').read_text())
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == 'initialize_data'
                             for node in ast.walk(tree)))
