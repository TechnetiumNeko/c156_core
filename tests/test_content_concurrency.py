"""Cross-process concurrency tests for ContentService writes.

Two independent OS processes with their own SQLite connections race through a
shared :class:`multiprocessing.Barrier`.  There is no in-process lock standing
in for database coordination.  Queue reads and process joins use bounded
timeouts so a dead worker fails the test instead of hanging the suite.
"""

from __future__ import annotations

import multiprocessing
import unittest
from pathlib import Path

from src.core import AlreadyExists, Conflict, ContentError
from src.services import ContentService
from src.storage import Database
from tests.helpers import (
    ContentReadFixture,
    TempPathTestCase,
    entry_state,
    seed_content_read_fixture,
    table_counts,
)

_BARRIER_TIMEOUT = 30.0
_QUEUE_TIMEOUT = 30.0
_JOIN_TIMEOUT = 30.0


def _create_worker(database_path, scope, parent_id, name, barrier, results):
    """Try to create one same-named document after the shared start barrier."""

    service = ContentService(Database(Path(database_path)))
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        node = service.create_document(scope, parent_id, name, content="")
    except AlreadyExists:
        results.put({"status": "already_exists"})
    except ContentError as exc:
        results.put({"status": "content_error", "detail": type(exc).__name__})
    except BaseException as exc:  # pragma: no cover - unexpected worker failure
        results.put({"status": "unexpected", "detail": repr(exc)})
    else:
        results.put({"status": "ok", "id": node.id})


def _save_worker(database_path, scope, object_id, content, barrier, results):
    """Read the current revision, synchronize, then save based on it."""

    service = ContentService(Database(Path(database_path)))
    try:
        base = service.read_document(scope, object_id)
    except BaseException as exc:  # pragma: no cover - unexpected worker failure
        results.put({"status": "read_error", "detail": repr(exc)})
        return
    try:
        barrier.wait(timeout=_BARRIER_TIMEOUT)
        saved = service.save_document(
            scope, object_id, content, expected_revision_id=base.revision_id
        )
    except Conflict:
        results.put({"status": "conflict", "base": base.revision_id})
    except ContentError as exc:
        results.put(
            {
                "status": "content_error",
                "detail": type(exc).__name__,
                "base": base.revision_id,
            }
        )
    except BaseException as exc:  # pragma: no cover - unexpected worker failure
        results.put(
            {"status": "unexpected", "detail": repr(exc), "base": base.revision_id}
        )
    else:
        results.put(
            {"status": "ok", "base": base.revision_id, "new": saved.revision_id}
        )


class ContentConcurrencyTestCase(TempPathTestCase):
    fixture: ContentReadFixture

    def setUp(self) -> None:
        super().setUp()
        self.fixture = seed_content_read_fixture(self.temp_path())
        self.scope = self.fixture.main_scope
        self.context = multiprocessing.get_context("spawn")

    def run_workers(self, target, arguments) -> list:
        """Run *target* in spawned processes and collect one result per worker."""

        barrier = self.context.Barrier(len(arguments))
        results = self.context.Queue()
        processes = [
            self.context.Process(
                target=target, args=(*argument, barrier, results)
            )
            for argument in arguments
        ]
        for process in processes:
            process.start()
        collected = []
        try:
            for _ in range(len(arguments)):
                collected.append(results.get(timeout=_QUEUE_TIMEOUT))
        finally:
            for process in processes:
                process.join(timeout=_JOIN_TIMEOUT)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        for process in processes:
            self.assertEqual(
                process.exitcode,
                0,
                "worker exited abnormally: {}".format(process.exitcode),
            )
        return collected


class TestConcurrentCreate(ContentConcurrencyTestCase):
    def test_same_name_create_has_exactly_one_winner(self):
        before = table_counts(self.fixture.path)
        before_parent = entry_state(self.fixture.path, self.fixture.products_id)
        results = self.run_workers(
            _create_worker,
            [
                (str(self.fixture.path), self.scope, self.fixture.products_id, "并发"),
                (str(self.fixture.path), self.scope, self.fixture.products_id, "并发"),
            ],
        )

        statuses = sorted(result["status"] for result in results)
        self.assertEqual(statuses, ["already_exists", "ok"])

        winner = next(result for result in results if result["status"] == "ok")
        after = table_counts(self.fixture.path)
        self.assertEqual(after["objects"], before["objects"] + 1)
        self.assertEqual(after["entries"], before["entries"] + 1)
        self.assertEqual(
            after["document_revisions"], before["document_revisions"] + 1
        )

        service = ContentService(self.fixture.database)
        matches = [
            child
            for child in service.list_children(self.scope, self.fixture.products_id)
            if child.name == "并发"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].id, winner["id"])
        self.assertEqual(
            entry_state(self.fixture.path, self.fixture.products_id)["version"],
            before_parent["version"] + 1,
        )


