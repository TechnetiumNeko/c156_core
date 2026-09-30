"""Behaviour tests for the SQLite connection, transaction and schema boundary."""

from __future__ import annotations

import sqlite3
import unittest

from src.storage import (
    BusyError,
    ConstraintError,
    Database,
    SchemaError,
    StorageError,
    create_schema,
)
from tests.helpers import TempPathTestCase, create_schema_database

T = "2026-01-01T00:00:00+00:00"

_INSERT_ENTRY = (
    "INSERT INTO entries (workspace_id, branch_id, object_id, parent_id, name, "
    "position, version, current_revision_id, metadata_json, created_at, "
    "modified_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


class TestConnectionBoundaries(TempPathTestCase):
    def test_missing_runtime_database_is_not_created(self):
        missing = self.temp_path("missing.sqlite")
        with self.assertRaises(StorageError):
            with Database(missing).transaction():
                self.fail("缺失数据库不能被打开")
        self.assertFalse(missing.exists())

    def test_empty_future_and_nonwal_database_unchanged(self):
        # Zero-byte file: not a usable content database, must stay zero bytes.
        empty = self.temp_path("empty.sqlite")
        empty.touch()
        self.assertEqual(empty.read_bytes(), b"")
        with self.assertRaises(SchemaError):
            with Database(empty).transaction():
                self.fail("空文件不能被当作运行库")
        self.assertEqual(empty.read_bytes(), b"")

        # Future protocol version: rejected without rewriting the header.
        future = self.temp_path("future.sqlite")
        raw = sqlite3.connect(future)
        raw.execute("PRAGMA user_version = 99")
        raw.commit()
        raw.close()
        before = future.read_bytes()
        with self.assertRaises(SchemaError) as caught:
            with Database(future).transaction():
                self.fail("未来协议版本不能被打开")
        self.assertEqual(caught.exception.details.get("actual"), 99)
        self.assertEqual(future.read_bytes(), before)
        with sqlite3.connect(future) as check:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 99)

        # Non-WAL target: runtime open refuses and leaves journal mode alone.
        nonwal = self.temp_path("nonwal.sqlite")
        create_schema_database(nonwal)
        raw = sqlite3.connect(nonwal)
        self.assertEqual(
            str(raw.execute("PRAGMA journal_mode = DELETE").fetchone()[0]).lower(),
            "delete",
        )
        raw.close()
        before = nonwal.read_bytes()
        with self.assertRaises(SchemaError):
            with Database(nonwal).transaction():
                self.fail("非 WAL 目标不能被运行打开")
        self.assertEqual(nonwal.read_bytes(), before)
        with sqlite3.connect(nonwal) as check:
            self.assertEqual(
                str(check.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "delete",
            )

    def test_rollback_across_statements(self):
        path = create_schema_database(self.temp_path())
        with self.assertRaises(RuntimeError):
            with Database(path).transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO workspaces VALUES ('w1','W1',?)", (T,)
                )
                connection.execute(
                    "INSERT INTO workspaces VALUES ('w2','W2',?)", (T,)
                )
                raise RuntimeError("boom")
        with Database(path).transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM workspaces"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_connections_enable_foreign_keys(self):
        path = create_schema_database(self.temp_path())
        with Database(path).transaction() as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000
            )
            row = connection.execute("SELECT 1 AS one").fetchone()
            self.assertEqual(row["one"], 1)

    def test_foreign_keys_are_enforced_on_runtime_connections(self):
        path = create_schema_database(self.temp_path())
        with self.assertRaises(ConstraintError) as caught:
            with Database(path).transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO objects VALUES ('orphan','nowhere','folder',?)",
                    (T,),
                )
        self.assertEqual(caught.exception.constraint, "foreign_key")

    def test_busy_timeout_is_configurable(self):
        path = create_schema_database(self.temp_path())
        with Database(path, busy_timeout_ms=1234).transaction() as connection:
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0], 1234
            )

    def test_management_connection_allows_non_wal(self):
        path = create_schema_database(self.temp_path())
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.close()
        before = path.read_bytes()
        with Database(path).management_connection() as connection:
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "delete",
            )
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_configure_runtime_enables_wal(self):
        path = self.temp_path("runtime.sqlite")
        path.touch()
        with Database(path).management_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            create_schema(connection)
            connection.execute("COMMIT")
        Database(path).configure_runtime()
        with Database(path).transaction() as connection:
            self.assertEqual(
                str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "wal",
            )

    def test_create_schema_sets_protocol_version_one(self):
        path = self.temp_path("version.sqlite")
        path.touch()
        with Database(path).management_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            create_schema(connection)
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 1
            )
            connection.execute("COMMIT")


