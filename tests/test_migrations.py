"""Real SQLite evidence for migration takeover, provenance and transaction safety."""
from contextlib import closing
import sqlite3
from unittest import mock

from src.storage import Database, create_schema, validate_schema, upgrade_database
from src.storage import migrations
from src.storage.backup import backup_database
from src.storage.errors import SchemaError
from tests.helpers import TempPathTestCase, _seed_repository_rows


class MigrationTests(TempPathTestCase):
    def baseline(self, name="old.sqlite"):
        path = self.temp_path(name)
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN")
            migrations._upgrade(connection, "base", migrations.BASELINE_REVISION)
            connection.execute("DROP TABLE alembic_version")
            _seed_repository_rows(connection)
            connection.execute("COMMIT")
        return path

    def test_create_schema_respects_caller_rollback(self):
        path = self.temp_path()
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(RuntimeError, "business failed"):
                try:
                    create_schema(connection)
                    self.assertTrue(connection.in_transaction)
                    connection.execute("INSERT INTO workspaces VALUES ('w','name','now')")
                    self.assertEqual(validate_schema(connection), migrations.HEAD_REVISION)
                    raise RuntimeError("business failed")
                finally:
                    connection.execute("ROLLBACK")
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_upgrade_preserves_content_and_constraints_and_is_idempotent(self):
        path = self.baseline()
        with closing(sqlite3.connect(path)) as connection:
            old_revisions = connection.execute("SELECT * FROM document_revisions ORDER BY id").fetchall()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute("UPDATE document_revisions SET content='bad'")
        self.assertEqual(upgrade_database(path), migrations.HEAD_REVISION)
        self.assertEqual(upgrade_database(path), migrations.HEAD_REVISION)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(connection.execute("SELECT id,workspace_id,object_id,parent_revision_id,content,created_at FROM document_revisions ORDER BY id").fetchall(), old_revisions)
            self.assertEqual(connection.execute("SELECT DISTINCT actor_id,source_kind,restored_from_revision_id FROM document_revisions").fetchall(), [(None, 'unknown', None)])
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for sql in ("UPDATE document_revisions SET content='bad'", "UPDATE document_revisions SET source_kind='save'", "DELETE FROM document_revisions"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    connection.execute(sql)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE entries SET current_revision_id='rev2' WHERE object_id='doc1'")

    def test_baseline_rejects_damaged_constraints(self):
        alterations = {
            "trigger": "DROP TRIGGER trg_document_revisions_no_update",
            "partial_index": "DROP INDEX idx_entries_active_sibling_name",
            "check": "UPDATE sqlite_master SET sql=replace(sql, \"CHECK(status IN ('invited','active','reset_required','disabled'))\", '') WHERE name='users'",
            "foreign_key": "UPDATE sqlite_master SET sql=replace(sql, 'REFERENCES users(id)', '') WHERE name='sessions'",
        }
        for name, damage in alterations.items():
            with self.subTest(name=name):
                path = self.baseline(name + '.sqlite')
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("PRAGMA writable_schema=ON")
                    connection.execute(damage)
                    connection.commit()
                before = path.read_bytes()
                with self.assertRaises(SchemaError):
                    upgrade_database(path)
                self.assertEqual(path.read_bytes(), before)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchall(), [])

    def test_unrecognized_sqlite_named_objects_reject_takeover_and_backup(self):
        additions = {
            "table": "CREATE TABLE sqliteXunrecognized (value TEXT)",
            "trigger": "CREATE TRIGGER sqliteXunrecognized AFTER INSERT ON workspaces BEGIN SELECT 1; END",
        }
        for kind, statement in additions.items():
            with self.subTest(kind=kind):
                path = self.baseline(kind + '-extra.sqlite')
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute(statement)
                    connection.commit()
                before = path.read_bytes()
                with closing(sqlite3.connect(path)) as connection:
                    with self.assertRaises(SchemaError):
                        migrations.validate_backup_schema(connection)
                with self.assertRaises(SchemaError):
                    upgrade_database(path)
                target = self.temp_path(kind + '-backup.sqlite')
                with self.assertRaises(SchemaError):
                    backup_database(path, target)
                self.assertFalse(target.exists())
                self.assertEqual(path.read_bytes(), before)

    def test_baseline_rejects_broken_foreign_key_data(self):
        path = self.baseline()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE entries SET current_revision_id='missing' WHERE object_id='doc1'")
            connection.commit()
        with self.assertRaisesRegex(SchemaError, 'foreign key'):
            upgrade_database(path)

    def test_upgrade_failure_rolls_back_schema_and_revision(self):
        path = self.baseline()
        original = migrations._upgrade
        def fail_after_migration(connection, start, end):
            original(connection, start, end)
            if start == migrations.BASELINE_REVISION:
                connection.execute("INSERT INTO workspaces VALUES ('rollback','Rollback','now')")
                raise RuntimeError("injected after DDL and version update")
        with mock.patch.object(migrations, '_upgrade', side_effect=fail_after_migration):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                upgrade_database(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            self.assertEqual(migrations.validate_backup_schema(connection), migrations.BASELINE_REVISION)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT content FROM document_revisions WHERE id='rev1a'").fetchone()[0], 'alpha')
            self.assertNotIn('actor_id', [row[1] for row in connection.execute('PRAGMA table_info(document_revisions)')])
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchall(), [])
            self.assertEqual(connection.execute("SELECT id FROM workspaces WHERE id='rollback'").fetchall(), [])
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                connection.execute("DELETE FROM document_revisions")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE entries SET current_revision_id='rev2' WHERE object_id='doc1'")

    def test_upgrade_blocks_protocol2_writer_and_runtime_never_upgrades(self):
        path = self.baseline()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        with self.assertRaises(SchemaError):
            with Database(path).transaction(write=True):
                self.fail('old database accepted by runtime')
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
        upgrade_database(path)
        with closing(sqlite3.connect(path)) as connection:
            # Actual old Database._require_runtime accepts exactly integer 2.
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertNotEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(validate_schema(connection), migrations.HEAD_REVISION)
            connection.execute("UPDATE alembic_version SET version_num='future'")
            with self.assertRaises(SchemaError):
                validate_schema(connection)

    def test_source_and_receipt_constraints(self):
        path = self.baseline()
        upgrade_database(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute("INSERT INTO users VALUES ('actor','login','Name','active',0,1,1,'now','now')")
            connection.execute("INSERT INTO users VALUES ('other','other','Other','active',0,1,1,'now','now')")
            revision_sql = "INSERT INTO document_revisions (id,workspace_id,object_id,content,created_at,actor_id,source_kind,restored_from_revision_id) VALUES (?,'w1','doc1','restored','now',?,?,?)"
            connection.execute(revision_sql, ('restored','actor','restore','rev1a'))
            for values in [('cross-object','actor','restore','reva'), ('cross-workspace','actor','restore','rev2'), ('bad-kind','actor','invalid',None), ('bad-actor','missing','save',None)]:
                with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(revision_sql, values)
            connection.execute("INSERT INTO access_rules VALUES ('w1','b1','doc1','role','reader',NULL,'history_read','allow')")
            receipt_sql = "INSERT INTO document_operations VALUES (?,?, 'w1',?, 'doc1',?,?,?,?,'now')"
            connection.execute(receipt_sql, ('actor','op','b1','restore','digest','restored',1))
            connection.execute(receipt_sql, ('other','op','b1','save','digest','rev1a',0))
            for values in [('actor','op','b1','save','other','rev1a',1), ('actor','cross-object','b1','save','digest','reva',1), ('actor','cross-workspace','b1','save','digest','rev2',1), ('actor','bad-branch','b2','save','digest','rev1a',1), ('actor','bad-type','b1','delete','digest','rev1a',1), ('actor','bad-changed','b1','save','digest','rev1a',2)]:
                with self.subTest(values=values), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(receipt_sql, values)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_backup_reads_protocol2_without_upgrading_source(self):
        path = self.baseline()
        before = path.read_bytes()
        target = backup_database(path, self.temp_path('snapshot.sqlite'))
        self.assertEqual(path.read_bytes(), before)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(migrations.validate_backup_schema(connection), migrations.BASELINE_REVISION)
            self.assertEqual(connection.execute("SELECT content FROM document_revisions WHERE id='rev1a'").fetchone()[0], 'alpha')
        self.assertEqual(upgrade_database(target), migrations.HEAD_REVISION)

    def test_empty_upgrade_and_unknown_database_preservation(self):
        path = self.temp_path()
        path.touch()
        self.assertEqual(upgrade_database(path), migrations.HEAD_REVISION)
        unknown = self.temp_path('unknown.sqlite')
        with closing(sqlite3.connect(unknown)) as connection:
            connection.execute('CREATE TABLE unknown(value)')
            connection.execute('PRAGMA user_version=2')
        before = unknown.read_bytes()
        with self.assertRaises(SchemaError):
            upgrade_database(unknown)
        self.assertEqual(unknown.read_bytes(), before)
        missing = self.temp_path('missing.sqlite')
        with self.assertRaises(sqlite3.OperationalError):
            upgrade_database(missing)
        self.assertFalse(missing.exists())
