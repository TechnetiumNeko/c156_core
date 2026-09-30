"""Behaviour tests for delete snapshots and atomic soft deletion.

The tests pin the contract from the design spec §4.2 and the Task 7 brief:

* ``prepare_delete`` returns the active subtree (start included) with depths and
  a canonical SHA-256 token over ``[workspace, branch, root, object, pairs]``,
  where the active ``(object_id, version)`` pairs are sorted by object id.
* ``delete_node`` runs one write transaction, re-reads the subtree and compares
  both the target version and the token, so a concurrent content, metadata,
  insert, rename, move-out or child delete makes an old token :class:`Conflict`
  without deleting anything.
* A successful recursive delete bumps every active subtree entry exactly once
  with one shared modified/deleted timestamp, bumps the external parent once,
  leaves previously deleted descendants untouched and preserves objects and
  revisions.
* The ``soft_delete_entries`` DAO only touches active rows and batches its work
  so a lowered SQLite host-variable limit cannot break a large delete.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch

from src.core import (
    Conflict,
    ContentScope,
    DirectoryNotEmpty,
    InvalidArgument,
    NotDirectory,
    NotFound,
    PathOutsideRoot,
    ProtectedNode,
)
from src.services import ContentService
from src.storage import Database
from src.storage.records import EntryRecord, RevisionRecord
from src.storage.repository import Repository
from tests.helpers import (
    FIXTURE_TIME,
    ContentReadFixture,
    TempPathTestCase,
    entry_state,
    revision_state,
    seed_content_read_fixture,
    table_counts,
)


class ContentDeleteTestCase(TempPathTestCase):
    """Shared fixture and raw-observation helpers for the delete tests."""

    fixture: ContentReadFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.service = ContentService(self.fixture.database)
        # A second service instance opens its own connection on every call, so
        # mutations made through it are invisible to an already-prepared token.
        self.other_service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope
        self.root_scope = self.fixture.root_scope
        self.products_id = self.fixture.products_id
        self.document_id = self.fixture.concretecream_id

    # -- observation helpers ------------------------------------------------

    def counts(self) -> dict:
        return table_counts(self.fixture.path)

    def entry(self, object_id: str) -> dict | None:
        return entry_state(self.fixture.path, object_id)

    def revisions(self, object_id: str) -> list:
        return revision_state(self.fixture.path, object_id)

    def products_scope(self) -> ContentScope:
        return ContentScope(
            self.fixture.workspace_id, self.fixture.branch_id, self.products_id
        )

    def seed_delete_tree(self) -> dict:
        """Create ``products/tree`` with nested documents and a deleted folder.

        The return mapping holds the stable ids.  ``stale`` is soft-deleted
        before the snapshot so the recursive delete can prove it is left alone.
        """

        tree = self.service.create_folder(self.scope, self.products_id, "tree")
        kept = self.service.create_document(
            self.scope, tree.id, "kept", content="kept-body"
        )
        nested = self.service.create_folder(self.scope, tree.id, "nested")
        deep = self.service.create_document(
            self.scope, nested.id, "deep", content="deep-body"
        )
        stale = self.service.create_folder(self.scope, nested.id, "stale")
        stale_node = self.service.get_node(self.scope, stale.id)
        self.service.delete_node(
            self.scope, stale.id, expected_version=stale_node.version
        )
        return {
            "tree": tree.id,
            "kept": kept.id,
            "nested": nested.id,
            "deep": deep.id,
            "stale": stale.id,
        }

    def snapshot_state(self, object_ids) -> dict:
        return {object_id: self.entry(object_id) for object_id in object_ids}

    def assert_delete_failed_cleanly(self, before_entries: dict, before_counts: dict) -> None:
        self.assertEqual(self.counts(), before_counts)
        for object_id, state in before_entries.items():
            self.assertEqual(self.entry(object_id), state)

    def mutate_then_old_token_conflicts(self, mutate) -> dict:
        """Take a snapshot, run *mutate*, then prove the old token conflicts.

        Returns the id mapping so callers can add extra assertions.
        """

        ids = self.seed_delete_tree()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])
        mutate(ids, snapshot)
        watched = set(ids.values())
        before_entries = self.snapshot_state(watched)
        before_counts = self.counts()
        with self.assertRaises(Conflict):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=snapshot.version,
                recursive=True,
                expected_subtree_token=snapshot.subtree_token,
            )
        self.assert_delete_failed_cleanly(before_entries, before_counts)
        return ids


class TestPrepareDelete(ContentDeleteTestCase):
    def test_returns_active_subtree_in_preorder_with_depths(self):
        ids = self.seed_delete_tree()

        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        self.assertEqual(snapshot.object_id, ids["tree"])
        self.assertEqual(snapshot.version, self.entry(ids["tree"])["version"])
        self.assertEqual(
            [(item.node.id, item.depth) for item in snapshot.items],
            [
                (ids["tree"], 0),
                (ids["kept"], 1),
                (ids["nested"], 1),
                (ids["deep"], 2),
            ],
        )
        # The previously soft-deleted descendant is not part of the snapshot.
        self.assertNotIn(
            ids["stale"], [item.node.id for item in snapshot.items]
        )

    def test_token_matches_canonical_sha_encoding(self):
        ids = self.seed_delete_tree()

        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        pairs = sorted(
            ([item.node.id, item.node.version] for item in snapshot.items),
            key=lambda pair: pair[0],
        )
        payload = [
            self.scope.workspace_id,
            self.scope.branch_id,
            self.scope.root_id,
            ids["tree"],
            pairs,
        ]
        canonical = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(snapshot.subtree_token, expected)
        self.assertEqual(len(snapshot.subtree_token), 64)

    def test_token_is_stable_and_changes_with_a_subtree_version(self):
        ids = self.seed_delete_tree()

        first = self.service.prepare_delete(self.scope, ids["tree"])
        second = self.service.prepare_delete(self.scope, ids["tree"])
        self.assertEqual(first.subtree_token, second.subtree_token)

        deep = self.service.get_node(self.scope, ids["deep"])
        self.other_service.rename_node(
            self.scope, ids["deep"], "deep-renamed", expected_version=deep.version
        )
        third = self.service.prepare_delete(self.scope, ids["tree"])
        self.assertNotEqual(first.subtree_token, third.subtree_token)
        self.assertEqual(third.version, first.version)
        self.assertEqual(
            self.entry(ids["tree"])["version"], first.version
        )

    def test_rejects_document_protected_and_deleted_targets(self):
        with self.assertRaises(NotDirectory):
            self.service.prepare_delete(self.scope, self.document_id)
        with self.assertRaises(ProtectedNode):
            self.service.prepare_delete(self.scope, self.scope.root_id)
        with self.assertRaises(ProtectedNode):
            self.service.prepare_delete(self.root_scope, self.root_scope.root_id)
        with self.assertRaises(ProtectedNode):
            self.service.prepare_delete(self.root_scope, self.fixture.admin_id)
        with self.assertRaises(NotFound):
            self.service.prepare_delete(
                self.scope, self.fixture.deleted_folder_id
            )
        with self.assertRaises(NotFound):
            self.service.prepare_delete(self.scope, "missing")

    def test_respects_access_root_and_branch(self):
        products_scope = self.products_scope()

        with self.assertRaises(PathOutsideRoot):
            self.service.prepare_delete(
                products_scope, self.fixture.chinese_folder_id
            )
        # The dev document lives in another branch, so the scoped lookup misses.
        with self.assertRaises(NotFound):
            self.service.prepare_delete(
                products_scope, self.fixture.dev_document_id
            )


class TestNonRecursiveDelete(ContentDeleteTestCase):
    def test_delete_document_soft_deletes_and_preserves_revision(self):
        before_entry = self.entry(self.document_id)
        before_revisions = self.revisions(self.document_id)
        before_objects = self.counts()["objects"]
        before_parent = self.entry(self.products_id)

        result = self.service.delete_node(
            self.scope, self.document_id, expected_version=before_entry["version"]
        )

        self.assertIsNone(result)
        with self.assertRaises(NotFound):
            self.service.read_document(self.scope, self.document_id)
        with self.assertRaises(NotFound):
            self.service.get_node(self.scope, self.document_id)
        after = self.entry(self.document_id)
        self.assertIsNotNone(after["deleted_at"])
        self.assertEqual(after["modified_at"], after["deleted_at"])
        self.assertEqual(after["version"], before_entry["version"] + 1)
        self.assertEqual(
            after["current_revision_id"], before_entry["current_revision_id"]
        )
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        self.assertEqual(self.counts()["objects"], before_objects)

        parent_after = self.entry(self.products_id)
        self.assertEqual(parent_after["version"], before_parent["version"] + 1)
        self.assertEqual(parent_after["modified_at"], after["deleted_at"])

    def test_delete_empty_folder_succeeds(self):
        folder = self.service.create_folder(self.scope, self.products_id, "empty")
        before_parent = self.entry(self.products_id)

        self.service.delete_node(
            self.scope, folder.id, expected_version=folder.version
        )

        with self.assertRaises(NotFound):
            self.service.get_node(self.scope, folder.id)
        after_folder = self.entry(folder.id)
        self.assertIsNotNone(after_folder["deleted_at"])
        self.assertEqual(after_folder["version"], folder.version + 1)
        parent_after = self.entry(self.products_id)
        self.assertEqual(parent_after["version"], before_parent["version"] + 1)
        self.assertEqual(parent_after["modified_at"], after_folder["deleted_at"])

    def test_delete_nonempty_folder_is_directory_not_empty(self):
        before_entries = self.snapshot_state(
            [self.products_id, self.document_id, self.fixture.draft_id]
        )
        before_counts = self.counts()
        before_parent = self.entry(self.scope.root_id)

        with self.assertRaises(DirectoryNotEmpty):
            self.service.delete_node(
                self.scope,
                self.products_id,
                expected_version=before_entries[self.products_id]["version"],
            )

        self.assert_delete_failed_cleanly(before_entries, before_counts)
        self.assertEqual(self.entry(self.scope.root_id), before_parent)
        self.assertEqual(
            self.service.read_document(self.scope, self.document_id).content,
            self.fixture.concretecream_content,
        )

    def test_non_recursive_delete_rejects_a_subtree_token(self):
        before = self.entry(self.document_id)

        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                self.document_id,
                expected_version=before["version"],
                expected_subtree_token="token",
            )

        self.assertEqual(self.entry(self.document_id), before)

    def test_stale_expected_version_conflicts_without_changes(self):
        before = self.entry(self.document_id)
        before_counts = self.counts()

        with self.assertRaises(Conflict):
            self.service.delete_node(
                self.scope, self.document_id, expected_version=before["version"] + 5
            )

        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.counts(), before_counts)

    def test_missing_deleted_and_protected_targets(self):
        with self.assertRaises(NotFound):
            self.service.delete_node(self.scope, "missing", expected_version=1)
        with self.assertRaises(NotFound):
            self.service.delete_node(
                self.scope,
                self.fixture.deleted_folder_id,
                expected_version=self.entry(self.fixture.deleted_folder_id)["version"],
            )
        with self.assertRaises(ProtectedNode):
            self.service.delete_node(
                self.scope,
                self.scope.root_id,
                expected_version=self.entry(self.scope.root_id)["version"],
            )
        with self.assertRaises(ProtectedNode):
            self.service.delete_node(
                self.root_scope,
                self.fixture.admin_id,
                expected_version=self.entry(self.fixture.admin_id)["version"],
            )

    def test_respects_access_root_and_branch(self):
        with self.assertRaises(PathOutsideRoot):
            self.service.delete_node(
                self.products_scope(),
                self.fixture.chinese_folder_id,
                expected_version=self.entry(self.fixture.chinese_folder_id)["version"],
            )
        with self.assertRaises(NotFound):
            self.service.delete_node(
                self.scope, self.fixture.dev_document_id, expected_version=1
            )

    def test_rejects_invalid_arguments(self):
        before = self.entry(self.document_id)

        for recursive in (1, 0, "yes", None):
            with self.assertRaises(InvalidArgument):
                self.service.delete_node(
                    self.scope,
                    self.document_id,
                    expected_version=before["version"],
                    recursive=recursive,
                )
        for version in (0, -1, True, "1"):
            with self.assertRaises(InvalidArgument):
                self.service.delete_node(
                    self.scope, self.document_id, expected_version=version
                )
        self.assertEqual(self.entry(self.document_id), before)


class TestRecursiveDelete(ContentDeleteTestCase):
    def test_recursive_requires_a_nonempty_token_and_folder(self):
        ids = self.seed_delete_tree()
        tree_version = self.entry(ids["tree"])["version"]
        deep_version = self.entry(ids["deep"])["version"]

        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=tree_version,
                recursive=True,
            )
        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=tree_version,
                recursive=True,
                expected_subtree_token=None,
            )
        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=tree_version,
                recursive=True,
                expected_subtree_token="",
            )
        # recursive=True on a document is invalid even with a non-empty token.
        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                ids["deep"],
                expected_version=deep_version,
                recursive=True,
                expected_subtree_token="token",
            )
        # A folder without recursive still rejects a token.
        with self.assertRaises(InvalidArgument):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=tree_version,
                expected_subtree_token="token",
            )

    def test_deep_save_invalidates_the_delete_token(self):
        ids = self.seed_delete_tree()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        doc = self.other_service.read_document(self.scope, ids["deep"])
        self.other_service.save_document(
            self.scope, ids["deep"], "并发修改", expected_revision_id=doc.revision_id
        )

        before_entries = self.snapshot_state(ids.values())
        before_counts = self.counts()
        with self.assertRaises(Conflict):
            self.service.delete_node(
                self.scope,
                ids["tree"],
                expected_version=snapshot.version,
                recursive=True,
                expected_subtree_token=snapshot.subtree_token,
            )
        self.assert_delete_failed_cleanly(before_entries, before_counts)
        self.assertEqual(
            self.service.read_document(self.scope, ids["deep"]).content, "并发修改"
        )

    def test_metadata_change_invalidates_the_delete_token(self):
        def mutate(ids, snapshot):
            target = self.service.get_node(self.scope, ids["deep"])
            self.other_service.set_metadata(
                self.scope, ids["deep"], {"tag": "y"}, expected_version=target.version
            )

        self.mutate_then_old_token_conflicts(mutate)

    def test_insert_invalidates_the_delete_token(self):
        def mutate(ids, snapshot):
            self.other_service.create_document(
                self.scope, ids["nested"], "added", content="added-body"
            )

        self.mutate_then_old_token_conflicts(mutate)

    def test_rename_invalidates_the_delete_token(self):
        def mutate(ids, snapshot):
            target = self.service.get_node(self.scope, ids["deep"])
            self.other_service.rename_node(
                self.scope, ids["deep"], "deep-renamed", expected_version=target.version
            )

        self.mutate_then_old_token_conflicts(mutate)

    def test_move_out_invalidates_the_delete_token(self):
        def mutate(ids, snapshot):
            target = self.service.get_node(self.scope, ids["deep"])
            self.other_service.move_node(
                self.scope, ids["deep"], self.products_id, expected_version=target.version
            )

        self.mutate_then_old_token_conflicts(mutate)

    def test_child_delete_invalidates_the_delete_token(self):
        def mutate(ids, snapshot):
            target = self.entry(ids["deep"])
            self.other_service.delete_node(
                self.scope, ids["deep"], expected_version=target["version"]
            )

        self.mutate_then_old_token_conflicts(mutate)

    def test_noop_changes_keep_the_delete_token_valid(self):
        ids = self.seed_delete_tree()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        kept = self.service.get_node(self.scope, ids["kept"])
        self.other_service.rename_node(
            self.scope, ids["kept"], kept.name, expected_version=kept.version
        )
        self.other_service.move_node(
            self.scope, ids["kept"], ids["tree"], expected_version=kept.version
        )
        deep = self.other_service.read_document(self.scope, ids["deep"])
        self.other_service.save_document(
            self.scope,
            ids["deep"],
            deep.content,
            expected_revision_id=deep.revision_id,
        )
        nested = self.service.get_node(self.scope, ids["nested"])
        nested_metadata = self.service.get_metadata(self.scope, ids["nested"])
        self.other_service.set_metadata(
            self.scope, ids["nested"], nested_metadata, expected_version=nested.version
        )

        after = self.service.prepare_delete(self.scope, ids["tree"])
        self.assertEqual(after.subtree_token, snapshot.subtree_token)

        self.service.delete_node(
            self.scope,
            ids["tree"],
            expected_version=snapshot.version,
            recursive=True,
            expected_subtree_token=snapshot.subtree_token,
        )
        for object_id in (ids["tree"], ids["kept"], ids["nested"], ids["deep"]):
            self.assertIsNotNone(self.entry(object_id)["deleted_at"])

    def test_change_outside_the_subtree_keeps_the_token_valid(self):
        ids = self.seed_delete_tree()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        chinese = self.service.get_node(self.scope, self.fixture.chinese_folder_id)
        self.other_service.rename_node(
            self.scope,
            self.fixture.chinese_folder_id,
            "中文 目录",
            expected_version=chinese.version,
        )
        self.other_service.create_folder(self.scope, self.scope.root_id, "other")

        after = self.service.prepare_delete(self.scope, ids["tree"])
        self.assertEqual(after.subtree_token, snapshot.subtree_token)
        self.service.delete_node(
            self.scope,
            ids["tree"],
            expected_version=snapshot.version,
            recursive=True,
            expected_subtree_token=snapshot.subtree_token,
        )
        self.assertIsNotNone(self.entry(ids["tree"])["deleted_at"])

    def test_recursive_delete_marks_subtree_and_touches_parent_once(self):
        ids = self.seed_delete_tree()
        active = [ids["tree"], ids["kept"], ids["nested"], ids["deep"]]
        before_entries = self.snapshot_state(active)
        before_stale = self.entry(ids["stale"])
        before_parent = self.entry(self.products_id)
        before_counts = self.counts()
        before_revisions = {oid: self.revisions(oid) for oid in active}
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])

        self.service.delete_node(
            self.scope,
            ids["tree"],
            expected_version=snapshot.version,
            recursive=True,
            expected_subtree_token=snapshot.subtree_token,
        )

        timestamps = set()
        for object_id in active:
            after = self.entry(object_id)
            self.assertIsNotNone(after["deleted_at"])
            self.assertEqual(
                after["version"], before_entries[object_id]["version"] + 1
            )
            # modified_at and deleted_at share the single operation timestamp.
            self.assertEqual(after["modified_at"], after["deleted_at"])
            self.assertEqual(
                after["created_at"], before_entries[object_id]["created_at"]
            )
            self.assertEqual(
                after["current_revision_id"],
                before_entries[object_id]["current_revision_id"],
            )
            timestamps.add(after["deleted_at"])
        self.assertEqual(len(timestamps), 1)
        operation_time = next(iter(timestamps))

        # The previously deleted descendant is untouched.
        self.assertEqual(self.entry(ids["stale"]), before_stale)

        # The external parent is bumped exactly once with the same timestamp.
        parent_after = self.entry(self.products_id)
        self.assertEqual(parent_after["version"], before_parent["version"] + 1)
        self.assertEqual(parent_after["modified_at"], operation_time)

        # Objects, revisions and entry rows all survive the soft delete.
        after_counts = self.counts()
        self.assertEqual(after_counts["objects"], before_counts["objects"])
        self.assertEqual(
            after_counts["document_revisions"], before_counts["document_revisions"]
        )
        self.assertEqual(after_counts["entries"], before_counts["entries"])
        for object_id in active:
            self.assertEqual(self.revisions(object_id), before_revisions[object_id])

        # The deleted subtree is invisible to every scoped read.
        for object_id in active:
            with self.assertRaises(NotFound):
                self.service.get_node(self.scope, object_id)
        self.assertNotIn(
            ids["tree"],
            [node.id for node in self.service.list_children(self.scope, self.products_id)],
        )
        tree_ids = {
            item.node.id for item in self.service.list_tree(self.scope, self.scope.root_id)
        }
        self.assertFalse(tree_ids & set(active))


class TestDeleteRollback(ContentDeleteTestCase):
    def test_exception_during_soft_delete_rolls_back_every_change(self):
        ids = self.seed_delete_tree()
        before_entries = self.snapshot_state(ids.values())
        before_parent = self.entry(self.products_id)
        before_counts = self.counts()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])
        original = Repository.soft_delete_entries

        def explode(repo_self, object_ids, deleted_at):
            original(repo_self, object_ids, deleted_at)
            raise RuntimeError("injected delete failure")

        with patch.object(Repository, "soft_delete_entries", explode):
            with self.assertRaises(RuntimeError):
                self.service.delete_node(
                    self.scope,
                    ids["tree"],
                    expected_version=snapshot.version,
                    recursive=True,
                    expected_subtree_token=snapshot.subtree_token,
                )

        self.assert_delete_failed_cleanly(before_entries, before_counts)
        self.assertEqual(self.entry(self.products_id), before_parent)

    def test_incorrect_affected_rowcount_rolls_back_every_change(self):
        ids = self.seed_delete_tree()
        before_entries = self.snapshot_state(ids.values())
        before_parent = self.entry(self.products_id)
        before_counts = self.counts()
        snapshot = self.service.prepare_delete(self.scope, ids["tree"])
        original = Repository.soft_delete_entries

        def lie(repo_self, object_ids, deleted_at):
            original(repo_self, object_ids, deleted_at)
            return len(list(object_ids)) + 1

        with patch.object(Repository, "soft_delete_entries", lie):
            with self.assertRaises(Conflict):
                self.service.delete_node(
                    self.scope,
                    ids["tree"],
                    expected_version=snapshot.version,
                    recursive=True,
                    expected_subtree_token=snapshot.subtree_token,
                )

        self.assert_delete_failed_cleanly(before_entries, before_counts)
        self.assertEqual(self.entry(self.products_id), before_parent)


class _LoweredVariableLimitDatabase(Database):
    """Test database whose connections cap the SQLite host-variable count."""

    def __init__(self, path, limit: int) -> None:
        super().__init__(path)
        self._limit = limit

    def _open(self):  # type: ignore[override]
        connection = super()._open()
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, self._limit)
        return connection


class TestSoftDeleteEntriesDao(ContentDeleteTestCase):
    def repo(self, connection) -> Repository:
        return Repository(
            connection,
            workspace_id=self.fixture.workspace_id,
            branch_id=self.fixture.branch_id,
        )

    def test_updates_only_active_rows_and_returns_the_affected_count(self):
        active = [
            self.products_id,
            self.document_id,
            self.fixture.draft_id,
        ]
        before = self.snapshot_state(active)
        before_stale = self.entry(self.fixture.deleted_folder_id)
        now = "2026-05-05T00:00:00+00:00"
        before_objects = self.counts()["objects"]

        with Database(self.fixture.path).transaction(write=True) as connection:
            count = self.repo(connection).soft_delete_entries(set(active), now)

        self.assertEqual(count, len(active))
        for object_id in active:
            after = self.entry(object_id)
            self.assertEqual(after["version"], before[object_id]["version"] + 1)
            self.assertEqual(after["modified_at"], now)
            self.assertEqual(after["deleted_at"], now)
            self.assertEqual(
                after["current_revision_id"],
                before[object_id]["current_revision_id"],
            )
        # An already deleted row is never touched again.
        self.assertEqual(self.entry(self.fixture.deleted_folder_id), before_stale)
        self.assertEqual(self.counts()["objects"], before_objects)

    def test_deduplicates_input_ids(self):
        now = "2026-05-05T00:00:00+00:00"

        with Database(self.fixture.path).transaction(write=True) as connection:
            count = self.repo(connection).soft_delete_entries(
                [self.document_id, self.document_id], now
            )

        self.assertEqual(count, 1)
        after = self.entry(self.document_id)
        self.assertEqual(after["version"], 2)
        self.assertEqual(after["deleted_at"], now)

    def test_empty_input_is_a_noop(self):
        with Database(self.fixture.path).transaction(write=True) as connection:
            count = self.repo(connection).soft_delete_entries(set(), "now")
        self.assertEqual(count, 0)

    def test_batches_many_ids_under_a_lowered_variable_limit(self):
        object_ids = ["bulk-{:03d}".format(index) for index in range(12)]
        with Database(self.fixture.path).transaction(write=True) as connection:
            repo = self.repo(connection)
            for index, object_id in enumerate(object_ids):
                repo.insert_object(object_id, "document", FIXTURE_TIME)
                repo.insert_revision(
                    RevisionRecord(
                        id="rev-" + object_id,
                        workspace_id=self.fixture.workspace_id,
                        object_id=object_id,
                        parent_revision_id=None,
                        content="",
                        created_at=FIXTURE_TIME,
                    )
                )
                repo.insert_entry(
                    EntryRecord(
                        workspace_id=self.fixture.workspace_id,
                        branch_id=self.fixture.branch_id,
                        object_id=object_id,
                        kind="document",
                        parent_id=self.products_id,
                        name=object_id,
                        position=100 + index,
                        version=1,
                        current_revision_id="rev-" + object_id,
                        metadata_json="{}",
                        created_at=FIXTURE_TIME,
                        modified_at=FIXTURE_TIME,
                        deleted_at=None,
                    )
                )
        now = "2026-05-05T00:00:00+00:00"

        with Database(self.fixture.path).transaction(write=True) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 5)
            self.assertEqual(
                connection.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER), 5
            )
            count = self.repo(connection).soft_delete_entries(set(object_ids), now)

        self.assertEqual(count, len(object_ids))
        for object_id in object_ids:
            after = self.entry(object_id)
            self.assertEqual(after["version"], 2)
            self.assertEqual(after["modified_at"], now)
            self.assertEqual(after["deleted_at"], now)

    def test_recursive_delete_batches_under_a_lowered_variable_limit(self):
        folder = self.service.create_folder(self.scope, self.products_id, "bulk")
        child_ids = [
            self.service.create_document(
                self.scope, folder.id, "doc-{:02d}".format(index), content=""
            ).id
            for index in range(12)
        ]
        limited_service = ContentService(
            _LoweredVariableLimitDatabase(self.fixture.path, 5)
        )

        before_versions = {
            object_id: self.entry(object_id)["version"]
            for object_id in [folder.id, *child_ids]
        }
        snapshot = limited_service.prepare_delete(self.scope, folder.id)
        limited_service.delete_node(
            self.scope,
            folder.id,
            expected_version=snapshot.version,
            recursive=True,
            expected_subtree_token=snapshot.subtree_token,
        )

        for object_id in [folder.id, *child_ids]:
            after = self.entry(object_id)
            self.assertIsNotNone(after["deleted_at"])
            self.assertEqual(after["version"], before_versions[object_id] + 1)


if __name__ == "__main__":
    unittest.main()
