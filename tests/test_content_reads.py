"""Behaviour tests for ContentService virtual-path reads and scope checks."""

from __future__ import annotations

import inspect
import sqlite3
import unittest

from src.core import (
    ContentScope,
    InvalidArgument,
    NotDirectory,
    NotFound,
    PathOutsideRoot,
    UnsupportedSchema,
)
from src.services import ContentService
import src.services.content as content_module
from src.storage import Database
from src.storage.records import EntryRecord, RevisionRecord
from src.storage.repository import Repository
from tests.helpers import (
    FIXTURE_TIME,
    ContentReadFixture,
    TempPathTestCase,
    seed_content_read_fixture,
)


class ContentReadTestCase(TempPathTestCase):
    fixture: ContentReadFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.service = ContentService(self.fixture.database)
        self.scope = self.fixture.main_scope

    def products_scope(self) -> ContentScope:
        return ContentScope(
            self.fixture.workspace_id,
            self.fixture.branch_id,
            self.fixture.products_id,
        )

    def _repo(self, connection) -> Repository:
        return Repository(
            connection,
            workspace_id=self.fixture.workspace_id,
            branch_id=self.fixture.branch_id,
        )

    def _soft_delete(self, object_id: str) -> None:
        with Database(self.fixture.path).transaction(write=True) as connection:
            self.assertEqual(
                self._repo(connection).update_entry(
                    object_id,
                    {"deleted_at": FIXTURE_TIME, "version": 2},
                    expected_version=1,
                ),
                1,
            )

    def _insert_deep_folder(self) -> None:
        with Database(self.fixture.path).transaction(write=True) as connection:
            repo = self._repo(connection)
            repo.insert_object("deep", "folder", FIXTURE_TIME)
            repo.insert_entry(
                EntryRecord(
                    workspace_id=self.fixture.workspace_id,
                    branch_id=self.fixture.branch_id,
                    object_id="deep",
                    kind="folder",
                    parent_id=self.fixture.products_id,
                    name="deep",
                    position=2,
                    version=1,
                    current_revision_id=None,
                    metadata_json="{}",
                    created_at=FIXTURE_TIME,
                    modified_at=FIXTURE_TIME,
                    deleted_at=None,
                )
            )

    def _insert_nonfolder_ancestor(self) -> None:
        """Put a document between ``main`` and ``products`` (damaged fixture)."""

        with Database(self.fixture.path).transaction(write=True) as connection:
            repo = self._repo(connection)
            repo.insert_object("mid-doc", "document", FIXTURE_TIME)
            repo.insert_revision(
                RevisionRecord(
                    id="rev-mid-doc",
                    workspace_id=self.fixture.workspace_id,
                    object_id="mid-doc",
                    parent_revision_id=None,
                    content="",
                    created_at=FIXTURE_TIME,
                )
            )
            repo.insert_entry(
                EntryRecord(
                    workspace_id=self.fixture.workspace_id,
                    branch_id=self.fixture.branch_id,
                    object_id="mid-doc",
                    kind="document",
                    parent_id=self.scope.root_id,
                    name="mid-doc",
                    position=3,
                    version=1,
                    current_revision_id="rev-mid-doc",
                    metadata_json="{}",
                    created_at=FIXTURE_TIME,
                    modified_at=FIXTURE_TIME,
                    deleted_at=None,
                )
            )
            self.assertEqual(
                repo.update_entry(
                    self.fixture.products_id,
                    {"parent_id": "mid-doc", "version": 2},
                    expected_version=1,
                ),
                1,
            )

    def _corrupt_metadata(self, value: str) -> None:
        with Database(self.fixture.path).transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET metadata_json = ? WHERE object_id = ?",
                (value, self.fixture.products_id),
            )

    def _state(self):
        with Database(self.fixture.path).transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "workspaces",
                    "objects",
                    "branches",
                    "entries",
                    "document_revisions",
                )
            )
            schema = tuple(
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                ).fetchall()
            )
        return version, counts, schema