class TestConcurrentSave(ContentConcurrencyTestCase):
    def test_same_revision_save_has_one_winner(self):
        document_id = self.fixture.concretecream_id
        base_document = ContentService(self.fixture.database).read_document(
            self.scope, document_id
        )
        self.assertEqual(
            base_document.revision_id, self.fixture.concretecream_revision_id
        )
        before = table_counts(self.fixture.path)

        results = self.run_workers(
            _save_worker,
            [
                (str(self.fixture.path), self.scope, document_id, "甲"),
                (str(self.fixture.path), self.scope, document_id, "乙"),
            ],
        )

        statuses = sorted(result["status"] for result in results)
        self.assertEqual(statuses, ["conflict", "ok"])
        # Both callers really did read the same base revision before saving.
        self.assertEqual(
            {result["base"] for result in results},
            {base_document.revision_id},
        )

        winner = next(result for result in results if result["status"] == "ok")
        after = table_counts(self.fixture.path)
        self.assertEqual(
            after["document_revisions"], before["document_revisions"] + 1
        )

        final = ContentService(self.fixture.database).read_document(
            self.scope, document_id
        )
        self.assertEqual(final.revision_id, winner["new"])
        self.assertIn(final.content, ("甲", "乙"))


class TestConcurrentDeleteSnapshot(ContentConcurrencyTestCase):
    """An independent process must be able to invalidate a delete token."""

    def test_deep_save_from_another_process_invalidates_delete_token(self):
        snapshot = ContentService(self.fixture.database).prepare_delete(
            self.scope, self.fixture.products_id
        )

        results = self.run_workers(
            _save_worker,
            [
                (
                    str(self.fixture.path),
                    self.scope,
                    self.fixture.concretecream_id,
                    "并发修改",
                )
            ],
        )
        self.assertEqual([result["status"] for result in results], ["ok"])

        before = table_counts(self.fixture.path)
        with self.assertRaises(Conflict):
            ContentService(self.fixture.database).delete_node(
                self.scope,
                self.fixture.products_id,
                expected_version=snapshot.version,
                recursive=True,
                expected_subtree_token=snapshot.subtree_token,
            )

        # The concurrent content survives and no extra row was deleted.
        after = table_counts(self.fixture.path)
        self.assertEqual(after, before)
        self.assertIsNone(
            entry_state(self.fixture.path, self.fixture.products_id)["deleted_at"]
        )
        final = ContentService(self.fixture.database).read_document(
            self.scope, self.fixture.concretecream_id
        )
        self.assertEqual(final.content, "并发修改")


if __name__ == "__main__":
    unittest.main()


class TestSaveAfterMoveOrDelete(ContentConcurrencyTestCase):
    def test_lock_timeout_is_storage_busy(self):
        import sqlite3
        from src.core import StorageBusy
        self.assertEqual(Database(self.fixture.path).busy_timeout_ms, 5000)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        lock = sqlite3.connect(self.fixture.path, isolation_level=None)
        try:
            lock.execute("BEGIN IMMEDIATE")
            service = ContentService(Database(self.fixture.path, busy_timeout_ms=10))
            with self.assertRaises(StorageBusy):
                service.create_folder(self.scope, self.scope.root_id, "blocked")
        finally:
            lock.close()
        self.assertFalse(any(row.name == "blocked" for row in
                             ContentService(self.fixture.database).list_children(self.scope, self.scope.root_id)))

    def test_save_after_move_in_scope_keeps_base_revision(self):
        service = ContentService(self.fixture.database)
        other = ContentService(Database(self.fixture.path))
        doc = service.read_document(self.scope, self.fixture.concretecream_id)
        other.move_node(self.scope, doc.id, self.scope.root_id, expected_version=doc.version)
        saved = service.save_document(self.scope, doc.id, "moved buffer",
                                      expected_revision_id=doc.revision_id)
        self.assertEqual(saved.content, "moved buffer")

    def test_save_after_move_outside_scope_fails_without_new_revision(self):
        from src.core import PathOutsideRoot
        from tests.helpers import revision_state
        service = ContentService(self.fixture.database)
        doc = service.read_document(self.scope, self.fixture.concretecream_id)
        ContentService(Database(self.fixture.path)).move_node(
            self.fixture.root_scope, doc.id, self.fixture.admin_id, expected_version=doc.version)
        before = revision_state(self.fixture.path, doc.id)
        with self.assertRaises(PathOutsideRoot):
            service.save_document(self.scope, doc.id, "old buffer", expected_revision_id=doc.revision_id)
        self.assertEqual(revision_state(self.fixture.path, doc.id), before)

    def test_save_after_delete_fails_without_new_revision(self):
        from src.core import NotFound
        from tests.helpers import revision_state
        service = ContentService(self.fixture.database)
        doc = service.read_document(self.scope, self.fixture.concretecream_id)
        ContentService(Database(self.fixture.path)).delete_node(self.scope, doc.id, expected_version=doc.version)
        before = revision_state(self.fixture.path, doc.id)
        with self.assertRaises(NotFound):
            service.save_document(self.scope, doc.id, "old buffer", expected_revision_id=doc.revision_id)
        self.assertEqual(revision_state(self.fixture.path, doc.id), before)
