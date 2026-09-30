"""Behaviour tests for ContentService rename and cross-directory move.

Every test starts from the initialized read fixture of :mod:`tests.helpers`, so
rename and move run against a real default workspace tree.  The tests pin the
structural contract: object ids and document revisions never change, a move to
a different parent appends to that parent, a same-parent rename keeps its
position, a true no-op neither bumps versions nor rewrites the timestamp, and
only the active-sibling name index is translated to :class:`AlreadyExists`.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.core import (
    AlreadyExists,
    Conflict,
    ContentScope,
    InvalidArgument,
    InvalidMove,
    InvalidName,
    NotDirectory,
    NotFound,
    PathOutsideRoot,
    ProtectedNode,
)
from src.services import ContentService
from src.storage.errors import ConstraintError
from src.storage.repository import Repository
from tests.helpers import (
    ContentReadFixture,
    TempPathTestCase,
    entry_state,
    revision_state,
    seed_content_read_fixture,
    table_counts,
)


class ContentMoveTestCase(TempPathTestCase):
    """Shared fixture and raw-observation helpers for the move tests."""

    fixture: ContentReadFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope
        self.root_scope = self.fixture.root_scope
        self.products_id = self.fixture.products_id
        self.document_id = self.fixture.concretecream_id

    def counts(self) -> dict:
        return table_counts(self.fixture.path)

    def entry(self, object_id: str) -> dict | None:
        return entry_state(self.fixture.path, object_id)

    def revisions(self, object_id: str) -> list:
        return revision_state(self.fixture.path, object_id)

    def node(self, object_id: str):
        return self.service.get_node(self.root_scope, object_id)

    def sibling_scope(self) -> ContentScope:
        return ContentScope(
            self.fixture.workspace_id,
            self.fixture.branch_id,
            self.fixture.products_id,
        )


class TestRenameNode(ContentMoveTestCase):
    def test_rename_folder_keeps_ids_descendant_paths_and_revisions(self):
        before = self.service.get_node(self.scope, self.products_id)
        before_child = self.service.get_node(self.scope, self.document_id)
        before_revisions = self.revisions(self.document_id)
        before_root = self.entry(self.scope.root_id)
        before_workspace_root = self.entry(self.fixture.root_id)

        after = self.service.rename_node(
            self.scope, self.products_id, "goods", expected_version=before.version
        )

        self.assertEqual(after.id, self.products_id)
        self.assertEqual(after.kind, "folder")
        self.assertEqual(after.name, "goods")
        self.assertEqual(after.parent_id, before.parent_id)
        self.assertEqual(after.position, before.position)
        self.assertEqual(after.version, before.version + 1)
        self.assertEqual(after.path, "/goods")
        self.assertEqual(after.created_at, before.created_at)
        self.assertNotEqual(after.modified_at, before.modified_at)

        # Descendants keep their ids and only gain the new displayed path.
        child = self.service.get_node(self.scope, self.document_id)
        self.assertEqual(child.id, before_child.id)
        self.assertEqual(child.path, "/goods/concretecream")
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.draft_id),
            "/goods/草稿 二",
        )
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        self.assertEqual(self.service.read_document(self.scope, self.document_id).content, "正文内容")

        # The direct parent (main) is the only ancestor touched.
        root = self.entry(self.scope.root_id)
        self.assertEqual(root["version"], before_root["version"] + 1)
        self.assertEqual(root["modified_at"], after.modified_at)
        # Renaming a grandchild never propagates to the workspace root.
        self.assertEqual(self.entry(self.fixture.root_id), before_workspace_root)

    def test_rename_document_keeps_content_and_revision(self):
        before = self.service.read_document(self.scope, self.document_id)
        before_revisions = self.revisions(self.document_id)

        after = self.service.rename_node(
            self.scope, self.document_id, "final", expected_version=before.version
        )

        self.assertEqual(after.id, before.id)
        self.assertEqual(after.kind, "document")
        self.assertEqual(after.name, "final")
        self.assertEqual(after.path, "/products/final")
        self.assertEqual(after.version, before.version + 1)
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        # The returned NodeSnapshot intentionally carries no body; reading the
        # document by id proves the revision pointer and content are untouched.
        read = self.service.read_document(self.scope, self.document_id)
        self.assertEqual(read.content, before.content)
        self.assertEqual(read.revision_id, before.revision_id)

    def test_rename_changes_only_entry_identity_columns(self):
        before = self.entry(self.document_id)
        before_revisions = self.revisions(self.document_id)
        counts = self.counts()

        self.service.rename_node(
            self.scope, self.document_id, "renamed", expected_version=1
        )

        after = self.entry(self.document_id)
        self.assertEqual(after["object_id"], before["object_id"])
        self.assertEqual(after["workspace_id"], before["workspace_id"])
        self.assertEqual(after["branch_id"], before["branch_id"])
        self.assertEqual(after["parent_id"], before["parent_id"])
        self.assertEqual(after["position"], before["position"])
        self.assertEqual(after["current_revision_id"], before["current_revision_id"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        self.assertEqual(self.counts(), counts)

    def test_rename_updates_paths_and_keeps_cwd_by_id(self):
        before = self.service.get_node(self.scope, self.products_id)
        self.service.rename_node(
            self.scope, self.products_id, "goods", expected_version=before.version
        )

        self.assertEqual(self.service.get_path(self.scope, self.products_id), "/goods")
        self.assertEqual(
            self.service.resolve_path(self.scope, "goods").id, self.products_id
        )
        with self.assertRaises(NotFound):
            self.service.resolve_path(self.scope, "products")
        # A session cwd stored as an object id keeps working after the rename.
        self.assertEqual(
            self.service.resolve_path(
                self.scope, "concretecream", cwd_id=self.products_id
            ).id,
            self.document_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, ".", cwd_id=self.products_id).path,
            "/goods",
        )

    def test_same_name_rename_is_a_true_noop(self):
        before = self.service.get_node(self.scope, self.document_id)
        before_parent = self.entry(self.products_id)
        before_revisions = self.revisions(self.document_id)
        counts = self.counts()

        after = self.service.rename_node(
            self.scope, self.document_id, before.name, expected_version=before.version
        )

        self.assertEqual(after, before)
        self.assertEqual(self.entry(self.products_id), before_parent)
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        self.assertEqual(self.counts(), counts)

    def test_stale_version_conflicts_even_for_same_name(self):
        before = self.service.get_node(self.scope, self.document_id)
        before_entry = self.entry(self.document_id)

        with self.assertRaises(Conflict):
            self.service.rename_node(
                self.scope,
                self.document_id,
                before.name,
                expected_version=before.version + 1,
            )
        with self.assertRaises(Conflict):
            self.service.rename_node(
                self.scope,
                self.document_id,
                "other",
                expected_version=before.version + 1,
            )

        self.assertEqual(self.entry(self.document_id), before_entry)

    def test_rename_to_existing_sibling_raises_already_exists(self):
        before = self.entry(self.document_id)
        before_other = self.entry(self.fixture.draft_id)
        counts = self.counts()

        with self.assertRaises(AlreadyExists):
            self.service.rename_node(
                self.scope, self.document_id, "草稿 二", expected_version=1
            )

        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.entry(self.fixture.draft_id), before_other)
        self.assertEqual(self.counts(), counts)

    def test_rename_protected_objects_fail(self):
        protected = (
            (self.root_scope, self.fixture.root_id, "root"),
            (self.root_scope, self.fixture.main_id, "main"),
            (self.root_scope, self.fixture.admin_id, "admin"),
            (self.root_scope, self.fixture.resource_id, "resource"),
            (self.root_scope, self.fixture.bin_id, "bin"),
        )
        before = {oid: self.entry(oid) for _, oid, _ in protected}
        for scope, object_id, _label in protected:
            version = self.service.get_node(scope, object_id).version
            with self.assertRaises(ProtectedNode):
                self.service.rename_node(
                    scope, object_id, "changed", expected_version=version
                )
        for _, object_id, _label in protected:
            self.assertEqual(self.entry(object_id), before[object_id])

    def test_same_name_elsewhere_is_not_protected(self):
        created = self.service.create_folder(self.scope, self.products_id, "main")
        renamed = self.service.rename_node(
            self.scope, created.id, "admin", expected_version=created.version
        )
        self.assertEqual(renamed.name, "admin")
        self.assertEqual(renamed.id, created.id)

    def test_rename_missing_and_deleted_are_not_found(self):
        counts = self.counts()
        with self.assertRaises(NotFound):
            self.service.rename_node(self.scope, "missing", "x", expected_version=1)
        with self.assertRaises(NotFound):
            self.service.rename_node(
                self.scope, self.fixture.deleted_folder_id, "x", expected_version=1
            )
        self.assertEqual(self.counts(), counts)

    def test_rename_rejects_invalid_arguments(self):
        before = self.entry(self.document_id)
        with self.assertRaises(InvalidArgument):
            self.service.rename_node(self.scope, 123, "x", expected_version=1)
        for bad_version in (True, 0, -1, "1", 1.0, None):
            with self.assertRaises(InvalidArgument):
                self.service.rename_node(
                    self.scope, self.document_id, "x", expected_version=bad_version
                )
        for bad_name in ("", ".", "..", "a/b", "a\\b", "x\x00y", None, 123):
            with self.assertRaises(InvalidName):
                self.service.rename_node(
                    self.scope, self.document_id, bad_name, expected_version=1
                )
        self.assertEqual(self.entry(self.document_id), before)

    def test_rename_name_race_maps_to_already_exists(self):
        before = self.entry(self.document_id)
        counts = self.counts()
        with patch.object(Repository, "find_child", return_value=None):
            with self.assertRaises(AlreadyExists):
                self.service.rename_node(
                    self.scope, self.document_id, "草稿 二", expected_version=1
                )
        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.counts(), counts)

    def test_rename_other_integrity_error_is_not_swallowed(self):
        position_error = ConstraintError(
            "UNIQUE constraint failed: entries.workspace_id, entries.branch_id, "
            "entries.parent_id, entries.position",
            constraint="unique",
            details={
                "sqlite_message": "UNIQUE constraint failed: entries.workspace_id, "
                "entries.branch_id, entries.parent_id, entries.position"
            },
        )
        with patch.object(Repository, "update_entry", side_effect=position_error):
            with self.assertRaises(ConstraintError) as caught:
                self.service.rename_node(
                    self.scope, self.document_id, "final", expected_version=1
                )
        self.assertNotIsInstance(caught.exception, AlreadyExists)

    def test_rename_failure_rolls_back(self):
        before = self.entry(self.document_id)
        before_parent = self.entry(self.products_id)
        counts = self.counts()
        with patch.object(
            Repository, "update_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.rename_node(
                    self.scope, self.document_id, "final", expected_version=1
                )
        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.entry(self.products_id), before_parent)
        self.assertEqual(self.counts(), counts)


class TestMoveNode(ContentMoveTestCase):
    def test_move_document_across_parents_appends_position(self):
        before_doc = self.service.read_document(self.scope, self.document_id)
        before_old_parent = self.entry(self.products_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        before_revisions = self.revisions(self.document_id)
        counts = self.counts()

        after = self.service.move_node(
            self.scope,
            self.document_id,
            self.fixture.chinese_folder_id,
            expected_version=before_doc.version,
        )

        self.assertEqual(after.id, self.document_id)
        self.assertEqual(after.kind, "document")
        self.assertEqual(after.parent_id, self.fixture.chinese_folder_id)
        self.assertEqual(after.position, 1)  # notes already occupies position 0
        self.assertEqual(after.version, before_doc.version + 1)
        self.assertEqual(after.path, "/中文 空格/concretecream")
        self.assertEqual(self.revisions(self.document_id), before_revisions)
        read = self.service.read_document(self.scope, self.document_id)
        self.assertEqual(read.content, before_doc.content)
        self.assertEqual(read.revision_id, before_doc.revision_id)
        self.assertEqual(self.counts(), counts)

        old_parent = self.entry(self.products_id)
        new_parent = self.entry(self.fixture.chinese_folder_id)
        self.assertEqual(old_parent["version"], before_old_parent["version"] + 1)
        self.assertEqual(new_parent["version"], before_new_parent["version"] + 1)
        self.assertEqual(old_parent["modified_at"], after.modified_at)
        self.assertEqual(new_parent["modified_at"], after.modified_at)

    def test_move_folder_cross_parents_updates_descendant_paths(self):
        before_products = self.node(self.products_id)
        before_child = self.node(self.document_id)
        before_old_parent = self.entry(self.scope.root_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        before_child_revisions = self.revisions(self.document_id)
        counts = self.counts()

        after = self.service.move_node(
            self.scope,
            self.products_id,
            self.fixture.chinese_folder_id,
            expected_version=before_products.version,
        )

        self.assertEqual(after.id, self.products_id)
        self.assertEqual(after.parent_id, self.fixture.chinese_folder_id)
        self.assertEqual(after.position, 1)
        self.assertEqual(after.path, "/中文 空格/products")
        # Stable ids and descendant display paths change naturally.
        self.assertEqual(self.node(self.products_id).id, self.products_id)
        self.assertEqual(self.node(self.document_id).id, before_child.id)
        self.assertEqual(
            self.service.get_path(self.scope, self.document_id),
            "/中文 空格/products/concretecream",
        )
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.draft_id),
            "/中文 空格/products/草稿 二",
        )
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.notes_id),
            "/中文 空格/笔记",
        )
        self.assertEqual(self.revisions(self.document_id), before_child_revisions)
        self.assertEqual(self.counts(), counts)

        old_parent = self.entry(self.scope.root_id)
        new_parent = self.entry(self.fixture.chinese_folder_id)
        self.assertEqual(old_parent["version"], before_old_parent["version"] + 1)
        self.assertEqual(new_parent["version"], before_new_parent["version"] + 1)
        self.assertEqual(old_parent["modified_at"], after.modified_at)
        self.assertEqual(new_parent["modified_at"], after.modified_at)

    def test_move_same_parent_with_new_name_is_a_rename(self):
        before = self.service.get_node(self.scope, self.document_id)
        before_parent = self.entry(self.products_id)
        before_revisions = self.revisions(self.document_id)

        after = self.service.move_node(
            self.scope,
            self.document_id,
            self.products_id,
            expected_version=before.version,
            name="final",
        )

        self.assertEqual(after.id, self.document_id)
        self.assertEqual(after.parent_id, self.products_id)
        self.assertEqual(after.position, before.position)  # order is preserved
        self.assertEqual(after.name, "final")
        self.assertEqual(after.path, "/products/final")
        self.assertEqual(self.revisions(self.document_id), before_revisions)

        parent = self.entry(self.products_id)
        # The single parent is touched exactly once.
        self.assertEqual(parent["version"], before_parent["version"] + 1)
        self.assertEqual(parent["modified_at"], after.modified_at)

    def test_same_parent_same_name_move_is_noop(self):
        before = self.service.get_node(self.scope, self.document_id)
        after = self.service.move_node(
            self.scope,
            before.id,
            before.parent_id,
            expected_version=before.version,
        )
        self.assertEqual(after, before)

    def test_same_parent_move_with_equal_name_is_noop(self):
        before = self.service.get_node(self.scope, self.document_id)
        before_parent = self.entry(self.products_id)
        counts = self.counts()

        after = self.service.move_node(
            self.scope,
            self.document_id,
            self.products_id,
            expected_version=before.version,
            name=before.name,
        )

        self.assertEqual(after, before)
        self.assertEqual(self.entry(self.products_id), before_parent)
        self.assertEqual(self.counts(), counts)

    def test_move_stale_version_conflicts_even_for_noop(self):
        before = self.service.get_node(self.scope, self.document_id)
        before_entry = self.entry(self.document_id)

        with self.assertRaises(Conflict):
            self.service.move_node(
                self.scope,
                self.document_id,
                before.parent_id,
                expected_version=before.version + 1,
            )

        self.assertEqual(self.entry(self.document_id), before_entry)

    def test_move_does_not_bump_grandparent(self):
        before_root = self.entry(self.scope.root_id)
        before_workspace_root = self.entry(self.fixture.root_id)
        before_doc = self.service.get_node(self.scope, self.document_id)

        self.service.move_node(
            self.scope,
            self.document_id,
            self.fixture.chinese_folder_id,
            expected_version=before_doc.version,
        )

        self.assertEqual(self.entry(self.scope.root_id), before_root)
        self.assertEqual(self.entry(self.fixture.root_id), before_workspace_root)

    def test_move_one_operation_uses_single_utc_time(self):
        before = self.service.get_node(self.scope, self.document_id)
        after = self.service.move_node(
            self.scope,
            self.document_id,
            self.fixture.chinese_folder_id,
            expected_version=before.version,
        )

        self.assertEqual(after.created_at, before.created_at)
        self.assertEqual(self.entry(self.products_id)["modified_at"], after.modified_at)
        self.assertEqual(
            self.entry(self.fixture.chinese_folder_id)["modified_at"],
            after.modified_at,
        )

    def test_move_to_self_and_descendant_fails(self):
        created = self.service.create_folder(self.scope, self.products_id, "sub")
        before = self.service.get_node(self.scope, self.products_id)
        before_entry = self.entry(self.products_id)
        before_created = self.entry(created.id)

        for target in (self.products_id, created.id):
            with self.assertRaises(InvalidMove):
                self.service.move_node(
                    self.scope, self.products_id, target, expected_version=before.version
                )

        self.assertEqual(self.entry(self.products_id), before_entry)
        self.assertEqual(self.entry(created.id), before_created)

    def test_move_to_document_target_is_not_directory(self):
        before = self.service.get_node(self.scope, self.document_id)
        with self.assertRaises(NotDirectory):
            self.service.move_node(
                self.scope,
                self.document_id,
                self.fixture.draft_id,
                expected_version=before.version,
            )
        with self.assertRaises(NotDirectory):
            self.service.move_node(
                self.scope,
                self.products_id,
                self.document_id,
                expected_version=self.service.get_node(
                    self.scope, self.products_id
                ).version,
            )

    def test_move_to_existing_name_raises_already_exists(self):
        before = self.entry(self.document_id)
        before_target = self.entry(self.fixture.notes_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        before_old_parent = self.entry(self.products_id)

        with self.assertRaises(AlreadyExists):
            self.service.move_node(
                self.scope,
                self.document_id,
                self.fixture.chinese_folder_id,
                expected_version=1,
                name="笔记",
            )

        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.entry(self.fixture.notes_id), before_target)
        self.assertEqual(self.entry(self.fixture.chinese_folder_id), before_new_parent)
        self.assertEqual(self.entry(self.products_id), before_old_parent)

    def test_move_outside_access_root_fails(self):
        products_scope = self.sibling_scope()
        before = self.service.get_node(products_scope, self.document_id)
        before_entry = self.entry(self.document_id)
        with self.assertRaises(PathOutsideRoot):
            self.service.move_node(
                products_scope,
                self.document_id,
                self.fixture.admin_id,
                expected_version=before.version,
            )
        with self.assertRaises(NotFound):
            self.service.move_node(
                self.scope,
                self.document_id,
                self.fixture.foreign_root_id,
                expected_version=before.version,
            )
        self.assertEqual(self.entry(self.document_id), before_entry)

    def test_move_protected_objects_fail(self):
        before_main = self.entry(self.scope.root_id)
        before_admin = self.entry(self.fixture.admin_id)
        before_products = self.entry(self.products_id)

        with self.assertRaises(ProtectedNode):
            self.service.move_node(
                self.root_scope,
                self.scope.root_id,
                self.fixture.admin_id,
                expected_version=self.service.get_node(
                    self.root_scope, self.scope.root_id
                ).version,
            )
        with self.assertRaises(ProtectedNode):
            self.service.move_node(
                self.root_scope,
                self.fixture.admin_id,
                self.products_id,
                expected_version=self.service.get_node(
                    self.root_scope, self.fixture.admin_id
                ).version,
            )

        self.assertEqual(self.entry(self.scope.root_id), before_main)
        self.assertEqual(self.entry(self.fixture.admin_id), before_admin)
        self.assertEqual(self.entry(self.products_id), before_products)

    def test_move_same_name_elsewhere_is_not_protected(self):
        created = self.service.create_folder(self.scope, self.products_id, "main")
        moved = self.service.move_node(
            self.scope,
            created.id,
            self.fixture.chinese_folder_id,
            expected_version=created.version,
        )
        self.assertEqual(moved.name, "main")
        self.assertEqual(moved.parent_id, self.fixture.chinese_folder_id)
        self.assertEqual(moved.id, created.id)

    def test_move_missing_and_deleted_are_not_found(self):
        counts = self.counts()
        with self.assertRaises(NotFound):
            self.service.move_node(
                self.scope, "missing", self.products_id, expected_version=1
            )
        with self.assertRaises(NotFound):
            self.service.move_node(
                self.scope,
                self.fixture.deleted_folder_id,
                self.products_id,
                expected_version=1,
            )
        with self.assertRaises(NotFound):
            self.service.move_node(
                self.scope, self.document_id, "missing", expected_version=1
            )
        with self.assertRaises(NotFound):
            self.service.move_node(
                self.scope,
                self.document_id,
                self.fixture.deleted_folder_id,
                expected_version=1,
            )
        self.assertEqual(self.counts(), counts)

    def test_move_rejects_invalid_arguments(self):
        before = self.entry(self.document_id)
        with self.assertRaises(InvalidArgument):
            self.service.move_node(self.scope, 123, self.products_id, expected_version=1)
        with self.assertRaises(InvalidArgument):
            self.service.move_node(self.scope, self.document_id, 123, expected_version=1)
        for bad_version in (True, 0, -1, "1", 1.0, None):
            with self.assertRaises(InvalidArgument):
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.products_id,
                    expected_version=bad_version,
                )
        for bad_name in ("", ".", "..", "a/b", "a\\b", "x\x00y"):
            with self.assertRaises(InvalidName):
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.fixture.chinese_folder_id,
                    expected_version=1,
                    name=bad_name,
                )
        self.assertEqual(self.entry(self.document_id), before)

    def test_move_name_race_maps_to_already_exists(self):
        before = self.entry(self.document_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        with patch.object(Repository, "find_child", return_value=None):
            with self.assertRaises(AlreadyExists):
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.fixture.chinese_folder_id,
                    expected_version=1,
                    name="笔记",
                )
        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.entry(self.fixture.chinese_folder_id), before_new_parent)

    def test_move_position_constraint_is_not_swallowed(self):
        before = self.entry(self.document_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        with patch.object(Repository, "next_position", return_value=0):
            with self.assertRaises(ConstraintError) as caught:
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.fixture.chinese_folder_id,
                    expected_version=1,
                )
        self.assertNotIsInstance(caught.exception, AlreadyExists)
        self.assertEqual(self.entry(self.document_id), before)
        self.assertEqual(self.entry(self.fixture.chinese_folder_id), before_new_parent)

    def test_source_update_failure_rolls_back_move(self):
        before_source = self.entry(self.document_id)
        before_old_parent = self.entry(self.products_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        counts = self.counts()
        with patch.object(
            Repository, "update_entry", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.fixture.chinese_folder_id,
                    expected_version=1,
                )
        self.assertEqual(self.entry(self.document_id), before_source)
        self.assertEqual(self.entry(self.products_id), before_old_parent)
        self.assertEqual(self.entry(self.fixture.chinese_folder_id), before_new_parent)
        self.assertEqual(self.counts(), counts)

    def test_parent_update_failure_rolls_back_move(self):
        before_source = self.entry(self.document_id)
        before_old_parent = self.entry(self.products_id)
        before_new_parent = self.entry(self.fixture.chinese_folder_id)
        counts = self.counts()
        with patch.object(
            Repository, "touch_entries", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                self.service.move_node(
                    self.scope,
                    self.document_id,
                    self.fixture.chinese_folder_id,
                    expected_version=1,
                )
        self.assertEqual(self.entry(self.document_id), before_source)
        self.assertEqual(self.entry(self.products_id), before_old_parent)
        self.assertEqual(self.entry(self.fixture.chinese_folder_id), before_new_parent)
        self.assertEqual(self.counts(), counts)

    def test_move_keeps_document_revision_and_parent_identity(self):
        before_revision = self.service.read_document(self.scope, self.document_id)
        before = self.entry(self.document_id)
        self.service.move_node(
            self.scope,
            self.document_id,
            self.fixture.chinese_folder_id,
            expected_version=1,
            name="moved",
        )
        after = self.entry(self.document_id)
        self.assertEqual(after["object_id"], before["object_id"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["current_revision_id"], before["current_revision_id"])
        read = self.service.read_document(self.scope, self.document_id)
        self.assertEqual(read.content, before_revision.content)
        self.assertEqual(read.revision_id, before_revision.revision_id)
        self.assertEqual(read.path, "/中文 空格/moved")


if __name__ == "__main__":
    unittest.main()
