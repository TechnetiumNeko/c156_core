"""Task 8: read-only legacy container scanning, validation and reports.

Every test starts from a fresh copy of the tracked ``data/`` sample; the
repository originals are never opened for writing.  Negative cases corrupt or
extend the copy with raw SQL so a failed scan must abort instead of silently
losing data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

from src.core.errors import MigrationError
from src.storage.legacy import LegacyScan, scan_legacy, source_fingerprint
from tests.helpers import (
    LEGACY_SAMPLE_DOCUMENT_CONTENT,
    LEGACY_SAMPLE_DOCUMENT_SHA256,
    SAMPLE_DOCUMENT_ID,
    TempPathTestCase,
    copy_sample_data,
    create_legacy_document,
    legacy_connection,
    legacy_object_id,
)

IMPORTED_AT = "2026-10-01T00:00:00+00:00"
SAMPLE_MODIFIED_AT = "2026-09-28T15:20:17.134659+08:00"
SAMPLE_MODIFIED_AT_UTC = "2026-09-28T07:20:17.134659+00:00"


class LegacyScanTestCase(TempPathTestCase):
    """Base case: an isolated copy of the tracked legacy sample."""

    def setUp(self) -> None:
        super().setUp()
        self.source = copy_sample_data(self.temp_root / "data")
        self.target = self.temp_root / "c156.sqlite"
        self.imported_at = IMPORTED_AT

    def scan(self, *, target: Path | None = None, imported_at: str = IMPORTED_AT):
        return scan_legacy(
            self.source,
            target=self.target if target is None else target,
            imported_at=imported_at,
        )

    def assertRejected(self, *, target: Path | None = None) -> None:
        with self.assertRaises(MigrationError):
            self.scan(target=target)

    def container(self, relative: str) -> Path:
        return self.source / relative

    def doc_container(self) -> Path:
        return self.container("main/products/concretecream")

    def source_hashes(self) -> dict[str, str]:
        return {
            path.relative_to(self.source).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(self.source.rglob("*"))
            if path.is_file()
        }


class SampleScanTests(LegacyScanTestCase):
    def test_sample_scan_preserves_document(self) -> None:
        scan = self.scan()
        self.assertIsInstance(scan, LegacyScan)
        self.assertEqual(len(scan.objects), 8)
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.id, SAMPLE_DOCUMENT_ID)
        self.assertEqual(doc.content, LEGACY_SAMPLE_DOCUMENT_CONTENT)
        self.assertEqual(
            hashlib.sha256(doc.content.encode("utf-8")).hexdigest(),
            LEGACY_SAMPLE_DOCUMENT_SHA256,
        )
        self.assertEqual(scan.imported_at, IMPORTED_AT)

    def test_sample_counts_and_report_payload(self) -> None:
        scan = self.scan()
        self.assertEqual(scan.report["counts"]["objects"], 8)
        self.assertEqual(scan.report["counts"]["folders"], 7)
        self.assertEqual(scan.report["counts"]["documents"], 1)
        self.assertEqual(scan.report["counts"]["repairs"], 0)
        self.assertEqual(scan.source_digest, source_fingerprint(self.source, target=self.target))
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["fullpath"], "/data/main/products/concretecream")
        self.assertEqual(entry["content_sha256"], LEGACY_SAMPLE_DOCUMENT_SHA256)
        self.assertEqual(entry["content_length"], len(LEGACY_SAMPLE_DOCUMENT_CONTENT))
        self.assertEqual(
            entry["raw_metadata"]["modified_at"], json.dumps(SAMPLE_MODIFIED_AT)
        )
        self.assertEqual(entry["raw_modified_at"], json.dumps(SAMPLE_MODIFIED_AT))
        self.assertEqual(entry["modified_at"], SAMPLE_MODIFIED_AT_UTC)
        self.assertEqual(entry["modified_at_reason"], "stored")
        self.assertEqual(entry["created_at"], IMPORTED_AT)
        self.assertEqual(entry["created_at_reason"], "missing")
        # The report must be strict, round-trippable JSON.
        encoded = json.dumps(scan.report, ensure_ascii=False, allow_nan=False)
        self.assertEqual(json.loads(encoded)["counts"]["objects"], 8)

    def test_sample_parents_positions_and_preorder(self) -> None:
        scan = self.scan()
        by_id = {item.id: item for item in scan.objects}
        root = next(item for item in scan.objects if item.parent_id is None)
        self.assertEqual((root.fullpath, root.name, root.position), ("/data", "", 0))
        self.assertEqual(scan.objects[0].id, root.id)
        main = next(item for item in scan.objects if item.name == "main")
        products = next(item for item in scan.objects if item.name == "products")
        doc = by_id[SAMPLE_DOCUMENT_ID]
        self.assertEqual(doc.fullpath, "/data/main/products/concretecream")
        self.assertEqual(doc.parent_id, products.id)
        self.assertEqual(products.parent_id, main.id)
        self.assertEqual(main.parent_id, root.id)
        root_children = sorted(
            (item.name, item.position)
            for item in scan.objects
            if item.parent_id == root.id
        )
        self.assertEqual(
            root_children,
            [("admin", 0), ("bin", 1), ("main", 2), ("resource", 3)],
        )
        self.assertEqual(
            [(item.name, item.position) for item in scan.objects if item.parent_id == products.id],
            [("concretecream", 0)],
        )

    def test_scan_does_not_modify_source_bytes(self) -> None:
        before = self.source_hashes()
        self.scan()
        self.assertEqual(self.source_hashes(), before)
        self.assertEqual(list(self.source.rglob("*-wal")), [])
        self.assertEqual(list(self.source.rglob("*-shm")), [])
        self.assertEqual(list(self.source.rglob("*-journal")), [])

    def test_unregistered_children_appended_in_binary_name_order(self) -> None:
        products = self.container("main/products")
        products_id = legacy_object_id(products / ".folder")
        create_legacy_document(
            products / "zzz",
            file_id="zzz-id",
            fullpath="/data/main/products/zzz",
            parent_id=products_id,
        )
        create_legacy_document(
            products / "aaa",
            file_id="aaa-id",
            fullpath="/data/main/products/aaa",
            parent_id=products_id,
        )
        scan = self.scan()
        children = [item for item in scan.objects if item.parent_id == products_id]
        self.assertEqual([item.name for item in children], ["concretecream", "aaa", "zzz"])
        self.assertEqual([item.position for item in children], [0, 1, 2])
        self.assertEqual(scan.report["counts"]["repairs"], 2)
        self.assertEqual(
            {item["name"] for item in scan.report["repairs"]}, {"aaa", "zzz"}
        )

    def test_missing_registration_is_repaired_and_reported(self) -> None:
        doc_id = legacy_object_id(self.doc_container())
        with legacy_connection(self.container("main/products/.folder"), write=True) as conn:
            conn.execute('DELETE FROM "file" WHERE child_id = ?', (doc_id,))
        scan = self.scan()
        self.assertEqual(len(scan.objects), 8)
        doc = next(item for item in scan.objects if item.id == doc_id)
        self.assertEqual(doc.position, 0)
        self.assertEqual(scan.report["counts"]["repairs"], 1)
        repair = scan.report["repairs"][0]
        self.assertEqual(repair["kind"], "missing_registration")
        self.assertEqual(repair["child_id"], doc_id)

    def test_names_with_spaces_and_quotes(self) -> None:
        renamed = self.container('main/world settings "x"')
        world_id = legacy_object_id(self.container("main/worldsettings/.folder"))
        self.container("main/worldsettings").rename(renamed)
        with legacy_connection(renamed / ".folder", write=True) as conn:
            conn.execute(
                "UPDATE abstract_file SET fullpath = ?",
                ('/data/main/world settings "x"',),
            )
        scan = self.scan()
        node = next(item for item in scan.objects if item.id == world_id)
        self.assertEqual(node.name, 'world settings "x"')
        self.assertEqual(node.fullpath, '/data/main/world settings "x"')

    def test_crlf_content_is_preserved(self) -> None:
        raw = "line1\r\nline2\r\n"
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("UPDATE document_content SET content = ?", (raw,))
        scan = self.scan()
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.content, raw)
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["content_sha256"], hashlib.sha256(raw.encode("utf-8")).hexdigest())

    def test_extension_metadata_is_preserved_and_validated(self) -> None:
        payload = {"nested": [1, True, None], "text": "值"}
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES ('extra', ?)",
                (json.dumps(payload, ensure_ascii=False),),
            )
        scan = self.scan()
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.metadata["extra"], payload)
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["metadata"]["extra"], payload)
        self.assertEqual(
            entry["raw_metadata"]["extra"], json.dumps(payload, ensure_ascii=False)
        )
        self.assertNotIn("created_at", doc.metadata)


class FingerprintTests(LegacyScanTestCase):
    def test_fingerprint_is_stable(self) -> None:
        self.assertEqual(
            source_fingerprint(self.source, target=self.target),
            source_fingerprint(self.source, target=self.target),
        )

    def test_fingerprint_rescans_added_deleted_changed_files(self) -> None:
        base = source_fingerprint(self.source, target=self.target)
        added = self.container("main/worldsettings/notes")
        added.write_bytes(b"x")
        self.assertNotEqual(base, source_fingerprint(self.source, target=self.target))
        added.unlink()
        self.assertEqual(base, source_fingerprint(self.source, target=self.target))
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES ('tag', '\"x\"')")
        self.assertNotEqual(base, source_fingerprint(self.source, target=self.target))

    def test_target_sidecars_and_temp_namespace_are_excluded(self) -> None:
        target = self.source / "c156.sqlite"
        target.write_bytes(b"not a legacy container")
        Path(str(target) + "-wal").write_bytes(b"wal")
        Path(str(target) + "-journal").write_bytes(b"journal")
        Path(str(target) + "-shm").write_bytes(b"shm")
        temp = self.source / (target.name + ".migrate-abc.tmp")
        temp.write_bytes(b"tmp")
        before = source_fingerprint(self.source, target=target)
        scan = scan_legacy(self.source, target=target, imported_at=IMPORTED_AT)
        self.assertEqual(len(scan.objects), 8)
        self.assertEqual(scan.source_digest, before)
        for path in (target, Path(str(target) + "-wal"), Path(str(target) + "-journal"),
                     Path(str(target) + "-shm"), temp):
            path.unlink()
        self.assertEqual(source_fingerprint(self.source, target=target), before)

    def test_unrelated_temp_file_is_not_ignored(self) -> None:
        (self.source / "notes.tmp").write_bytes(b"x")
        self.assertRejected()


class TargetOverlapTests(LegacyScanTestCase):
    def test_target_that_is_the_root_container_is_rejected(self) -> None:
        self.assertRejected(target=self.container(".folder"))

    def test_target_that_is_a_source_container_is_rejected(self) -> None:
        self.assertRejected(target=self.doc_container())

    def test_target_directory_is_rejected(self) -> None:
        self.assertRejected(target=self.container("main"))


class ValidationAbortTests(LegacyScanTestCase):
    def test_unknown_file_rejected(self) -> None:
        (self.source / "unknown.bin").write_bytes(b"not a container")
        self.assertRejected()

    def test_unknown_table_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("CREATE TABLE surprise (x INTEGER)")
        self.assertRejected()

    def test_missing_required_table_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("DROP TABLE metadata")
        self.assertRejected()

    def test_missing_required_column_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("ALTER TABLE abstract_file RENAME TO abstract_file_old")
            conn.execute(
                "CREATE TABLE abstract_file (singleton INTEGER PRIMARY KEY, id TEXT, kind TEXT)"
            )
        self.assertRejected()

    def test_missing_abstract_file_row_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("DELETE FROM abstract_file")
        self.assertRejected()

    def test_singleton_check_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("UPDATE abstract_file SET singleton = 2")
        self.assertRejected()

    def test_duplicate_id_rejected(self) -> None:
        bin_id = legacy_object_id(self.container("bin/.folder"))
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("UPDATE abstract_file SET id = ?", (bin_id,))
        self.assertRejected()

    def test_fullpath_contradiction_rejected(self) -> None:
        with legacy_connection(self.container("main/products/.folder"), write=True) as conn:
            conn.execute("UPDATE abstract_file SET fullpath = '/data/main/other'")
        self.assertRejected()

    def test_parent_contradiction_rejected(self) -> None:
        admin_id = legacy_object_id(self.container("admin/.folder"))
        with legacy_connection(self.container("main/.folder"), write=True) as conn:
            conn.execute("UPDATE abstract_file SET parent_id = ?", (admin_id,))
        self.assertRejected()

    def test_root_fullpath_contradiction_rejected(self) -> None:
        with legacy_connection(self.container(".folder"), write=True) as conn:
            conn.execute("UPDATE abstract_file SET fullpath = '/elsewhere'")
        self.assertRejected()

    def test_missing_folder_container_rejected(self) -> None:
        self.container("admin/.folder").unlink()
        self.assertRejected()

    def test_missing_mandatory_default_folder_rejected(self) -> None:
        main_id = legacy_object_id(self.container("main/.folder"))
        shutil.rmtree(self.container("main"))
        with legacy_connection(self.container(".folder"), write=True) as conn:
            conn.execute('DELETE FROM "file" WHERE child_id = ?', (main_id,))
        self.assertRejected()

    def test_dangling_registration_rejected(self) -> None:
        self.doc_container().unlink()
        self.assertRejected()

    def test_wrong_parent_registration_rejected(self) -> None:
        doc_id = legacy_object_id(self.doc_container())
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute(
                'INSERT INTO "file"(parent_id, child_id, position) '
                "SELECT id, ?, 0 FROM abstract_file",
                (doc_id,),
            )
        self.assertRejected()

    def test_missing_content_singleton_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("DELETE FROM document_content")
        self.assertRejected()

    def test_duplicate_content_singleton_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("INSERT INTO document_content(singleton, content) VALUES (2, 'x')")
        self.assertRejected()

    def test_document_file_table_must_be_empty(self) -> None:
        doc_id = legacy_object_id(self.doc_container())
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute(
                'INSERT INTO "file"(parent_id, child_id, position) VALUES (?, ?, 0)',
                (doc_id, doc_id),
            )
        self.assertRejected()

    def test_unknown_kind_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("UPDATE abstract_file SET kind = 'widget'")
        self.assertRejected()

    def test_out_of_scope_payload_kind_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("UPDATE abstract_file SET kind = 'executable'")
        self.assertRejected()

    def test_kind_and_container_name_must_agree(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("UPDATE abstract_file SET kind = 'folder'")
        self.assertRejected()

    def test_reserved_metadata_key_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES ('version', '1')")
        self.assertRejected()

    def test_nonfinite_metadata_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES ('bad', 'NaN')")
        self.assertRejected()

    def test_invalid_json_metadata_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("INSERT INTO metadata(key, value) VALUES ('bad', '{oops')")
        self.assertRejected()

    def test_damaged_database_rejected(self) -> None:
        self.container("admin/.folder").write_bytes(b"not a database at all")
        self.assertRejected()

    def test_empty_database_rejected(self) -> None:
        self.container("admin/.folder").write_bytes(b"")
        self.assertRejected()

    def test_directory_symlink_rejected(self) -> None:
        (self.source / "linked").symlink_to(self.container("admin"), target_is_directory=True)
        self.assertRejected()

    def test_file_symlink_rejected(self) -> None:
        (self.source / "linked.bin").symlink_to(self.doc_container())
        self.assertRejected()

    def test_source_journal_rejected(self) -> None:
        (self.container("admin/.folder-journal")).write_bytes(b"")
        self.assertRejected()


class ExactSchemaContractTests(LegacyScanTestCase):
    """An extra column is unknown payload data and must abort, not be dropped."""

    def test_extra_document_content_column_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("ALTER TABLE document_content ADD COLUMN extra_payload TEXT")
            conn.execute("UPDATE document_content SET extra_payload = 'unmigrated'")
        with self.assertRaises(MigrationError) as ctx:
            self.scan()
        self.assertEqual(ctx.exception.details.get("columns"), ["extra_payload"])

    def test_extra_abstract_file_column_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("ALTER TABLE abstract_file ADD COLUMN extra_payload TEXT")
        self.assertRejected()

    def test_extra_folder_metadata_column_rejected(self) -> None:
        with legacy_connection(self.container("admin/.folder"), write=True) as conn:
            conn.execute("ALTER TABLE metadata ADD COLUMN extra_payload TEXT")
        self.assertRejected()

    def test_malformed_time_json_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("UPDATE metadata SET value = '{oops' WHERE key = 'modified_at'")
        self.assertRejected()

    def test_nonfinite_time_constant_rejected(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("UPDATE metadata SET value = 'NaN' WHERE key = 'modified_at'")
        self.assertRejected()

    def test_valid_json_non_string_time_falls_back(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("UPDATE metadata SET value = '123' WHERE key = 'modified_at'")
        scan = self.scan()
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["modified_at_reason"], "invalid")
        self.assertEqual(entry["modified_at"], IMPORTED_AT)


class ImportedAtContractTests(LegacyScanTestCase):
    def test_invalid_imported_at_rejected(self) -> None:
        for value in ("invalid-time", "2026-01-01T00:00:00", "", 123, None):
            with self.subTest(value=value):
                with self.assertRaises(MigrationError):
                    scan_legacy(self.source, target=self.target, imported_at=value)

    def test_imported_at_is_normalized_to_utc(self) -> None:
        scan = scan_legacy(
            self.source, target=self.target, imported_at="2026-10-01T08:00:00+08:00"
        )
        self.assertEqual(scan.imported_at, "2026-10-01T00:00:00+00:00")
        self.assertEqual(scan.report["imported_at"], "2026-10-01T00:00:00+00:00")
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.created_at, "2026-10-01T00:00:00+00:00")


class TimeMappingTests(LegacyScanTestCase):
    def test_aware_stored_time_is_converted_to_utc(self) -> None:
        scan = self.scan()
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.modified_at, SAMPLE_MODIFIED_AT_UTC)

    def test_missing_and_naive_times_fall_back_with_reasons(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute("DELETE FROM metadata WHERE key = 'created_at'")
            conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'modified_at'",
                (json.dumps("2026-01-01T00:00:00"),),
            )
        scan = self.scan()
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.created_at, IMPORTED_AT)
        self.assertEqual(doc.modified_at, IMPORTED_AT)
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["created_at_reason"], "missing")
        self.assertEqual(entry["modified_at_reason"], "naive")
        self.assertEqual(entry["raw_modified_at"], json.dumps("2026-01-01T00:00:00"))

    def test_invalid_time_falls_back_to_created_at(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'modified_at'",
                (json.dumps("not-a-time"),),
            )
        scan = self.scan()
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["modified_at_reason"], "invalid")
        self.assertEqual(entry["modified_at"], IMPORTED_AT)

    def test_stored_created_at_is_adopted(self) -> None:
        with legacy_connection(self.doc_container(), write=True) as conn:
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES ('created_at', ?)",
                (json.dumps("2026-02-03T04:05:06+02:00"),),
            )
        scan = self.scan()
        doc = next(item for item in scan.objects if item.kind == "document")
        self.assertEqual(doc.created_at, "2026-02-03T02:05:06+00:00")
        entry = next(
            item for item in scan.report["objects"] if item["id"] == SAMPLE_DOCUMENT_ID
        )
        self.assertEqual(entry["created_at_reason"], "stored")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