class TestNodeReads(ContentReadTestCase):
    def test_get_node_returns_snapshot(self):
        node = self.service.get_node(self.scope, self.fixture.concretecream_id)
        self.assertEqual(node.id, self.fixture.concretecream_id)
        self.assertEqual(node.kind, "document")
        self.assertEqual(node.name, "concretecream")
        self.assertEqual(node.parent_id, self.fixture.products_id)
        self.assertEqual(node.position, 0)
        self.assertEqual(node.version, 1)
        self.assertEqual(node.path, "/products/concretecream")
        self.assertIsNone(node.deleted_at)
        self.assertEqual(dict(node.metadata), {})
        root = self.service.get_node(self.scope, self.scope.root_id)
        self.assertEqual(root.path, "/")
        self.assertEqual(root.name, "main")

    def test_get_path_is_relative_to_access_root(self):
        self.assertEqual(self.service.get_path(self.scope, self.scope.root_id), "/")
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.products_id), "/products"
        )
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.concretecream_id),
            "/products/concretecream",
        )
        self.assertEqual(
            self.service.get_path(self.fixture.root_scope, self.scope.root_id), "/main"
        )
        self.assertEqual(
            self.service.get_path(self.fixture.root_scope, self.fixture.bin_id), "/bin"
        )

    def test_get_metadata_returns_independent_copy(self):
        metadata = self.service.get_metadata(self.scope, self.fixture.products_id)
        self.assertEqual(metadata, {"tag": "x", "nested": {"a": 1}})
        metadata["tag"] = "changed"
        metadata["nested"]["a"] = 2
        self.assertEqual(
            self.service.get_metadata(self.scope, self.fixture.products_id),
            {"tag": "x", "nested": {"a": 1}},
        )
        node = self.service.get_node(self.scope, self.fixture.products_id)
        self.assertEqual(node.metadata["tag"], "x")
        with self.assertRaises(TypeError):
            node.metadata["tag"] = "other"

    def test_get_node_missing_and_out_of_scope(self):
        with self.assertRaises(NotFound):
            self.service.get_node(self.scope, "missing-object")
        with self.assertRaises(PathOutsideRoot):
            self.service.get_node(self.products_scope(), self.scope.root_id)
        with self.assertRaises(PathOutsideRoot):
            self.service.get_node(self.products_scope(), self.fixture.admin_id)


class TestResolvePath(ContentReadTestCase):
    def test_resolve_path_defaults_to_access_root(self):
        self.assertEqual(
            self.service.resolve_path(self.scope, "products").id,
            self.fixture.products_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "products/concretecream").id,
            self.fixture.concretecream_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "/products/concretecream").id,
            self.fixture.concretecream_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "~/products").id,
            self.fixture.products_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "~").id, self.scope.root_id
        )
        self.assertEqual(
            self.service.resolve_path(
                self.scope, "concretecream", cwd_id=self.fixture.products_id
            ).id,
            self.fixture.concretecream_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, ".").id, self.scope.root_id
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "products/..").id,
            self.scope.root_id,
        )

    def test_document_dotdot_is_not_collapsed(self):
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(self.scope, "products/concretecream/../x")
        self.assertEqual(
            len(self.service.list_tree(self.scope, self.scope.root_id, max_depth=0)), 1
        )

    def test_missing_dotdot_and_trailing_slash(self):
        with self.assertRaises(NotFound):
            self.service.resolve_path(self.scope, "missing/../x")
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(self.scope, "products/concretecream/")
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(self.scope, "products/concretecream/.")
        self.assertEqual(
            self.service.resolve_path(self.scope, "products/").id,
            self.fixture.products_id,
        )

    def test_resolve_path_collapses_separators(self):
        self.assertEqual(
            self.service.resolve_path(self.scope, "//products//concretecream").id,
            self.fixture.concretecream_id,
        )
        with self.assertRaises(InvalidArgument):
            self.service.resolve_path(self.scope, "")
        with self.assertRaises(InvalidArgument):
            self.service.resolve_path(self.scope, 123)

    def test_root_dotdot_is_outside_root(self):
        for value in ("..", "/..", "~/.."):
            with self.assertRaises(PathOutsideRoot):
                self.service.resolve_path(self.scope, value)

    def test_resolve_path_supports_chinese_and_spaces(self):
        self.assertEqual(
            self.service.resolve_path(self.scope, "中文 空格/笔记").id,
            self.fixture.notes_id,
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "products/草稿 二").id,
            self.fixture.draft_id,
        )

    def test_relative_cwd_must_be_active_folder(self):
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(
                self.scope, "x", cwd_id=self.fixture.concretecream_id
            )
        with self.assertRaises(NotFound):
            self.service.resolve_path(self.scope, "x", cwd_id="missing")
        with self.assertRaises(NotFound):
            self.service.resolve_path(
                self.scope, "x", cwd_id=self.fixture.deleted_folder_id
            )


