"""Behaviour tests for explicit initialization and default-tree validation."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from src.core import ContentScope, StorageBusy, UnsupportedSchema
from src.services import ContentService
from src.storage import Database
import src.storage.management as management_module
from src.storage.management import initialize_database, validate_default_tree
from src.storage.records import EntryRecord, RevisionRecord
from src.storage.repository import (
    Repository,
    insert_workspace,
    lookup_default_main,
)
from tests.helpers import FIXTURE_TIME, TempPathTestCase

_ENTRY_DUMP = (
    "SELECT workspace_id, branch_id, object_id, parent_id, name, position, version, "
    "current_revision_id, metadata_json, created_at, modified_at, deleted_at "
    "FROM entries ORDER BY branch_id, parent_id, position, object_id"
)


def _entries(path):
    with Database(path).transaction() as connection:
        return [tuple(row) for row in connection.execute(_ENTRY_DUMP).fetchall()]


class TestInitializeDatabase(TempPathTestCase):
    def test_post_commit_config_failure_retains_complete_database_and_recovers(self):
        from src.storage.errors import SchemaError
        path = self.temp_path()
        with mock.patch.object(Database, "configure_runtime", side_effect=SchemaError("configure failed")):
            with self.assertRaises(SchemaError):
                initialize_database(path)
        self.assertTrue(path.exists())
        with Database(path).management_connection() as connection:
            scope = validate_default_tree(connection)
            before = [tuple(row) for row in connection.execute(_ENTRY_DUMP)]
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(initialize_database(path), scope)
        self.assertEqual(_entries(path), before)

    def test_locked_existing_init_is_storage_busy(self):
        path = self.temp_path()
        initialize_database(path)
        with Database(path).management_connection() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                with mock.patch.object(management_module, "Database", lambda path: Database(path, busy_timeout_ms=10)):
                    with self.assertRaises(StorageBusy):
                        initialize_database(path)
            finally:
                connection.execute("ROLLBACK")

    def test_initialize_creates_protocol_default_tree_and_wal(self):
        path = self.temp_path()
        scope = initialize_database(path)

        self.assertTrue(path.exists())
        self.assertIsInstance(scope, ContentScope)
        with Database(path).management_connection() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "wal",
            )
            workspaces = connection.execute("SELECT id FROM workspaces").fetchall()
            self.assertEqual([row["id"] for row in workspaces], [scope.workspace_id])
            branch = connection.execute(
                "SELECT name, root_object_id FROM branches WHERE id = ?",
                (scope.branch_id,),
            ).fetchone()
            self.assertEqual(branch["name"], "main")
            self.assertEqual(branch["root_object_id"], scope.root_id)
            root = connection.execute(
                "SELECT e.parent_id, e.name, e.position, e.version, o.kind, e.deleted_at "
                "FROM entries AS e JOIN objects AS o ON o.id = e.object_id "
                "WHERE e.object_id = ?",
                (scope.root_id,),
            ).fetchone()
            self.assertIsNone(root["parent_id"])
            self.assertEqual(root["name"], "")
            self.assertEqual(root["position"], 0)
            self.assertEqual(root["version"], 1)
            self.assertEqual(root["kind"], "folder")
            self.assertIsNone(root["deleted_at"])
            tops = connection.execute(
                "SELECT e.name, e.position, e.version, o.kind, e.deleted_at "
                "FROM entries AS e JOIN objects AS o ON o.id = e.object_id "
                "WHERE e.parent_id = ? ORDER BY e.position",
                (scope.root_id,),
            ).fetchall()

        self.assertEqual(
            [row["name"] for row in tops], ["main", "admin", "resource", "bin"]
        )
        self.assertEqual([row["position"] for row in tops], [0, 1, 2, 3])
        self.assertEqual({row["kind"] for row in tops}, {"folder"})
        self.assertEqual({row["version"] for row in tops}, {1})
        self.assertEqual([row["deleted_at"] for row in tops], [None, None, None, None])

    def test_init_is_idempotent(self):
        path = self.temp_path()
        first = initialize_database(path)
        with Database(path).transaction(write=True) as connection:
            repo = Repository(
                connection, workspace_id=first.workspace_id, branch_id=first.branch_id
            )
            repo.insert_object("extra-doc", "document", FIXTURE_TIME)
            repo.insert_revision(
                RevisionRecord(
                    id="extra-rev",
                    workspace_id=first.workspace_id,
                    object_id="extra-doc",
                    parent_revision_id=None,
                    content="extra",
                    created_at=FIXTURE_TIME,
                )
            )
            main = repo.find_child(first.root_id, "main")
            repo.insert_entry(
                EntryRecord(
                    workspace_id=first.workspace_id,
                    branch_id=first.branch_id,
                    object_id="extra-doc",
                    kind="document",
                    parent_id=main.object_id,
                    name="extra",
                    position=0,
                    version=1,
                    current_revision_id="extra-rev",
                    metadata_json="{}",
                    created_at=FIXTURE_TIME,
                    modified_at=FIXTURE_TIME,
                    deleted_at=None,
                )
            )

        before = _entries(path)
        second = initialize_database(path)

        self.assertEqual(second, first)
        self.assertEqual(_entries(path), before)
        with Database(path).transaction() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM entries WHERE object_id = 'extra-doc'"
                ).fetchone()
            )

    def test_default_scope_is_main(self):
        path = self.temp_path()
        root_scope = initialize_database(path)
        service = ContentService(Database(path))

        cli_scope = service.default_scope()

        self.assertIsInstance(cli_scope, ContentScope)
        self.assertEqual(cli_scope.workspace_id, root_scope.workspace_id)
        self.assertEqual(cli_scope.branch_id, root_scope.branch_id)
        self.assertNotEqual(cli_scope.root_id, root_scope.root_id)
        with Database(path).transaction() as connection:
            main = connection.execute(
                "SELECT e.object_id FROM entries AS e WHERE e.parent_id = ? "
                "AND e.name = 'main' AND e.deleted_at IS NULL",
                (root_scope.root_id,),
            ).fetchone()
        self.assertEqual(cli_scope.root_id, main["object_id"])
        self.assertEqual(service.get_path(cli_scope, cli_scope.root_id), "/")
        self.assertEqual(service.get_path(root_scope, cli_scope.root_id), "/main")
        resolved = service.resolve_path(cli_scope, "~")
        self.assertEqual(resolved.id, cli_scope.root_id)
        self.assertEqual(resolved.path, "/")

    def test_existing_non_wal_database_is_reconfigured_without_content_change(self):
        path = self.temp_path()
        first = initialize_database(path)
        before = _entries(path)
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.close()

        second = initialize_database(path)

        self.assertEqual(second, first)
        self.assertEqual(_entries(path), before)
        with Database(path).transaction() as connection:
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "wal",
            )

    def test_empty_unknown_and_future_targets_are_not_overwritten(self):
        empty = self.temp_path("empty.sqlite")
        empty.touch()
        empty_before = empty.read_bytes()
        with self.assertRaises(UnsupportedSchema):
            initialize_database(empty)
        self.assertEqual(empty.read_bytes(), empty_before)

        unknown = self.temp_path("unknown.sqlite")
        raw = sqlite3.connect(unknown)
        raw.execute("CREATE TABLE junk (value TEXT)")
        raw.commit()
        raw.close()
        unknown_before = unknown.read_bytes()
        with self.assertRaises(UnsupportedSchema):
            initialize_database(unknown)
        self.assertEqual(unknown.read_bytes(), unknown_before)

        future = self.temp_path("future.sqlite")
        raw = sqlite3.connect(future)
        raw.execute("PRAGMA user_version = 99")
        raw.commit()
        raw.close()
        future_before = future.read_bytes()
        with self.assertRaises(UnsupportedSchema):
            initialize_database(future)
        self.assertEqual(future.read_bytes(), future_before)
        with sqlite3.connect(future) as check:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_invalid_default_tree_not_repaired(self):
        # Missing protected top-level folder: reject and change nothing.
        missing_path = self.temp_path("missing.sqlite")
        scope = initialize_database(missing_path)
        with Database(missing_path).transaction(write=True) as connection:
            connection.execute(
                "DELETE FROM entries WHERE name = 'bin' AND parent_id = ?",
                (scope.root_id,),
            )
        missing_before = _entries(missing_path)
        with self.assertRaises(UnsupportedSchema):
            initialize_database(missing_path)
        self.assertEqual(_entries(missing_path), missing_before)

        # Branch root no longer matches the actual root entry: reject unchanged.
        wrong_path = self.temp_path("wrong-root.sqlite")
        wrong_scope = initialize_database(wrong_path)
        with Database(wrong_path).transaction(write=True) as connection:
            connection.execute(
                "UPDATE branches SET root_object_id = ("
                "SELECT object_id FROM entries WHERE parent_id = ? AND name = 'admin'"
                ") WHERE id = ?",
                (wrong_scope.root_id, wrong_scope.branch_id),
            )
        wrong_before = _entries(wrong_path)
        with self.assertRaises(UnsupportedSchema):
            initialize_database(wrong_path)
        self.assertEqual(_entries(wrong_path), wrong_before)

    def test_validate_default_tree_accepts_and_rejects(self):
        path = self.temp_path()
        scope = initialize_database(path)
        with Database(path).management_connection() as connection:
            self.assertEqual(validate_default_tree(connection), scope)

        with Database(path).transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET deleted_at = ? WHERE name = 'main' AND parent_id = ?",
                (FIXTURE_TIME, scope.root_id),
            )
        with Database(path).management_connection() as connection:
            with self.assertRaises(UnsupportedSchema):
                validate_default_tree(connection)

    def test_lookup_default_main_rejects_multiple_workspaces(self):
        path = self.temp_path()
        scope = initialize_database(path)
        with Database(path).management_connection() as connection:
            self.assertEqual(
                lookup_default_main(connection),
                (scope.workspace_id, scope.branch_id),
            )
            insert_workspace(connection, "second", "Second", FIXTURE_TIME)
        with Database(path).management_connection() as connection:
            with self.assertRaises(UnsupportedSchema):
                lookup_default_main(connection)

    def test_first_time_failure_removes_only_its_own_artifact(self):
        path = self.temp_path("broken.sqlite")
        with mock.patch.object(
            management_module, "insert_branch", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                initialize_database(path)
        self.assertFalse(path.exists())
        for suffix in ("-journal", "-wal", "-shm"):
            self.assertFalse(Path(str(path) + suffix).exists())


if __name__ == "__main__":
    unittest.main()