class TestSchemaAtomicity(TempPathTestCase):
    def test_failed_schema_creation_leaves_no_partial_protocol(self):
        path = self.temp_path("atomic.sqlite")
        path.touch()
        database = Database(path)
        with database.management_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            create_schema(connection)
            with self.assertRaises(sqlite3.OperationalError):
                # Second application in the same transaction must fail and the
                # rollback must remove the whole batch, version included.
                create_schema(connection)
            connection.execute("ROLLBACK")
        with database.management_connection() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], 0
            )
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        self.assertEqual(list(tables), [])


class TestSchemaConstraints(TempPathTestCase):
    def setUp(self):
        super().setUp()
        self.path = create_schema_database(self.temp_path())
        with Database(self.path).transaction(write=True) as connection:
            self._seed(connection)

    def _seed(self, connection):
        connection.execute("INSERT INTO workspaces VALUES ('w1','W1',?)", (T,))
        connection.execute("INSERT INTO workspaces VALUES ('w2','W2',?)", (T,))
        for object_id, workspace_id, kind in (
            ("root", "w1", "folder"),
            ("root2", "w1", "folder"),
            ("a", "w1", "folder"),
            ("c", "w1", "folder"),
            ("d", "w1", "document"),
            ("other", "w1", "document"),
            ("x", "w2", "folder"),
            ("y", "w2", "document"),
        ):
            connection.execute(
                "INSERT INTO objects VALUES (?,?,?,?)",
                (object_id, workspace_id, kind, T),
            )
        connection.execute(
            "INSERT INTO document_revisions VALUES ('r1','w1','d',NULL,'',?)", (T,)
        )
        connection.execute(
            "INSERT INTO document_revisions VALUES ('r2','w1','other',NULL,'',?)",
            (T,),
        )
        connection.execute(
            "INSERT INTO document_revisions VALUES ('r3','w2','y',NULL,'',?)", (T,)
        )
        connection.execute(
            "INSERT INTO branches VALUES ('b1','w1','main','root',?)", (T,)
        )
        connection.execute(
            "INSERT INTO branches VALUES ('b2','w2','main','x',?)", (T,)
        )
        self._insert_entry(connection, "w1", "b1", "root", None, "", 0, 1)
        self._insert_entry(connection, "w1", "b1", "a", "root", "a", 0, 1)
        self._insert_entry(connection, "w1", "b1", "d", "root", "d", 1, 1, "r1")
        self._insert_entry(connection, "w2", "b2", "x", None, "", 0, 1)

    @staticmethod
    def _insert_entry(
        connection,
        workspace_id,
        branch_id,
        object_id,
        parent_id,
        name,
        position,
        version,
        revision_id=None,
        deleted_at=None,
    ):
        connection.execute(
            _INSERT_ENTRY,
            (
                workspace_id,
                branch_id,
                object_id,
                parent_id,
                name,
                position,
                version,
                revision_id,
                "{}",
                T,
                T,
                deleted_at,
            ),
        )

    def _constraint_error(self, statement, parameters=()) -> ConstraintError:
        with self.assertRaises(ConstraintError) as caught:
            with Database(self.path).transaction(write=True) as connection:
                connection.execute(statement, parameters)
        return caught.exception

    def test_constraint_error_preserves_sqlite_cause_and_category(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "c", "root", "a", 2, 1, None, "{}", T, T, None),
        )
        self.assertIsInstance(error.__cause__, sqlite3.IntegrityError)
        self.assertEqual(error.details.get("constraint"), "unique")

    def test_busy_lock_is_mapped_to_busy_error(self):
        holder_cm = Database(self.path).management_connection()
        holder = holder_cm.__enter__()
        try:
            holder.execute("BEGIN IMMEDIATE")
            with self.assertRaises(BusyError):
                with Database(self.path, busy_timeout_ms=100).transaction(write=True):
                    self.fail("second writer must not acquire the held write lock")
        finally:
            holder.execute("ROLLBACK")
            holder_cm.__exit__(None, None, None)

    def test_rejects_duplicate_active_sibling_name(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "c", "root", "a", 2, 1, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "unique")

    def test_rejects_duplicate_active_sibling_position(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "c", "root", "c", 0, 1, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "unique")

    def test_rejects_second_active_root(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "root2", None, "", 0, 1, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "unique")

    def test_rejects_negative_position(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "c", "root", "c", -1, 1, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "check")

    def test_rejects_zero_version(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "c", "root", "c", 2, 0, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "check")

    def test_rejects_unknown_kind(self):
        error = self._constraint_error(
            "INSERT INTO objects VALUES ('k','w1','bogus',?)", (T,)
        )
        self.assertEqual(error.constraint, "check")

    def test_rejects_cross_workspace_object_reference(self):
        error = self._constraint_error(
            _INSERT_ENTRY,
            ("w1", "b1", "x", "root", "x", 2, 1, None, "{}", T, T, None),
        )
        self.assertEqual(error.constraint, "foreign_key")

    def test_rejects_cross_object_revision(self):
        error = self._constraint_error(
            "UPDATE entries SET current_revision_id = 'r2' WHERE object_id = 'd'"
        )
        self.assertEqual(error.constraint, "foreign_key")

    def test_rejects_cross_workspace_revision(self):
        error = self._constraint_error(
            "UPDATE entries SET current_revision_id = 'r3' WHERE object_id = 'd'"
        )
        self.assertEqual(error.constraint, "foreign_key")

    def test_revision_update_and_delete_are_rejected(self):
        update = self._constraint_error(
            "UPDATE document_revisions SET content = 'changed' WHERE id = 'r1'"
        )
        self.assertEqual(update.constraint, "trigger")
        delete = self._constraint_error(
            "DELETE FROM document_revisions WHERE id = 'r2'"
        )
        self.assertEqual(delete.constraint, "trigger")

    def test_object_identity_is_immutable(self):
        kind = self._constraint_error(
            "UPDATE objects SET kind = 'document' WHERE id = 'a'"
        )
        self.assertEqual(kind.constraint, "trigger")
        workspace = self._constraint_error(
            "UPDATE objects SET workspace_id = 'w2' WHERE id = 'a'"
        )
        self.assertEqual(workspace.constraint, "trigger")

    def test_soft_deleted_name_can_be_reused(self):
        with Database(self.path).transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET deleted_at = ?, version = 2 WHERE object_id = 'a'",
                (T,),
            )
            connection.execute(
                "INSERT INTO objects VALUES ('c2','w1','folder',?)", (T,)
            )
            connection.execute(
                _INSERT_ENTRY,
                ("w1", "b1", "c2", "root", "a", 0, 1, None, "{}", T, T, None),
            )
        with Database(self.path).transaction() as connection:
            rows = connection.execute(
                "SELECT object_id FROM entries WHERE workspace_id = 'w1' "
                "AND branch_id = 'b1' AND parent_id = 'root' "
                "AND deleted_at IS NULL ORDER BY position"
            ).fetchall()
        self.assertEqual([row["object_id"] for row in rows], ["c2", "d"])


if __name__ == "__main__":
    unittest.main()
