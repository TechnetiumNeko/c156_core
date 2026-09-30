"""Task 9: atomic legacy import, repeat-import recovery and publication.

Every test starts from a fresh copy of the tracked ``data/`` sample; the
repository originals are never opened for writing.  Time, metadata, content,
order and identity are checked against an independent read-only scan.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from src.core.errors import MigrationError, StorageBusy, UnsupportedSchema
from src.services import ContentService
from src.storage import Database, SchemaError
from src.storage.legacy import migrate_legacy, scan_legacy, source_fingerprint
import src.storage.legacy as legacy_module
import src.storage.publication as publication_module
from src.storage.publication import publish_no_replace
from tests.helpers import (
    LEGACY_SAMPLE_DOCUMENT_CONTENT,
    LEGACY_SAMPLE_DOCUMENT_SHA256,
    PROJECT_ROOT,
    SAMPLE_DOCUMENT_ID,
    TempPathTestCase,
    copy_sample_data,
    create_legacy_document,
    legacy_connection,
    legacy_object_id,
)

READ_SCAN_AT = "2026-10-01T00:00:00+00:00"
SAMPLED_MODIFIED_AT_UTC = "2026-09-28T07:20:17.134659+00:00"
_TABLES = (
    "workspaces",
    "objects",
    "branches",
    "entries",
    "document_revisions",
    "legacy_imports",
)


class LegacyImportTestCase(TempPathTestCase):
    """Base case: an isolated copy of the tracked legacy sample."""

    def setUp(self) -> None:
        super().setUp()
        self.source = copy_sample_data(self.temp_root / "data")
        self.target = self.temp_root / "c156.sqlite"

    # -- helpers -----------------------------------------------------------

    def raw_counts(self, path: Path) -> dict:
        connection = sqlite3.connect(path)
        try:
            return {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in _TABLES
            }
        finally:
            connection.close()

    def source_hashes(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in sorted(self.source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.source).as_posix()
            if (
                relative == "c156.sqlite"
                or relative.startswith("c156.sqlite-")
                or relative.startswith("c156.sqlite.migrate-")
            ):
                continue
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def temp_leftovers(self) -> list[Path]:
        return sorted(self.temp_root.rglob("*.migrate-*.tmp*"))


class SampleImportTests(LegacyImportTestCase):
    def test_import_maps_objects_content_metadata_and_cli_path(self) -> None:
        report = migrate_legacy(self.source, self.target)

        self.assertEqual(report["counts"]["objects"], 8)
        self.assertEqual(report["counts"]["folders"], 7)
        self.assertEqual(report["counts"]["documents"], 1)
        self.assertEqual(report["counts"]["repairs"], 0)
        self.assertEqual(len(report["objects"]), 8)

        counts = self.raw_counts(self.target)
        self.assertEqual(counts["workspaces"], 1)
        self.assertEqual(counts["objects"], 8)
        self.assertEqual(counts["branches"], 1)
        self.assertEqual(counts["entries"], 8)
        self.assertEqual(counts["document_revisions"], 1)
        self.assertEqual(counts["legacy_imports"], 1)

        service = ContentService(Database(self.target))
        scope = service.default_scope()
        node = service.resolve_path(scope, "/products/concretecream")
        self.assertEqual(node.id, SAMPLE_DOCUMENT_ID)
        self.assertEqual(node.path, "/products/concretecream")
        document = service.read_document(scope, SAMPLE_DOCUMENT_ID)
        self.assertEqual(document.content, LEGACY_SAMPLE_DOCUMENT_CONTENT)
        self.assertEqual(
            hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
            LEGACY_SAMPLE_DOCUMENT_SHA256,
        )
        self.assertEqual(document.modified_at, SAMPLED_MODIFIED_AT_UTC)

    def test_import_equivalence_with_independent_scan(self) -> None:
        scan = scan_legacy(
            self.source, target=self.target, imported_at=READ_SCAN_AT
        )
        report = migrate_legacy(self.source, self.target)
        self.assertEqual(report["source_digest"], scan.source_digest)
        self.assertEqual(
            [item["id"] for item in report["objects"]],
            [item.id for item in scan.objects],
        )
        for expected, actual in zip(scan.report["objects"], report["objects"]):
            for key in (
                "id",
                "kind",
                "fullpath",
                "parent_id",
                "name",
                "position",
                "metadata",
                "content_length",
                "content_sha256",
            ):
                self.assertEqual(actual[key], expected[key], key)

        content_by_id = {
            item.id: item.content for item in scan.objects
        }
        order_by_id = {item.id: item.position for item in scan.objects}
        with Database(self.target).transaction() as connection:
            rows = {
                row["object_id"]: row
                for row in connection.execute(
                    "SELECT e.object_id, e.parent_id, e.name, e.position, "
                    "e.version, e.current_revision_id, e.metadata_json, "
                    "e.created_at, e.modified_at, e.deleted_at, o.kind "
                    "FROM entries AS e JOIN objects AS o "
                    "ON o.workspace_id = e.workspace_id AND o.id = e.object_id"
                )
            }
            for item in report["objects"]:
                row = rows[item["id"]]
                self.assertEqual(row["kind"], item["kind"])
                self.assertEqual(row["parent_id"], item["parent_id"])
                self.assertEqual(row["name"], item["name"])
                self.assertEqual(row["position"], order_by_id[item["id"]])
                self.assertEqual(row["version"], 1)
                self.assertIsNone(row["deleted_at"])
                self.assertEqual(row["created_at"], item["created_at"])
                self.assertEqual(row["modified_at"], item["modified_at"])
                self.assertEqual(
                    json.loads(row["metadata_json"]), item["metadata"]
                )
                if item["kind"] == "document":
                    revision = connection.execute(
                        "SELECT content, parent_revision_id, created_at "
                        "FROM document_revisions WHERE object_id = ?",
                        (item["id"],),
                    ).fetchone()
                    self.assertIsNotNone(revision)
                    self.assertEqual(
                        revision["content"], content_by_id[item["id"]]
                    )
                    self.assertIsNone(revision["parent_revision_id"])
                    self.assertEqual(
                        revision["created_at"], report["imported_at"]
                    )

    def test_import_preserves_crlf_and_extension_metadata(self) -> None:
        raw = "line1\r\nline2\r\n"
        payload = {"nested": [1, True, None], "text": "值"}
        with legacy_connection(
            self.source / "main/products/concretecream", write=True
        ) as connection:
            connection.execute(
                "UPDATE document_content SET content = ?", (raw,)
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES ('extra', ?)",
                (json.dumps(payload, ensure_ascii=False),),
            )
        report = migrate_legacy(self.source, self.target)
        entry = next(
            item
            for item in report["objects"]
            if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["metadata"]["extra"], payload)
        self.assertEqual(entry["raw_metadata"]["extra"], json.dumps(payload, ensure_ascii=False))

        service = ContentService(Database(self.target))
        scope = service.default_scope()
        document = service.read_document(scope, SAMPLE_DOCUMENT_ID)
        self.assertEqual(document.content, raw)
        self.assertEqual(
            service.get_metadata(scope, SAMPLE_DOCUMENT_ID)["extra"], payload
        )

    def test_import_reports_and_applies_missing_registration_repair(self) -> None:
        document_id = legacy_object_id(
            self.source / "main/products/concretecream"
        )
        with legacy_connection(
            self.source / "main/products/.folder", write=True
        ) as connection:
            connection.execute(
                'DELETE FROM "file" WHERE child_id = ?', (document_id,)
            )
        report = migrate_legacy(self.source, self.target)
        self.assertEqual(report["counts"]["repairs"], 1)
        self.assertEqual(report["repairs"][0]["child_id"], document_id)
        with Database(self.target).transaction() as connection:
            row = connection.execute(
                "SELECT position FROM entries WHERE object_id = ?",
                (document_id,),
            ).fetchone()
        self.assertEqual(row["position"], 0)

    def test_import_reports_time_fallbacks(self) -> None:
        report = migrate_legacy(self.source, self.target)
        document = next(
            item
            for item in report["objects"]
            if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(document["created_at"], report["imported_at"])
        self.assertEqual(document["created_at_reason"], "missing")
        self.assertEqual(document["modified_at"], SAMPLED_MODIFIED_AT_UTC)
        self.assertEqual(document["modified_at_reason"], "stored")
        self.assertEqual(report["counts"]["time_fallbacks"], 15)

    def test_source_bytes_are_never_modified(self) -> None:
        before = self.source_hashes()
        migrate_legacy(self.source, self.target)
        self.assertEqual(self.source_hashes(), before)
        self.assertEqual(list(self.source.rglob("*-wal")), [])
        self.assertEqual(list(self.source.rglob("*-shm")), [])
        self.assertEqual(list(self.source.rglob("*-journal")), [])


class RepeatImportTests(LegacyImportTestCase):
    def test_repeat_import_preserves_later_edit(self) -> None:
        report = migrate_legacy(self.source, self.target)
        service = ContentService(Database(self.target))
        scope = service.default_scope()
        document = service.read_document(scope, SAMPLE_DOCUMENT_ID)
        saved = service.save_document(
            scope,
            document.id,
            "迁移后修改",
            expected_revision_id=document.revision_id,
        )
        counts_before = self.raw_counts(self.target)
        self.assertEqual(migrate_legacy(self.source, self.target), report)
        self.assertEqual(service.read_document(scope, document.id), saved)
        self.assertEqual(self.raw_counts(self.target), counts_before)

    def test_repeat_import_reconfigures_wal_without_content_change(self) -> None:
        report = migrate_legacy(self.source, self.target)
        raw = sqlite3.connect(self.target)
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.close()
        self.assertEqual(migrate_legacy(self.source, self.target), report)
        with Database(self.target).transaction() as connection:
            self.assertEqual(
                str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
                "wal",
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0],
                8,
            )

    def test_different_source_is_rejected_untouched(self) -> None:
        migrate_legacy(self.source, self.target)
        before = self._entries(self.target)
        with legacy_connection(
            self.source / "main/products/concretecream", write=True
        ) as connection:
            connection.execute(
                "UPDATE document_content SET content = 'changed source'"
            )
        with self.assertRaises(MigrationError):
            migrate_legacy(self.source, self.target)
        self.assertEqual(self._entries(self.target), before)

    def test_unknown_existing_target_is_rejected_untouched(self) -> None:
        from src.storage.management import initialize_database

        initialize_database(self.target)
        before = self._entries(self.target)
        with self.assertRaises(MigrationError):
            migrate_legacy(self.source, self.target)
        self.assertEqual(self._entries(self.target), before)

    def test_corrupt_import_record_rejected_before_configuration(self):
        report = migrate_legacy(self.source, self.target)
        for changes in (
            {"source_digest": "wrong"},
            {"imported_at": "invalid-time"},
            {"counts": {"objects": 999, "documents": 1}},
            {"extra": float("nan")},
        ):
            with self.subTest(changes=changes):
                invalid = dict(report, **changes)
                with Database(self.target).management_connection() as connection:
                    connection.execute("UPDATE legacy_imports SET report_json = ?", (json.dumps(invalid),))
                with mock.patch.object(Database, "configure_runtime") as configure:
                    with self.assertRaises(MigrationError):
                        migrate_legacy(self.source, self.target)
                configure.assert_not_called()

    def test_unreachable_entry_rejected_without_target_change(self):
        migrate_legacy(self.source, self.target)
        with Database(self.target).management_connection() as connection:
            connection.execute("UPDATE entries SET parent_id = object_id, position = 99 WHERE name = 'products'")
        before = self.target.read_bytes()
        with mock.patch.object(Database, "configure_runtime") as configure:
            with self.assertRaises(MigrationError):
                migrate_legacy(self.source, self.target)
        configure.assert_not_called()
        self.assertEqual(self.target.read_bytes(), before)

    def _assert_invalid_target_rejected_unchanged(self, target):
        before = target.read_bytes()
        with mock.patch.object(Database, "configure_runtime") as configure:
            with self.assertRaises(MigrationError) as caught:
                migrate_legacy(self.source, target)
        configure.assert_not_called()
        self.assertEqual(caught.exception.details["phase"], "validate_target")
        self.assertIn("reason", caught.exception.details)
        self.assertEqual(target.read_bytes(), before)
        with Database(target).management_connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(Path(str(target) + "-journal").exists())
        self.assertFalse(Path(str(target) + "-wal").exists())

    def test_recovery_rejects_document_without_current_revision(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                target = self.temp_root / f"missing-revision-{deleted}.sqlite"
                migrate_legacy(self.source, target)
                with Database(target).management_connection() as connection:
                    connection.execute("PRAGMA journal_mode = DELETE")
                    connection.execute(
                        "UPDATE entries SET current_revision_id = NULL, deleted_at = ? WHERE object_id = ?",
                        (READ_SCAN_AT if deleted else None, SAMPLE_DOCUMENT_ID),
                    )
                self._assert_invalid_target_rejected_unchanged(target)

    def test_recovery_rejects_revision_from_other_object_or_folder_revision(self):
        for folder in (False, True):
            with self.subTest(folder=folder):
                target = self.temp_root / f"wrong-revision-{folder}.sqlite"
                migrate_legacy(self.source, target)
                service = ContentService(Database(target))
                scope = service.default_scope()
                other = service.create_document(scope, scope.root_id, "other", content="body")
                connection = sqlite3.connect(target, isolation_level=None)
                try:
                    connection.execute("PRAGMA journal_mode = DELETE")
                    object_id = scope.root_id if folder else SAMPLE_DOCUMENT_ID
                    revision_id = other.revision_id
                    if folder:
                        # Even a revision matching the folder's workspace/object
                        # passes the FK but must fail the content type invariant.
                        revision_id = "00000000-0000-4000-8000-000000000009"
                        connection.execute(
                            "INSERT INTO document_revisions (id, workspace_id, object_id, content, created_at) VALUES (?,?,?,?,?)",
                            (revision_id, scope.workspace_id, object_id, "invalid folder body", READ_SCAN_AT),
                        )
                    connection.execute("UPDATE entries SET current_revision_id = ? WHERE object_id = ?",
                                       (revision_id, object_id))
                finally:
                    connection.close()
                self._assert_invalid_target_rejected_unchanged(target)

    def test_recovery_rejects_invalid_extension_metadata_on_retained_entries(self):
        invalid_values = ('[]', '{"nested":{"value":NaN}}', '{"nested":[Infinity]}',
                          '{"version":1}', '{"nested":{"value":1e999}}')
        for deleted in (False, True):
            for index, payload in enumerate(invalid_values):
                with self.subTest(deleted=deleted, payload=payload):
                    target = self.temp_root / f"metadata-{deleted}-{index}.sqlite"
                    migrate_legacy(self.source, target)
                    with Database(target).management_connection() as connection:
                        connection.execute("PRAGMA journal_mode = DELETE")
                        connection.execute("UPDATE entries SET metadata_json = ?, deleted_at = ? WHERE object_id = ?",
                                           (payload, READ_SCAN_AT if deleted else None, SAMPLE_DOCUMENT_ID))
                    self._assert_invalid_target_rejected_unchanged(target)

    def test_repeat_import_preserves_legal_edits_metadata_and_soft_deletion(self):
        report = migrate_legacy(self.source, self.target)
        service = ContentService(Database(self.target))
        scope = service.default_scope()
        doc = service.read_document(scope, SAMPLE_DOCUMENT_ID)
        saved = service.save_document(scope, doc.id, "later edit", expected_revision_id=doc.revision_id)
        updated = service.set_metadata(scope, doc.id, {"nested": {"values": [1, None, True]}},
                                       expected_version=saved.version)
        service.delete_node(scope, doc.id, expected_version=updated.version)
        with Database(self.target).management_connection() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            before = [tuple(row) for row in connection.execute("SELECT * FROM entries ORDER BY object_id")]
            revisions = [tuple(row) for row in connection.execute("SELECT * FROM document_revisions ORDER BY id")]
        self.assertEqual(migrate_legacy(self.source, self.target), report)
        with Database(self.target).transaction() as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM entries ORDER BY object_id")], before)
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM document_revisions ORDER BY id")], revisions)

    def test_locked_target_raises_storage_busy(self) -> None:
        migrate_legacy(self.source, self.target)
        raw = sqlite3.connect(self.target)
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.execute("BEGIN IMMEDIATE")
        try:
            with mock.patch.object(
                legacy_module,
                "Database",
                lambda path: Database(path, busy_timeout_ms=50),
            ):
                with self.assertRaises(StorageBusy):
                    migrate_legacy(self.source, self.target)
        finally:
            raw.execute("ROLLBACK")
            raw.close()

    def test_exclusive_locked_recovery_is_storage_busy(self):
        migrate_legacy(self.source, self.target)
        with Database(self.target).management_connection() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                with mock.patch.object(legacy_module, "Database", lambda path: Database(path, busy_timeout_ms=10)):
                    with self.assertRaises(StorageBusy):
                        migrate_legacy(self.source, self.target)
            finally:
                connection.execute("ROLLBACK")

    @staticmethod
    def _entries(path: Path) -> list[tuple]:
        connection = sqlite3.connect(path)
        try:
            return [
                tuple(row)
                for row in connection.execute(
                    "SELECT object_id, parent_id, name, position, "
                    "current_revision_id FROM entries "
                    "ORDER BY branch_id, parent_id, position, object_id"
                )
            ]
        finally:
            connection.close()


class PublicationAndRecoveryTests(LegacyImportTestCase):
    def test_publish_no_replace_leaves_existing_target(self) -> None:
        temporary = self.temp_root / "build.tmp"
        temporary.write_bytes(b"new")
        publish_no_replace(temporary, self.target)
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertFalse(temporary.exists())

        second = self.temp_root / "build2.tmp"
        second.write_bytes(b"other")
        with self.assertRaises(FileExistsError):
            publish_no_replace(second, self.target)
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertTrue(second.exists())

    def test_published_configure_failure_keeps_target_and_rerun_recovers(
        self,
    ) -> None:
        with mock.patch.object(
            Database,
            "configure_runtime",
            side_effect=SchemaError("cannot configure"),
        ):
            with self.assertRaises(MigrationError) as caught:
                migrate_legacy(self.source, self.target)
        self.assertEqual(
            caught.exception.details.get("phase"), "configure_runtime"
        )
        self.assertTrue(self.target.exists())
        counts = self.raw_counts(self.target)
        self.assertEqual(counts["objects"], 8)

        report = migrate_legacy(self.source, self.target)
        self.assertEqual(report["counts"]["objects"], 8)
        self.assertEqual(self.raw_counts(self.target)["objects"], 8)

    def test_process_exit_after_publish_recovers_on_rerun(self) -> None:
        script = (
            "import os\n"
            "from src.storage import database as _database\n"
            "from src.storage.legacy import migrate_legacy\n"
            "_database.Database.configure_runtime = lambda self: os._exit(9)\n"
            "migrate_legacy(%r, %r)\n"
            % (str(self.source), str(self.target))
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 9, completed.stderr)
        self.assertTrue(self.target.exists())
        self.assertEqual(self.raw_counts(self.target)["objects"], 8)

        report = migrate_legacy(self.source, self.target)
        self.assertEqual(report["counts"]["objects"], 8)
        with Database(self.target).transaction() as connection:
            self.assertEqual(
                str(
                    connection.execute("PRAGMA journal_mode").fetchone()[0]
                ).lower(),
                "wal",
            )

    def test_competing_migration_processes_leave_valid_target(self) -> None:
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "import src.storage.legacy as legacy\n"
            "original = legacy.publish_no_replace\n"
            "def publish(temporary, target):\n"
            "    Path(sys.argv[3]).touch()\n"
            "    deadline = time.monotonic() + 10\n"
            "    while not Path(sys.argv[4]).exists():\n"
            "        if time.monotonic() > deadline: raise RuntimeError('barrier timeout')\n"
            "        time.sleep(0.01)\n"
            "    original(temporary, target)\n"
            "legacy.publish_no_replace = publish\n"
            "legacy.migrate_legacy(sys.argv[1], sys.argv[2])\n"
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.source), str(self.target),
                 str(self.temp_root / f"publisher-{index}"),
                 str(self.temp_root / f"publisher-{1-index}")],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(2)
        ]
        outputs = [process.communicate(timeout=20) for process in processes]
        codes = [process.returncode for process in processes]
        self.assertEqual(codes, [0, 0], outputs)
        counts = self.raw_counts(self.target)
        self.assertEqual(counts["objects"], 8)
        self.assertEqual(counts["entries"], 8)
        self.assertEqual(counts["document_revisions"], 1)
        self.assertEqual(counts["legacy_imports"], 1)
        report = migrate_legacy(self.source, self.target)
        self.assertEqual(
            report["source_digest"],
            source_fingerprint(self.source, target=self.target),
        )
        self.assertEqual(self.temp_leftovers(), [])

    def test_file_fsync_failure_prevents_publication(self):
        temporary = self.temp_root / "build.tmp"
        temporary.write_bytes(b"complete")
        with mock.patch.object(publication_module.os, "fsync", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                publish_no_replace(temporary, self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue(temporary.exists())

    def test_existing_future_schema_rejected_before_wal_change(self):
        migrate_legacy(self.source, self.target)
        with Database(self.target).management_connection() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA user_version = 99")
        before = self.target.read_bytes()
        with mock.patch.object(Database, "configure_runtime") as configure:
            with self.assertRaises(UnsupportedSchema):
                migrate_legacy(self.source, self.target)
        configure.assert_not_called()
        self.assertEqual(self.target.read_bytes(), before)

    def test_recovery_rejects_invalid_source_directory_set(self):
        migrate_legacy(self.source, self.target)
        (self.source / "unregistered-empty").mkdir()
        before = self.target.read_bytes()
        with self.assertRaises(MigrationError):
            migrate_legacy(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), before)

    def test_object_timestamp_mismatch_aborts_before_publication(self):
        original = legacy_module._populate
        def populate(connection, scan, imported_at):
            result = original(connection, scan, imported_at)
            connection.execute("UPDATE objects SET created_at = 'wrong'")
            return result
        with mock.patch.object(legacy_module, "_populate", side_effect=populate):
            with self.assertRaises(MigrationError):
                migrate_legacy(self.source, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.temp_leftovers(), [])

    def test_added_directory_before_commit_aborts_and_cleans_temp(self):
        original = legacy_module._verify_source_unchanged
        def changed(source, target, scan, imported_at):
            (source / "unregistered-empty").mkdir()
            return original(source, target, scan, imported_at)
        with mock.patch.object(legacy_module, "_verify_source_unchanged", side_effect=changed):
            with self.assertRaises(MigrationError) as caught:
                migrate_legacy(self.source, self.target)
        self.assertEqual(caught.exception.details["phase"], "pre_commit")
        self.assertFalse(self.target.exists())
        self.assertEqual(self.temp_leftovers(), [])

    def test_configuration_failure_has_exact_rerun_command(self):
        with mock.patch.object(Database, "configure_runtime", side_effect=SchemaError("cannot configure")):
            with self.assertRaises(MigrationError) as caught:
                migrate_legacy(self.source, self.target)
        self.assertEqual(caught.exception.details["rerun_command"], [
            "python", "-m", "src.storage", "migrate-legacy", "--source", str(self.source),
            "--database", str(self.target),
        ])

    def test_target_inside_source_with_live_rollback_journal(self) -> None:
        target = self.source / "c156.sqlite"
        before = self.source_hashes()
        original = legacy_module._verify_source_unchanged
        observed: list[bool] = []

        def spy(source, target_path, scan, imported_at):
            temporary = next(
                iter(
                    target_path.parent.glob(
                        target_path.name + ".migrate-*.tmp"
                    )
                ),
                None,
            )
            self.assertIsNotNone(temporary)
            observed.append(Path(str(temporary) + "-journal").exists())
            return original(source, target_path, scan, imported_at)

        with mock.patch.object(
            legacy_module, "_verify_source_unchanged", side_effect=spy
        ):
            migrate_legacy(self.source, target)
        self.assertTrue(observed)
        self.assertTrue(all(observed), "rollback journal must exist pre-COMMIT")
        self.assertEqual(self.source_hashes(), before)
        self.assertEqual(self.raw_counts(target)["objects"], 8)
        self.assertEqual(self.temp_leftovers(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