class TestScopeRules(ContentReadTestCase):
    def test_scope_root_must_be_active_folder(self):
        document_scope = ContentScope(
            self.fixture.workspace_id,
            self.fixture.branch_id,
            self.fixture.concretecream_id,
        )
        with self.assertRaises(NotDirectory):
            self.service.get_node(document_scope, self.fixture.concretecream_id)
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(document_scope, "/")

        deleted_scope = ContentScope(
            self.fixture.workspace_id,
            self.fixture.branch_id,
            self.fixture.deleted_folder_id,
        )
        with self.assertRaises(NotFound):
            self.service.get_node(deleted_scope, self.fixture.deleted_folder_id)

    def test_cross_branch_and_cross_workspace_are_not_found(self):
        dev_scope = ContentScope(
            self.fixture.workspace_id, self.fixture.dev_branch_id, self.fixture.root_id
        )
        self.assertEqual(
            self.service.get_node(dev_scope, self.fixture.dev_document_id).path,
            "/dev-note",
        )
        with self.assertRaises(NotFound):
            self.service.get_node(dev_scope, self.fixture.concretecream_id)

        missing = ContentScope("no-workspace", "no-branch", "no-root")
        with self.assertRaises(NotFound):
            self.service.get_node(missing, self.fixture.concretecream_id)

        foreign = ContentScope(
            self.fixture.foreign_workspace_id,
            self.fixture.foreign_branch_id,
            self.fixture.foreign_root_id,
        )
        self.assertEqual(
            self.service.get_node(foreign, self.fixture.foreign_document_id).path,
            "/foreign",
        )
        with self.assertRaises(NotFound):
            self.service.get_node(foreign, self.fixture.concretecream_id)

    def test_cyclic_chain_is_rejected_without_hanging(self):
        with Database(self.fixture.path).transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET parent_id = ? WHERE object_id = ?",
                (self.fixture.concretecream_id, self.fixture.products_id),
            )
            connection.execute(
                "UPDATE entries SET parent_id = ? WHERE object_id = ?",
                (self.fixture.products_id, self.fixture.concretecream_id),
            )
        with self.assertRaises((NotFound, PathOutsideRoot)):
            self.service.get_node(self.scope, self.fixture.concretecream_id)


class TestListReads(ContentReadTestCase):
    def test_list_children_ordered_active_only(self):
        children = self.service.list_children(self.scope, self.scope.root_id)
        self.assertEqual(
            [child.id for child in children],
            [self.fixture.products_id, self.fixture.chinese_folder_id],
        )
        self.assertEqual([child.position for child in children], [0, 1])
        with self.assertRaises(NotDirectory):
            self.service.list_children(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotFound):
            self.service.list_children(self.scope, "missing-object")
        with self.assertRaises(PathOutsideRoot):
            self.service.list_children(self.products_scope(), self.scope.root_id)

    def test_list_tree_preorder_and_max_depth(self):
        items = self.service.list_tree(self.scope, self.scope.root_id)
        self.assertEqual(
            [item.node.id for item in items],
            [
                self.scope.root_id,
                self.fixture.products_id,
                self.fixture.concretecream_id,
                self.fixture.draft_id,
                self.fixture.chinese_folder_id,
                self.fixture.notes_id,
            ],
        )
        self.assertEqual([item.depth for item in items], [0, 1, 2, 2, 1, 2])

        only_root = self.service.list_tree(
            self.scope, self.scope.root_id, max_depth=0
        )
        self.assertEqual([item.node.id for item in only_root], [self.scope.root_id])
        self.assertEqual([item.depth for item in only_root], [0])

        level_one = self.service.list_tree(
            self.scope, self.scope.root_id, max_depth=1
        )
        self.assertEqual(
            [item.node.id for item in level_one],
            [
                self.scope.root_id,
                self.fixture.products_id,
                self.fixture.chinese_folder_id,
            ],
        )

    def test_list_tree_rejects_invalid_depth(self):
        for bad_depth in (-1, True, 1.5, "1", object()):
            with self.assertRaises(InvalidArgument):
                self.service.list_tree(
                    self.scope, self.scope.root_id, max_depth=bad_depth
                )


