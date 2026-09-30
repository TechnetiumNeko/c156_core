"""Shared test helpers.

Only test infrastructure lives here: temporary directories, schema-only
database fixtures, sample-tree copies and small counting utilities.  Helpers
never touch the tracked legacy sample under ``data/``.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from src.storage import Database, create_schema

__all__ = [
    "PROJECT_ROOT",
    "SAMPLE_DATA",
    "SAMPLE_DOCUMENT_ID",
    "TempPathTestCase",
    "copy_sample_data",
    "create_schema_database",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = PROJECT_ROOT / "data"
SAMPLE_DOCUMENT_ID = "8b168849-1ab3-4542-9828-f2e2120f2d57"


class TempPathTestCase(unittest.TestCase):
    """Base test case giving each test an isolated, auto-cleaned temp dir."""

    def setUp(self) -> None:
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_root = Path(self._temp_dir.name)

    def temp_path(self, name: str = "c156.sqlite") -> Path:
        return self.temp_root / name


def create_schema_database(path: Path) -> Path:
    """Create an empty schema-v1 database configured for WAL.

    No default tree is inserted: this is the storage-only fixture used by the
    database and repository tests.  ``initialize_database`` owns the tree.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    database = Database(path)
    with database.management_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            create_schema(connection)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    database.configure_runtime()
    return path


def copy_sample_data(destination: Path) -> Path:
    """Copy the tracked legacy sample tree to *destination* for read-only use."""

    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SAMPLE_DATA, destination)
    return destination
