"""Behaviour tests for the scoped SQLite repository and conditional writes."""

from __future__ import annotations

import inspect
import unittest

from src.storage import ConstraintError, Database
from src.storage import repository as repository_module
from src.storage.records import EntryRecord, RevisionRecord
from src.storage.repository import Repository
from tests.helpers import (
    FIXTURE_TIME,
    RepositoryFixture,
    TempPathTestCase,
    seed_repository_fixture,
)

T2 = "2026-02-02T00:00:00+00:00"


class RepositoryTestCase(TempPathTestCase):
    """Seed the two-workspace / two-branch fixture before every test."""

    fixture: RepositoryFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_repository_fixture(self.temp_path())
        self.database = Database(self.fixture.path)

    def repo(self, connection, *, branch_id: str | None = None) -> Repository:
        return Repository(
            connection,
            workspace_id=self.fixture.workspace_id,
            branch_id=branch_id or self.fixture.branch_id,
        )

    def other_branch_repo(self, connection) -> Repository:
        return self.repo(connection, branch_id=self.fixture.other_branch_id)


class TestScopedReads(RepositoryTestCase):
    def test_get_entry_returns_active_and_deleted_records(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            record = repo.get_entry(self.fixture.document_id)
            self.assertIsNotNone(record)
            self.assertEqual(record.name, self.fixture.document_name)
            self.assertEqual(record.version, self.fixture.document_version)
            self.assertEqual(record.kind, "document")
            self.assertEqual(record.metadata_json, "{}")
            self.assertIsNone(record.deleted_at)

            deleted = repo.get_entry(self.fixture.deleted_document_id)
            self.assertIsNotNone(deleted)
            self.assertIsNotNone(deleted.deleted_at)

            self.assertIsNone(repo.get_entry("missing-object"))

    def test_same_object_is_branch_scoped(self):
        with self.database.transaction() as connection:
            first = self.repo(connection).get_entry(self.fixture.document_id)
            second = self.other_branch_repo(connection).get_entry(
                self.fixture.document_id
            )
            self.assertEqual(first.name, self.fixture.document_name)
            self.assertEqual(second.name, self.fixture.other_branch_name)
            self.assertEqual(first.version, self.fixture.document_version)
            self.assertEqual(second.version, self.fixture.other_branch_version)

    def test_cross_workspace_lookup_returns_none(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            self.assertIsNone(repo.get_entry(self.fixture.foreign_document_id))
            foreign = Repository(
                connection,
                workspace_id=self.fixture.other_workspace_id,
                branch_id=self.fixture.foreign_branch_id,
            )
            self.assertIsNone(foreign.get_entry(self.fixture.document_id))

    def test_get_branch_root_id(self):
        with self.database.transaction() as connection:
            self.assertEqual(
                self.repo(connection).get_branch_root_id(), self.fixture.root_id
            )
            missing = Repository(
                connection, workspace_id="no-workspace", branch_id="no-branch"
            )
            self.assertIsNone(missing.get_branch_root_id())

    def test_find_child_matches_name_and_handles_null_parent(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            child = repo.find_child(self.fixture.folder_id, self.fixture.document_name)
            self.assertIsNotNone(child)
            self.assertEqual(child.object_id, self.fixture.document_id)
            self.assertIsNone(repo.find_child(self.fixture.folder_id, "gone"))
            self.assertIsNone(repo.find_child(self.fixture.root_id, "only-other"))
            root = repo.find_child(None, "")
            self.assertIsNotNone(root)
            self.assertEqual(root.object_id, self.fixture.root_id)

    def test_list_children_is_ordered_and_excludes_deleted(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            self.assertEqual(
                [row.object_id for row in repo.list_children(self.fixture.folder_id)],
                [
                    self.fixture.document_id,
                    self.fixture.sibling_a_id,
                    self.fixture.sibling_b_id,
                ],
            )
            self.assertEqual(
                [row.object_id for row in repo.list_children(self.fixture.root_id)],
                [self.fixture.folder_id],
            )
            other = self.other_branch_repo(connection)
            self.assertEqual(
                [row.object_id for row in other.list_children(self.fixture.root_id)],
                [self.fixture.document_id, self.fixture.other_branch_only_id],
            )

    def test_ancestors_returns_self_to_root(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            self.assertEqual(
                [row.object_id for row in repo.ancestors(self.fixture.sibling_a_id)],
                [
                    self.fixture.sibling_a_id,
                    self.fixture.folder_id,
                    self.fixture.root_id,
                ],
            )
            self.assertEqual(
                [row.object_id for row in repo.ancestors(self.fixture.root_id)],
                [self.fixture.root_id],
            )
            self.assertEqual(repo.ancestors("missing-object"), [])

    def test_ancestors_terminates_on_cyclic_parent_chain(self):
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET parent_id = ? WHERE workspace_id = ? "
                "AND branch_id = ? AND object_id = ?",
                (
                    self.fixture.sibling_b_id,
                    self.fixture.workspace_id,
                    self.fixture.branch_id,
                    self.fixture.folder_id,
                ),
            )
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            chain = [row.object_id for row in repo.ancestors(self.fixture.document_id)]
        self.assertEqual(
            chain,
            [
                self.fixture.document_id,
                self.fixture.folder_id,
                self.fixture.sibling_b_id,
            ],
        )

    def test_subtree_is_active_preorder(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            self.assertEqual(
                [row.object_id for row in repo.subtree(self.fixture.root_id)],
                [
                    self.fixture.root_id,
                    self.fixture.folder_id,
                    self.fixture.document_id,
                    self.fixture.sibling_a_id,
                    self.fixture.sibling_b_id,
                ],
            )
            self.assertEqual(
                [row.object_id for row in repo.subtree(self.fixture.folder_id)],
                [
                    self.fixture.folder_id,
                    self.fixture.document_id,
                    self.fixture.sibling_a_id,
                    self.fixture.sibling_b_id,
                ],
            )
            self.assertEqual(repo.subtree(self.fixture.deleted_document_id), [])

    def test_subtree_terminates_on_cycle(self):
        with self.database.transaction(write=True) as connection:
            connection.execute(
                "UPDATE entries SET parent_id = ? WHERE workspace_id = ? "
                "AND branch_id = ? AND object_id = ?",
                (
                    self.fixture.sibling_b_id,
                    self.fixture.workspace_id,
                    self.fixture.branch_id,
                    self.fixture.folder_id,
                ),
            )
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            ids = [row.object_id for row in repo.subtree(self.fixture.folder_id)]
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)

    def test_next_position_uses_active_children_only(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            self.assertEqual(repo.next_position(self.fixture.folder_id), 3)
            self.assertEqual(repo.next_position(self.fixture.root_id), 1)
            self.assertEqual(repo.next_position(self.fixture.document_id), 0)

    def test_get_revision_is_workspace_and_object_scoped(self):
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            current = repo.get_revision(
                self.fixture.document_id, self.fixture.revision_id
            )
            self.assertIsInstance(current, RevisionRecord)
            self.assertEqual(current.content, "alpha")
            self.assertIsNone(current.parent_revision_id)
            # Revision rows are shared by object across branches of a workspace.
            shared = repo.get_revision(
                self.fixture.document_id, self.fixture.other_revision_id
            )
            self.assertEqual(shared.content, "beta")
            self.assertEqual(shared.parent_revision_id, self.fixture.revision_id)
            self.assertIsNone(
                repo.get_revision(self.fixture.document_id, self.fixture.foreign_revision_id)
            )
            self.assertIsNone(
                repo.get_revision(self.fixture.foreign_document_id, self.fixture.foreign_revision_id)
            )
            self.assertIsNone(repo.get_revision(self.fixture.document_id, "missing"))


class TestScopedWrites(RepositoryTestCase):
    def test_insert_round_trip(self):
        new_id = "newdoc"
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            repo.insert_object(new_id, "document", FIXTURE_TIME)
            repo.insert_revision(
                RevisionRecord(
                    id="newrev",
                    workspace_id=self.fixture.workspace_id,
                    object_id=new_id,
                    parent_revision_id=None,
                    content="新正文",
                    created_at=FIXTURE_TIME,
                )
            )
            repo.insert_entry(
                EntryRecord(
                    workspace_id=self.fixture.workspace_id,
                    branch_id=self.fixture.branch_id,
                    object_id=new_id,
                    kind="document",
                    parent_id=self.fixture.folder_id,
                    name="新文档",
                    position=9,
                    version=1,
                    current_revision_id="newrev",
                    metadata_json='{"tag": "x"}',
                    created_at=FIXTURE_TIME,
                    modified_at=FIXTURE_TIME,
                    deleted_at=None,
                )
            )
        with self.database.transaction() as connection:
            repo = self.repo(connection)
            entry = repo.get_entry(new_id)
            self.assertIsNotNone(entry)
            self.assertEqual(entry.name, "新文档")
            self.assertEqual(entry.kind, "document")
            self.assertEqual(entry.metadata_json, '{"tag": "x"}')
            revision = repo.get_revision(new_id, "newrev")
            self.assertEqual(revision.content, "新正文")

    def test_conditional_update_rowcount(self):
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            self.assertEqual(
                repo.update_entry(
                    self.fixture.document_id,
                    {"name": "改名", "version": 2},
                    expected_version=self.fixture.document_version,
                ),
                1,
            )
            self.assertEqual(
                repo.update_entry(
                    self.fixture.document_id,
                    {"name": "过期改名"},
                    expected_version=self.fixture.document_version,
                ),
                0,
            )
        with self.database.transaction() as connection:
            self.assertEqual(self.repo(connection).get_entry(self.fixture.document_id).name, "改名")

    def test_update_entry_revision_condition(self):
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            self.assertEqual(
                repo.update_entry(
                    self.fixture.document_id,
                    {"current_revision_id": self.fixture.other_revision_id, "version": 2},
                    expected_revision_id=self.fixture.revision_id,
                ),
                1,
            )
            self.assertEqual(
                repo.update_entry(
                    self.fixture.document_id,
                    {"current_revision_id": self.fixture.revision_id},
                    expected_revision_id=self.fixture.revision_id,
                ),
                0,
            )

    def test_update_entry_scoped_to_workspace_and_branch(self):
        with self.database.transaction(write=True) as connection:
            self.repo(connection).update_entry(
                self.fixture.document_id,
                {"name": "b1-renamed", "version": 2},
                expected_version=self.fixture.document_version,
            )
        with self.database.transaction() as connection:
            other = self.other_branch_repo(connection).get_entry(
                self.fixture.document_id
            )
            self.assertEqual(other.name, self.fixture.other_branch_name)
            self.assertEqual(other.version, self.fixture.other_branch_version)

    def test_update_entry_deleted_row_returns_zero(self):
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            self.assertEqual(
                repo.update_entry(self.fixture.deleted_document_id, {"name": "x"}), 0
            )

    def test_update_entry_without_expected_values_updates_active_row(self):
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            self.assertEqual(
                repo.update_entry(
                    self.fixture.document_id, {"metadata_json": '{"a": 1}'}
                ),
                1,
            )
            self.assertEqual(
                repo.get_entry(self.fixture.document_id).metadata_json, '{"a": 1}'
            )

    def test_update_entry_rejects_unknown_or_empty_changes(self):
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            with self.assertRaises(ValueError):
                repo.update_entry(self.fixture.document_id, {"workspace_id": "w2"})
            with self.assertRaises(ValueError):
                repo.update_entry(self.fixture.document_id, {"kind": "folder"})
            with self.assertRaises(ValueError):
                repo.update_entry(self.fixture.document_id, {})

    def test_touch_entries_increments_distinct_active_rows(self):
        target_ids = {
            self.fixture.document_id,
            self.fixture.sibling_a_id,
            self.fixture.document_id,
            self.fixture.deleted_document_id,
        }
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            before = {
                object_id: repo.get_entry(object_id).version
                for object_id in (
                    self.fixture.document_id,
                    self.fixture.sibling_a_id,
                    self.fixture.deleted_document_id,
                )
            }
            self.assertEqual(repo.touch_entries(target_ids, T2), 2)
            self.assertEqual(
                repo.get_entry(self.fixture.document_id).version, before[self.fixture.document_id] + 1
            )
            self.assertEqual(
                repo.get_entry(self.fixture.sibling_a_id).version,
                before[self.fixture.sibling_a_id] + 1,
            )
            self.assertEqual(
                repo.get_entry(self.fixture.deleted_document_id).version,
                before[self.fixture.deleted_document_id],
            )
            self.assertEqual(repo.get_entry(self.fixture.document_id).modified_at, T2)
            self.assertEqual(repo.touch_entries(set(), T2), 0)

    def test_cross_branch_parent_reference_is_rejected(self):
        with self.assertRaises(ConstraintError) as caught:
            with self.database.transaction(write=True) as connection:
                repo = self.repo(connection)
                repo.insert_object("crosschild", "folder", FIXTURE_TIME)
                repo.insert_entry(
                    EntryRecord(
                        workspace_id=self.fixture.workspace_id,
                        branch_id=self.fixture.branch_id,
                        object_id="crosschild",
                        kind="folder",
                        parent_id=self.fixture.other_branch_only_id,
                        name="cross-branch",
                        position=9,
                        version=1,
                        current_revision_id=None,
                        metadata_json="{}",
                        created_at=FIXTURE_TIME,
                        modified_at=FIXTURE_TIME,
                        deleted_at=None,
                    )
                )
        self.assertEqual(caught.exception.constraint, "foreign_key")

    def test_cross_workspace_parent_reference_is_rejected(self):
        with self.assertRaises(ConstraintError) as caught:
            with self.database.transaction(write=True) as connection:
                repo = self.repo(connection)
                repo.insert_object("crosswork", "folder", FIXTURE_TIME)
                repo.insert_entry(
                    EntryRecord(
                        workspace_id=self.fixture.workspace_id,
                        branch_id=self.fixture.branch_id,
                        object_id="crosswork",
                        kind="folder",
                        parent_id=self.fixture.foreign_root_id,
                        name="cross-workspace",
                        position=9,
                        version=1,
                        current_revision_id=None,
                        metadata_json="{}",
                        created_at=FIXTURE_TIME,
                        modified_at=FIXTURE_TIME,
                        deleted_at=None,
                    )
                )
        self.assertEqual(caught.exception.constraint, "foreign_key")


class TestRecordScopeGuard(RepositoryTestCase):
    """A repository may only insert records that match its own scope.

    Each case builds a fully valid foreign-scope object and revision first, so
    the referenced rows all exist and the foreign foreign-key checks would pass.
    Only the explicit pre-SQL scope guard may reject the write, which proves the
    rejection is not an accidental constraint failure.
    """

    def test_insert_entry_rejects_workspace_mismatch_without_writing(self):
        fx = self.fixture
        with self.database.transaction(write=True) as connection:
            foreign = Repository(
                connection,
                workspace_id=fx.other_workspace_id,
                branch_id=fx.foreign_branch_id,
            )
            foreign.insert_object("w2new", "document", FIXTURE_TIME)
            foreign.insert_revision(
                RevisionRecord(
                    id="w2newrev",
                    workspace_id=fx.other_workspace_id,
                    object_id="w2new",
                    parent_revision_id=None,
                    content="foreign-body",
                    created_at=FIXTURE_TIME,
                )
            )
            record = EntryRecord(
                workspace_id=fx.other_workspace_id,
                branch_id=fx.foreign_branch_id,
                object_id="w2new",
                kind="document",
                parent_id=fx.foreign_root_id,
                name="foreign-new",
                position=1,
                version=1,
                current_revision_id="w2newrev",
                metadata_json="{}",
                created_at=FIXTURE_TIME,
                modified_at=FIXTURE_TIME,
                deleted_at=None,
            )
            with self.assertRaises(ValueError):
                self.repo(connection).insert_entry(record)
            self.assertIsNone(foreign.get_entry("w2new"))

    def test_insert_entry_rejects_branch_mismatch_without_writing(self):
        fx = self.fixture
        with self.database.transaction(write=True) as connection:
            repo = self.repo(connection)
            repo.insert_object("w1new", "document", FIXTURE_TIME)
            repo.insert_revision(
                RevisionRecord(
                    id="w1newrev",
                    workspace_id=fx.workspace_id,
                    object_id="w1new",
                    parent_revision_id=None,
                    content="body",
                    created_at=FIXTURE_TIME,
                )
            )
            record = EntryRecord(
                workspace_id=fx.workspace_id,
                branch_id=fx.other_branch_id,
                object_id="w1new",
                kind="document",
                parent_id=fx.root_id,
                name="b1b-new",
                position=5,
                version=1,
                current_revision_id="w1newrev",
                metadata_json="{}",
                created_at=FIXTURE_TIME,
                modified_at=FIXTURE_TIME,
                deleted_at=None,
            )
            with self.assertRaises(ValueError):
                repo.insert_entry(record)
            self.assertIsNone(
                self.other_branch_repo(connection).get_entry("w1new")
            )

    def test_insert_revision_rejects_workspace_mismatch_without_writing(self):
        fx = self.fixture
        with self.database.transaction(write=True) as connection:
            foreign = Repository(
                connection,
                workspace_id=fx.other_workspace_id,
                branch_id=fx.foreign_branch_id,
            )
            foreign.insert_object("w2revobj", "document", FIXTURE_TIME)
            record = RevisionRecord(
                id="w2rev",
                workspace_id=fx.other_workspace_id,
                object_id="w2revobj",
                parent_revision_id=None,
                content="foreign-body",
                created_at=FIXTURE_TIME,
            )
            with self.assertRaises(ValueError):
                self.repo(connection).insert_revision(record)
            count = connection.execute(
                "SELECT COUNT(*) FROM document_revisions WHERE id = 'w2rev'"
            ).fetchone()[0]
            self.assertEqual(count, 0)


class TestDaoBoundary(RepositoryTestCase):
    def test_dao_does_not_commit(self):
        with self.assertRaises(RuntimeError):
            with self.database.transaction(write=True) as connection:
                repo = self.repo(connection)
                repo.insert_object("rollback-doc", "document", FIXTURE_TIME)
                repo.insert_revision(
                    RevisionRecord(
                        id="rollback-rev",
                        workspace_id=self.fixture.workspace_id,
                        object_id="rollback-doc",
                        parent_revision_id=None,
                        content="temporary",
                        created_at=FIXTURE_TIME,
                    )
                )
                repo.insert_entry(
                    EntryRecord(
                        workspace_id=self.fixture.workspace_id,
                        branch_id=self.fixture.branch_id,
                        object_id="rollback-doc",
                        kind="document",
                        parent_id=self.fixture.folder_id,
                        name="temporary",
                        position=8,
                        version=1,
                        current_revision_id="rollback-rev",
                        metadata_json="{}",
                        created_at=FIXTURE_TIME,
                        modified_at=FIXTURE_TIME,
                        deleted_at=None,
                    )
                )
                raise RuntimeError("outer operation failed")
        with self.database.transaction() as connection:
            objects = connection.execute(
                "SELECT COUNT(*) FROM objects WHERE id = 'rollback-doc'"
            ).fetchone()[0]
            entries = connection.execute(
                "SELECT COUNT(*) FROM entries WHERE object_id = 'rollback-doc'"
            ).fetchone()[0]
            revisions = connection.execute(
                "SELECT COUNT(*) FROM document_revisions WHERE id = 'rollback-rev'"
            ).fetchone()[0]
        self.assertEqual((objects, entries, revisions), (0, 0, 0))

    def test_repository_has_no_transaction_or_connection_control(self):
        source = inspect.getsource(repository_module)
        for token in (
            "BEGIN",
            "COMMIT",
            "ROLLBACK",
            ".connect(",
            ".commit(",
            ".rollback(",
            ".close(",
            "executescript",
        ):
            self.assertNotIn(token, source)
        self.assertNotIn("CREATE TABLE", source)


if __name__ == "__main__":
    unittest.main()