class TestReadSideEffects(ContentReadTestCase):
    def test_reads_do_not_modify_schema_version_or_rows(self):
        before = self._state()
        self.service.get_node(self.scope, self.fixture.concretecream_id)
        self.service.get_path(self.scope, self.fixture.notes_id)
        self.service.resolve_path(self.scope, "products/草稿 二")
        self.service.list_children(self.scope, self.scope.root_id)
        self.service.list_tree(self.scope, self.scope.root_id)
        self.service.get_metadata(self.scope, self.fixture.products_id)
        self.assertEqual(self._state(), before)

    def test_non_wal_target_maps_to_unsupported_schema(self):
        raw = sqlite3.connect(self.fixture.path)
        raw.execute("PRAGMA journal_mode = DELETE")
        raw.close()
        with self.assertRaises(UnsupportedSchema):
            self.service.get_node(self.scope, self.fixture.concretecream_id)

    def test_renamed_node_is_found_by_id_with_new_path(self):
        from src.storage.repository import Repository

        with Database(self.fixture.path).transaction(write=True) as connection:
            repo = Repository(
                connection,
                workspace_id=self.fixture.workspace_id,
                branch_id=self.fixture.branch_id,
            )
            self.assertEqual(
                repo.update_entry(
                    self.fixture.products_id,
                    {"name": "goods", "version": 2},
                    expected_version=1,
                ),
                1,
            )
        node = self.service.get_node(self.scope, self.fixture.products_id)
        self.assertEqual(node.name, "goods")
        self.assertEqual(node.path, "/goods")
        self.assertEqual(
            self.service.get_path(self.scope, self.fixture.concretecream_id),
            "/goods/concretecream",
        )
        self.assertEqual(
            self.service.resolve_path(self.scope, "goods").id,
            self.fixture.products_id,
        )

    def test_service_source_contains_no_sql(self):
        source = inspect.getsource(content_module)
        for token in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "FROM",
            "WHERE",
            "JOIN",
            "PRAGMA",
            "sqlite3",
        ):
            self.assertNotIn(token, source)


class TestAncestorValidation(ContentReadTestCase):
    """Damaged fixtures must not let reads cross a deleted/non-folder ancestor."""

    def test_deleted_ancestor_blocks_id_and_path_reads(self):
        self._insert_deep_folder()
        self._soft_delete(self.fixture.products_id)

        with self.assertRaises(NotFound):
            self.service.get_node(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotFound):
            self.service.get_path(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotFound):
            self.service.get_metadata(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotFound):
            self.service.get_node(self.scope, "deep")
        with self.assertRaises(NotFound):
            self.service.resolve_path(self.scope, "x", cwd_id="deep")

    def test_nonfolder_ancestor_is_not_directory(self):
        self._insert_deep_folder()
        self._insert_nonfolder_ancestor()

        with self.assertRaises(NotDirectory):
            self.service.get_node(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotDirectory):
            self.service.get_path(self.scope, "deep")
        with self.assertRaises(NotDirectory):
            self.service.get_metadata(self.scope, self.fixture.concretecream_id)
        with self.assertRaises(NotDirectory):
            self.service.resolve_path(self.scope, "x", cwd_id="deep")

    def test_tree_snapshots_validate_each_node(self):
        self._insert_deep_folder()
        self._insert_nonfolder_ancestor()

        with self.assertRaises(NotDirectory):
            self.service.list_tree(self.scope, self.scope.root_id)

    def test_deleted_ancestor_is_not_reported_as_outside_root(self):
        # A deleted ancestor inside the scope is NotFound, never PathOutsideRoot.
        self._soft_delete(self.fixture.products_id)
        with self.assertRaises(NotFound):
            self.service.get_node(self.products_scope(), self.fixture.products_id)


class TestStoredMetadataCorruption(ContentReadTestCase):
    def test_nan_and_infinity_stored_metadata_are_rejected(self):
        for value in (
            '{"bad": NaN}',
            '{"bad": Infinity}',
            '{"bad": -Infinity}',
            '{"nested": {"bad": NaN}}',
        ):
            self._corrupt_metadata(value)
            with self.assertRaises(UnsupportedSchema):
                self.service.get_metadata(self.scope, self.fixture.products_id)
            with self.assertRaises(UnsupportedSchema):
                self.service.get_node(self.scope, self.fixture.products_id)

    def test_non_object_invalid_and_reserved_stored_metadata_are_rejected(self):
        for value in ('[1, 2]', '{"bad": }', '{"id": "reserved"}'):
            self._corrupt_metadata(value)
            with self.assertRaises(UnsupportedSchema):
                self.service.get_metadata(self.scope, self.fixture.products_id)
            with self.assertRaises(UnsupportedSchema):
                self.service.get_node(self.scope, self.fixture.products_id)


if __name__ == "__main__":
    unittest.main()
