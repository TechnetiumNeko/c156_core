"""Startup checks operate only on disposable libraries."""
import asyncio
from contextlib import closing
from dataclasses import FrozenInstanceError
import sqlite3
import subprocess
import sys

from src.server.app import create_app
from src.server.config import ServerConfig
from src.storage.management import initialize_database
from tests.helpers import TempPathTestCase, PROJECT_ROOT, table_counts


async def start(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        return app.state.services


class TestServerStartup(TempPathTestCase):
    def test_missing_database_is_not_created(self):
        path = self.temp_path()
        with self.assertRaisesRegex(RuntimeError, 'python -m src.storage init'):
            asyncio.run(start(ServerConfig(path)))
        self.assertEqual(list(self.temp_root.iterdir()), [])

    def test_incompatible_database_is_not_repaired(self):
        path = self.temp_path()
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute('CREATE TABLE sentinel (value TEXT)')
                connection.execute("INSERT INTO sentinel VALUES ('untouched')")
                connection.execute('PRAGMA user_version = 999')
        original = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, '无法启动'):
            asyncio.run(start(ServerConfig(path)))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.temp_root.iterdir()), [path])

    def test_initialized_library_starts(self):
        path = self.temp_path()
        root_scope = initialize_database(path)
        before = table_counts(path)
        services = asyncio.run(start(ServerConfig(path)))
        self.assertEqual(services.scope.workspace_id, root_scope.workspace_id)
        self.assertEqual(services.scope.branch_id, root_scope.branch_id)
        self.assertNotEqual(services.scope.root_id, root_scope.root_id)
        self.assertTrue(services.nonce)
        self.assertEqual(table_counts(path), before)

    def test_help_does_not_open_database(self):
        path = self.temp_path()
        for entry in (['run_server.py'], ['-m', 'src.server']):
            result = subprocess.run([sys.executable, *entry, '--database', str(path), '--help'],
                                    cwd=PROJECT_ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--allowed-origin', result.stdout)
        self.assertEqual(list(self.temp_root.iterdir()), [])

    def test_configuration_rejects_public_listening_and_invalid_ports(self):
        for host in ('0.0.0.0', '::', 'example.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                ServerConfig(self.temp_path(), host=host)
        for port in (0, -1, 65536, True, '8001'):
            with self.subTest(port=port), self.assertRaises(ValueError):
                ServerConfig(self.temp_path(), port=port)
        config = ServerConfig(self.temp_path())
        with self.assertRaises(FrozenInstanceError):
            config.port = 9000
