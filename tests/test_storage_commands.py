"""Task 9: the thin ``python -m src.storage`` management adapter."""

from __future__ import annotations

import io
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.storage import Database
from src.storage.__main__ import main
from tests.helpers import (
    PROJECT_ROOT,
    SAMPLE_DOCUMENT_ID,
    TempPathTestCase,
    copy_sample_data,
)


class StorageCommandTests(TempPathTestCase):
    def test_init_command_creates_default_database(self) -> None:
        path = self.temp_path()

        code = main(["init", "--database", str(path)])

        self.assertEqual(code, 0)
        self.assertTrue(path.exists())
        with Database(path).transaction() as connection:
            self.assertEqual(
                str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
                "wal",
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],
                1,
            )

    def test_init_failure_is_nonzero_without_success_output(self) -> None:
        path = self.temp_path("empty.sqlite")
        path.touch()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["init", "--database", str(path)])

        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())
        self.assertEqual(path.read_bytes(), b"")

    def test_migrate_legacy_command_imports_sample(self) -> None:
        source = copy_sample_data(self.temp_root / "data")
        target = self.temp_root / "c156.sqlite"

        code = main(
            [
                "migrate-legacy",
                "--source",
                str(source),
                "--database",
                str(target),
            ]
        )

        self.assertEqual(code, 0)
        connection = sqlite3.connect(target)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
                8,
            )
            row = connection.execute(
                "SELECT object_id FROM entries WHERE name = ?", ("concretecream",)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], SAMPLE_DOCUMENT_ID)

    def test_migrate_legacy_failure_is_nonzero(self) -> None:
        missing = self.temp_root / "missing-source"
        target = self.temp_root / "c156.sqlite"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "migrate-legacy",
                    "--source",
                    str(missing),
                    "--database",
                    str(target),
                ]
            )

        self.assertNotEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())
        self.assertFalse(target.exists())

    def test_configuration_failure_prints_rerun_command(self):
        from src.storage.errors import SchemaError
        source = copy_sample_data(self.temp_root / "data with space")
        target = self.temp_root / "c156.sqlite"
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(Database, "configure_runtime", side_effect=SchemaError("cannot configure")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["migrate-legacy", "--source", str(source), "--database", str(target)])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("rerun:", stderr.getvalue())
        self.assertIn("python -m src.storage migrate-legacy --source", stderr.getvalue())
        self.assertIn(str(target), stderr.getvalue())
        self.assertTrue(target.exists())

    def test_missing_arguments_exit_nonzero(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            main(["init"])
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("the following arguments are required: --database", stderr.getvalue())

    def test_module_entry_point(self) -> None:
        path = self.temp_path()
        completed = subprocess.run(
            [sys.executable, "-m", "src.storage", "init", "--database", str(path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(path.exists())


class IdentityCommandTests(TempPathTestCase):
    def test_bootstrap_password_confirmation_and_existing_initialized_database(self):
        from src.identity.__main__ import main as identity_main
        from src.storage.management import initialize_database
        path = self.temp_path()
        initialize_database(path)
        arguments = ['bootstrap-admin', '--database', str(path), '--login-name', 'admin', '--display-name', 'Admin']
        with mock.patch('src.identity.__main__.getpass.getpass', side_effect=['a secure password 123', 'different password 123']):
            with redirect_stderr(io.StringIO()):
                self.assertEqual(identity_main(arguments), 1)
        with Database(path).transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        with mock.patch('src.identity.__main__.getpass.getpass', side_effect=['a secure password 123'] * 2) as prompt:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(identity_main(arguments), 0)
            self.assertEqual(prompt.call_count, 2)

    def test_identity_help_does_not_create_database_or_prompt(self):
        from src.identity.__main__ import main as identity_main
        path = self.temp_path('missing.sqlite')
        with mock.patch('src.identity.__main__.getpass.getpass') as prompt:
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                identity_main(['bootstrap-admin', '--database', str(path), '--help'])
            self.assertEqual(result.exception.code, 0)
            prompt.assert_not_called()
        self.assertFalse(path.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
