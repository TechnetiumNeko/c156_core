"""Behaviour tests for ContentService writes: create, save and metadata.

Every test starts from the initialized read fixture of :mod:`tests.helpers`,
which seeds a real ``main`` subtree in a freshly initialized database.  Nothing
here touches the tracked legacy sample under ``data/``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core import (
    AlreadyExists,
    Conflict,
    ContentScope,
    InvalidArgument,
    InvalidName,
    NotDirectory,
    NotDocument,
    NotFound,
    PathOutsideRoot,
)
from src.services import ApplicationUnitOfWork, ContentService
from src.storage.audit_repository import AuditEventRecord
from src.storage.errors import ConstraintError
from src.storage.repository import Repository
from tests.helpers import (
    ContentReadFixture,
    TempPathTestCase,
    entry_state,
    revision_state,
    seed_content_read_fixture,
    seed_test_actors,
    table_counts,
)


class ContentWriteTestCase(TempPathTestCase):
    """Shared fixture and observation helpers for the write tests."""

    fixture: ContentReadFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.token = seed_test_actors(self.fixture.database, self.fixture.workspace_id)["owner"].session_token
        self.service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope
        self.parent_id = self.fixture.products_id
        self.document_id = self.fixture.concretecream_id

    def counts(self) -> dict:
        return table_counts(self.fixture.path)

    def entry(self, object_id: str) -> dict | None:
        return entry_state(self.fixture.path, object_id)

    def revisions(self, object_id: str) -> list:
        return revision_state(self.fixture.path, object_id)

    def sibling_scope(self) -> ContentScope:
        return ContentScope(
            self.fixture.workspace_id,
            self.fixture.branch_id,
            self.fixture.products_id,
        )


class TestCreateFolder(ContentWriteTestCase):
    def test_create_folder_appends_and_touches_parent(self):
        before_counts = self.counts()
        before_parent = self.entry(self.parent_id)
        before_root = self.entry(self.scope.root_id)

        node = self.service.create_folder(self.scope, self.parent_id, "新 目录", session_token=self.token)

        self.assertEqual(node.kind, "folder")
        self.assertEqual(node.name, "新 目录")
        self.assertEqual(node.parent_id, self.parent_id)
        self.assertEqual(node.position, 2)
        self.assertEqual(node.version, 1)
        self.assertEqual(node.path, "/products/新 目录")
        self.assertIsNone(node.deleted_at)
        self.assertEqual(dict(node.metadata), {})

        after_counts = self.counts()
        self.assertEqual(after_counts["objects"], before_counts["objects"] + 1)
        self.assertEqual(after_counts["entries"], before_counts["entries"] + 1)
        self.assertEqual(
            after_counts["document_revisions"],
            before_counts["document_revisions"],
        )

        parent = self.entry(self.parent_id)
        self.assertEqual(parent["version"], before_parent["version"] + 1)
        self.assertNotEqual(parent["modified_at"], before_parent["modified_at"])
        self.assertEqual(parent["modified_at"], node.created_at)
        # Creating a direct child never propagates to the grandparent.
        self.assertEqual(self.entry(self.scope.root_id), before_root)

    def test_create_folder_at_end_of_multiple_children(self):
        first = self.service.create_folder(self.scope, self.parent_id, "一", session_token=self.token)
        second = self.service.create_folder(self.scope, self.parent_id, "二", session_token=self.token)
        self.assertEqual((first.position, second.position), (2, 3))


class TestCreateDocument(ContentWriteTestCase):
    def test_create_document_creates_empty_first_revision(self):
        before_counts = self.counts()
        before_parent = self.entry(self.parent_id)
        before_root = self.entry(self.scope.root_id)

        node = self.service.create_document(self.scope, self.parent_id, "新文档", session_token=self.token)

        self.assertEqual(node.kind, "document")
        self.assertEqual(node.name, "新文档")
        self.assertEqual(node.parent_id, self.parent_id)
        self.assertEqual(node.position, 2)
        self.assertEqual(node.version, 1)
        self.assertEqual(node.content, "")
        self.assertEqual(node.path, "/products/新文档")
        self.assertIsNone(node.deleted_at)
        self.assertEqual(node.created_at, node.modified_at)

        revisions = self.revisions(node.id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["id"], node.revision_id)
        self.assertIsNone(revisions[0]["parent_revision_id"])
        self.assertEqual(revisions[0]["content"], "")
        self.assertEqual(revisions[0]["created_at"], node.created_at)

        after_counts = self.counts()
        self.assertEqual(after_counts["objects"], before_counts["objects"] + 1)
        self.assertEqual(after_counts["entries"], before_counts["entries"] + 1)
        self.assertEqual(
            after_counts["document_revisions"],
            before_counts["document_revisions"] + 1,
        )
        parent = self.entry(self.parent_id)
        self.assertEqual(parent["version"], before_parent["version"] + 1)
        self.assertEqual(parent["modified_at"], node.created_at)
        # Creating a direct child never propagates to the grandparent.
        self.assertEqual(self.entry(self.scope.root_id), before_root)

    def test_create_document_with_chinese_content_is_readable(self):
        node = self.service.create_document(
            self.scope, self.parent_id, "中文 文档", content="正文 内容",
            session_token=self.token,
        )
        read = self.service.read_document(self.scope, node.id, session_token=self.token)
        self.assertEqual(read, node)
        self.assertEqual(read.content, "正文 内容")
        self.assertEqual(read.revision_id, node.revision_id)

    def test_create_document_content_must_be_text(self):
        before = self.counts()
        for bad in (123, None, b"bytes", ["list"], {"a": 1}):
            with self.assertRaises(InvalidArgument):
                self.service.create_document(
                    self.scope, self.parent_id, "x", content=bad,
                    session_token=self.token,
                )
        self.assertEqual(self.counts(), before)


class TestCreateErrors(ContentWriteTestCase):
    def test_create_rejects_wrong_parent_type_and_scope(self):
        before = self.counts()
        with self.assertRaises(NotDirectory):
            self.service.create_folder(self.scope, self.document_id, "x", session_token=self.token)
        with self.assertRaises(NotDirectory):
            self.service.create_document(self.scope, self.document_id, "x", session_token=self.token)
        with self.assertRaises(NotFound):
            self.service.create_folder(self.scope, "missing", "x", session_token=self.token)
        with self.assertRaises(NotFound):
            self.service.create_folder(
                self.sibling_scope(), self.fixture.admin_id, "x",
                session_token=self.token,
            )
        self.assertEqual(self.counts(), before)

    def test_create_rejects_invalid_name_and_parent_without_partial(self):
        before = self.counts()
        before_parent = self.entry(self.parent_id)
        for name in ("", ".", "..", "a/b", "a\\b", "x\x00y"):
            with self.assertRaises(InvalidName):
                self.service.create_folder(self.scope, self.parent_id, name, session_token=self.token)
        with self.assertRaises(InvalidName):
            self.service.create_folder(self.scope, self.parent_id, 123, session_token=self.token)
        with self.assertRaises(InvalidArgument):
            self.service.create_folder(self.scope, 123, "x", session_token=self.token)
        with self.assertRaises(InvalidArgument):
            self.service.create_document(self.scope, 123, "x", session_token=self.token)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.entry(self.parent_id), before_parent)

    def test_create_duplicate_name_raises_already_exists(self):
        self.service.create_folder(self.scope, self.parent_id, "重复", session_token=self.token)
        before = self.counts()
        with self.assertRaises(AlreadyExists):
            self.service.create_folder(self.scope, self.parent_id, "重复", session_token=self.token)
        with self.assertRaises(AlreadyExists):
            self.service.create_document(self.scope, self.parent_id, "重复", session_token=self.token)
        self.assertEqual(self.counts(), before)

    def test_name_unique_constraint_maps_to_already_exists(self):
        self.service.create_folder(self.scope, self.parent_id, "同名", session_token=self.token)
        before = self.counts()
        # Simulate the race where the pre-check misses the sibling but the
        # database unique index still rejects the insert.
        with patch.object(Repository, "find_child", return_value=None):
            with self.assertRaises(AlreadyExists):
                self.service.create_folder(self.scope, self.parent_id, "同名", session_token=self.token)
        self.assertEqual(self.counts(), before)

    def test_other_unique_constraint_is_not_swallowed(self):
        before = self.counts()
        # Force a position collision: only the sibling NAME index may become
        # AlreadyExists, so a position unique failure must surface unchanged.
        with patch.object(Repository, "next_position", return_value=0):
            with self.assertRaises(ConstraintError):
                self.service.create_folder(self.scope, self.parent_id, "位置冲突", session_token=self.token)
        self.assertEqual(self.counts(), before)


class TestReadDocument(ContentWriteTestCase):
    def test_read_document_returns_content_and_revision(self):
        doc = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        self.assertEqual(doc.kind, "document")
        self.assertEqual(doc.name, "concretecream")
        self.assertEqual(doc.content, self.fixture.concretecream_content)
        self.assertEqual(doc.revision_id, self.fixture.concretecream_revision_id)
        self.assertEqual(doc.path, "/products/concretecream")

    def test_read_document_rejects_wrong_type_and_scope(self):
        with self.assertRaises(NotDocument):
            self.service.read_document(self.scope, self.parent_id, session_token=self.token)
        with self.assertRaises(NotFound):
            self.service.read_document(self.scope, "missing-object", session_token=self.token)
        with self.assertRaises(NotFound):
            self.service.read_document(self.sibling_scope(), self.scope.root_id, session_token=self.token)
        with self.assertRaises(InvalidArgument):
            self.service.read_document(self.scope, 123, session_token=self.token)


class TestSaveDocument(ContentWriteTestCase):
    def test_save_appends_revision_and_updates_pointer(self):
        before_doc = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        before_revisions = self.revisions(self.document_id)

        after = self.service.save_document(
            self.scope,
            self.document_id,
            "改写 正文",
            expected_revision_id=before_doc.revision_id,
            session_token=self.token,
        )

        self.assertEqual(after.content, "改写 正文")
        self.assertNotEqual(after.revision_id, before_doc.revision_id)
        self.assertEqual(after.version, before_doc.version + 1)
        self.assertNotEqual(after.modified_at, before_doc.modified_at)
        self.assertEqual(after.created_at, before_doc.created_at)

        revisions = {row["id"]: row for row in self.revisions(self.document_id)}
        self.assertEqual(len(revisions), len(before_revisions) + 1)
        new_revision = revisions[after.revision_id]
        self.assertEqual(new_revision["parent_revision_id"], before_doc.revision_id)
        self.assertEqual(new_revision["content"], "改写 正文")
        self.assertEqual(new_revision["created_at"], after.modified_at)
        self.assertEqual(
            revisions[before_doc.revision_id],
            before_revisions[0],
        )
        self.assertEqual(
            self.service.read_document(self.scope, self.document_id, session_token=self.token), after
        )

    def test_second_save_parents_the_previous_revision(self):
        first = self.service.save_document(
            self.scope,
            self.document_id,
            "one",
            expected_revision_id=self.fixture.concretecream_revision_id,
            session_token=self.token,
        )
        second = self.service.save_document(
            self.scope, self.document_id, "two", expected_revision_id=first.revision_id,
            session_token=self.token,
        )
        revisions = {row["id"]: row for row in self.revisions(self.document_id)}
        self.assertEqual(
            revisions[second.revision_id]["parent_revision_id"], first.revision_id
        )
        self.assertEqual(
            revisions[first.revision_id]["parent_revision_id"],
            self.fixture.concretecream_revision_id,
        )

    def test_same_content_save_is_a_noop(self):
        before = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        before_counts = self.counts()
        before_entry = self.entry(self.document_id)

        after = self.service.save_document(
            self.scope,
            self.document_id,
            before.content,
            expected_revision_id=before.revision_id,
            session_token=self.token,
        )

        self.assertEqual(after, before)
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(self.entry(self.document_id), before_entry)

    def test_old_revision_conflicts_even_for_identical_content(self):
        before = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        after = self.service.save_document(
            self.scope,
            self.document_id,
            "新正文",
            expected_revision_id=before.revision_id,
            session_token=self.token,
        )
        with self.assertRaises(Conflict):
            self.service.save_document(
                self.scope,
                self.document_id,
                after.content,
                expected_revision_id=before.revision_id,
                session_token=self.token,
            )
        self.assertEqual(
            self.service.read_document(self.scope, self.document_id, session_token=self.token), after
        )

    def test_save_rejects_bad_arguments_without_partial_writes(self):
        before = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        before_counts = self.counts()
        with self.assertRaises(Conflict):
            self.service.save_document(
                self.scope, self.document_id, "x", expected_revision_id="missing",
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.save_document(
                self.scope,
                self.document_id,
                123,
                expected_revision_id=before.revision_id,
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.save_document(
                self.scope, self.document_id, "x", expected_revision_id=123,
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.save_document(
                self.scope, self.document_id, "x", expected_revision_id=None,
                session_token=self.token,
            )
        with self.assertRaises(NotDocument):
            self.service.save_document(
                self.scope, self.parent_id, "x", expected_revision_id=before.revision_id,
                session_token=self.token,
            )
        with self.assertRaises(NotFound):
            self.service.save_document(
                self.scope, "missing", "x", expected_revision_id=before.revision_id,
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.save_document(
                self.scope, 123, "x", expected_revision_id=before.revision_id,
                session_token=self.token,
            )
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(
            self.service.read_document(self.scope, self.document_id, session_token=self.token), before
        )

    def test_save_does_not_touch_parent(self):
        before_parent = self.entry(self.parent_id)
        self.service.save_document(
            self.scope,
            self.document_id,
            "changed",
            expected_revision_id=self.fixture.concretecream_revision_id,
            session_token=self.token,
        )
        self.assertEqual(self.entry(self.parent_id), before_parent)


class TestSetMetadata(ContentWriteTestCase):
    def test_set_metadata_merges_and_bumps_version(self):
        before_parent = self.entry(self.scope.root_id)
        node = self.service.set_metadata(
            self.scope,
            self.parent_id,
            {"tag": "y", "extra": {"b": 2}},
            expected_version=1,
            session_token=self.token,
        )
        self.assertEqual(node.version, 2)
        self.assertEqual(
            dict(node.metadata),
            {"tag": "y", "nested": {"a": 1}, "extra": {"b": 2}},
        )
        self.assertEqual(
            self.service.get_metadata(self.scope, self.parent_id, session_token=self.token),
            {"tag": "y", "nested": {"a": 1}, "extra": {"b": 2}},
        )
        # metadata changes never propagate to the parent directory.
        self.assertEqual(self.entry(self.scope.root_id), before_parent)

    def test_set_metadata_none_is_json_null_and_nested_copy_is_detached(self):
        node = self.service.set_metadata(
            self.scope,
            self.parent_id,
            {"note": None, "deep": {"k": [1, 2]}},
            expected_version=1,
            session_token=self.token,
        )
        self.assertIsNone(node.metadata["note"])
        caller_payload = {"deep": {"k": [1, 2, 3]}}
        self.service.set_metadata(
            self.scope, self.parent_id, caller_payload, expected_version=2,
            session_token=self.token,
        )
        caller_payload["deep"]["k"].append(4)
        self.assertEqual(
            self.service.get_metadata(self.scope, self.parent_id, session_token=self.token)["deep"]["k"],
            [1, 2, 3],
        )

    def test_set_metadata_bool_and_number_are_distinct(self):
        self.service.set_metadata(
            self.scope, self.parent_id, {"flag": 1}, expected_version=1,
            session_token=self.token,
        )
        self.service.set_metadata(
            self.scope, self.parent_id, {"flag": True}, expected_version=2,
            session_token=self.token,
        )
        self.assertIs(self.service.get_metadata(self.scope, self.parent_id, session_token=self.token)["flag"], True)
        self.service.set_metadata(
            self.scope, self.parent_id, {"flag": 1}, expected_version=3,
            session_token=self.token,
        )
        self.assertEqual(
            self.service.get_metadata(self.scope, self.parent_id, session_token=self.token)["flag"], 1
        )
        # JSON numbers compare numerically, so 1 and 1.0 are the same value.
        before = self.entry(self.parent_id)
        self.service.set_metadata(
            self.scope, self.parent_id, {"flag": 1.0}, expected_version=4,
            session_token=self.token,
        )
        self.assertEqual(self.entry(self.parent_id), before)

    def test_set_metadata_rejects_reserved_and_invalid(self):
        before = self.entry(self.parent_id)
        for change in (
            {"version": 2},
            {"id": "x"},
            {"parent_id": None},
            {"name": "x"},
            {"position": 0},
            {"revision_id": "x"},
            {"created_at": "x"},
            {"modified_at": "x"},
            {"deleted_at": None},
            {"workspace_id": "x"},
            {"branch_id": "x"},
            {"object_id": "x"},
            {"current_revision_id": "x"},
            {"bad": float("nan")},
            {"bad": float("inf")},
            {1: "x"},
            {"bad": object()},
            {"nested": {"bad": float("nan")}},
        ):
            with self.assertRaises(InvalidArgument):
                self.service.set_metadata(
                    self.scope, self.parent_id, change, expected_version=1,
                    session_token=self.token,
                )
        with self.assertRaises(InvalidArgument):
            self.service.set_metadata(
                self.scope, self.parent_id, "not-an-object", expected_version=1,
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.set_metadata(
                self.scope, self.parent_id, [("k", "v")], expected_version=1,
                session_token=self.token,
            )
        self.assertEqual(self.entry(self.parent_id), before)
        self.assertEqual(
            self.service.get_metadata(self.scope, self.parent_id, session_token=self.token),
            {"tag": "x", "nested": {"a": 1}},
        )

    def test_same_value_update_is_a_noop_but_stale_version_conflicts(self):
        before = self.entry(self.parent_id)
        node = self.service.set_metadata(
            self.scope, self.parent_id, {"tag": "x"}, expected_version=1,
            session_token=self.token,
        )
        self.assertEqual(node.version, 1)
        self.assertEqual(self.entry(self.parent_id), before)

        with self.assertRaises(Conflict):
            self.service.set_metadata(
                self.scope, self.parent_id, {"tag": "x"}, expected_version=999,
                session_token=self.token,
            )

        self.service.set_metadata(
            self.scope, self.parent_id, {"tag": "y"}, expected_version=1,
            session_token=self.token,
        )
        with self.assertRaises(Conflict):
            self.service.set_metadata(
                self.scope, self.parent_id, {"tag": "y"}, expected_version=1,
                session_token=self.token,
            )
        self.assertEqual(self.entry(self.parent_id)["version"], 2)

    def test_expected_version_must_be_a_positive_integer(self):
        before = self.entry(self.parent_id)
        for bad in (True, 1.0, "1", None, 0, -1):
            with self.assertRaises(InvalidArgument):
                self.service.set_metadata(
                    self.scope, self.parent_id, {"tag": "x"}, expected_version=bad,
                    session_token=self.token,
                )
        self.assertEqual(self.entry(self.parent_id), before)

    def test_set_metadata_on_document_and_missing(self):
        before = self.entry(self.document_id)
        node = self.service.set_metadata(
            self.scope,
            self.document_id,
            {"k": "v"},
            expected_version=before["version"],
            session_token=self.token,
        )
        self.assertEqual(node.version, 2)
        self.assertEqual(
            self.service.get_metadata(self.scope, self.document_id, session_token=self.token), {"k": "v"}
        )
        with self.assertRaises(NotFound):
            self.service.set_metadata(
                self.scope, "missing", {"k": "v"}, expected_version=1,
                session_token=self.token,
            )
        with self.assertRaises(InvalidArgument):
            self.service.set_metadata(self.scope, 123, {"k": "v"}, expected_version=1, session_token=self.token)
        with self.assertRaises(Conflict):
            self.service.set_metadata(
                self.scope, self.document_id, {"k": "v"}, expected_version=1,
                session_token=self.token,
            )


class TestTransactionalRollback(ContentWriteTestCase):
    """An injected failure after earlier DAO writes must undo all of them."""

    def test_content_and_audit_failure_roll_back_together(self):
        before_counts = self.counts()
        before_parent = self.entry(self.parent_id)
        event = AuditEventRecord("test-audit", None, self.scope.workspace_id,
                                 "content.created", "document", "target",
                                 None, None, "2026-10-03T00:00:00+00:00")
        with self.assertRaises(ConstraintError):
            with ApplicationUnitOfWork(self.fixture.database).transaction(write=True) as work:
                work.content(self.scope).create_document(
                    self.scope, self.parent_id, "audit-rollback", content="body")
                work.audit.append(event)
                # A real primary-key violation after both repositories wrote.
                work.audit.append(event)
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(self.entry(self.parent_id), before_parent)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM audit_events WHERE id = ?", (event.id,)
            ).fetchone()[0], 0)

    def test_create_document_failure_rolls_back_object_and_revision(self):
        before_counts = self.counts()
        before_parent = self.entry(self.parent_id)
        with patch.object(
            Repository, "insert_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.create_document(
                    self.scope, self.parent_id, "x", content="body",
                    session_token=self.token,
                )
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(self.entry(self.parent_id), before_parent)

    def test_create_folder_failure_rolls_back_object(self):
        before_counts = self.counts()
        before_parent = self.entry(self.parent_id)
        with patch.object(
            Repository, "insert_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.create_folder(self.scope, self.parent_id, "x", session_token=self.token)
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(self.entry(self.parent_id), before_parent)

    def test_save_failure_rolls_back_new_revision(self):
        before = self.service.read_document(self.scope, self.document_id, session_token=self.token)
        before_counts = self.counts()
        with patch.object(
            Repository, "update_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.save_document(
                    self.scope,
                    self.document_id,
                    "changed",
                    expected_revision_id=before.revision_id,
                    session_token=self.token,
                )
        self.assertEqual(self.counts(), before_counts)
        self.assertEqual(
            self.service.read_document(self.scope, self.document_id, session_token=self.token), before
        )

    def test_metadata_failure_rolls_back(self):
        before = self.entry(self.parent_id)
        with patch.object(
            Repository, "update_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.set_metadata(
                    self.scope, self.parent_id, {"tag": "y"}, expected_version=1,
                    session_token=self.token,
                )
        self.assertEqual(self.entry(self.parent_id), before)
        self.assertEqual(
            self.service.get_metadata(self.scope, self.parent_id, session_token=self.token),
            {"tag": "x", "nested": {"a": 1}},
        )


if __name__ == "__main__":
    unittest.main()


class TestMetadataPrevalidation(ContentWriteTestCase):
    def test_invalid_metadata_never_opens_write_transaction(self):
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        doc = self.service.get_node(self.scope, self.document_id, session_token=self.token)
        for index, payload in enumerate((cyclic_dict, {"loop": cyclic_list}, {"bad": object()},
                        {"bad": float("nan")}, {"bad": "\ud800"})):
            with self.subTest(case=index):
                with patch.object(self.service, "_write") as write:
                    with self.assertRaises(InvalidArgument):
                        self.service.set_metadata(self.scope, doc.id, payload,
                                                  expected_version=doc.version, session_token=self.token)
                    write.assert_not_called()


    def test_metadata_is_detached_before_write_connection(self):
        doc = self.service.get_node(self.scope, self.document_id, session_token=self.token)
        changes = {"nested": {"value": "validated"}}
        original_write = self.service._write
        def mutate_before_connection():
            changes["nested"]["value"] = object()
            return original_write()
        with patch.object(self.service, "_write", side_effect=mutate_before_connection):
            saved = self.service.set_metadata(self.scope, doc.id, changes,
                                               expected_version=doc.version, session_token=self.token)
        self.assertEqual(saved.metadata["nested"]["value"], "validated")
