"""Shared test helpers.

Only test infrastructure lives here: temporary directories, schema-only
database fixtures, sample-tree copies and small counting utilities.  Helpers
never touch the tracked legacy sample under ``data/``.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.core.models import ContentScope
from src.storage import Database, create_schema
from src.storage.management import initialize_database
from src.storage.records import EntryRecord, RevisionRecord
from src.storage.repository import (
    Repository,
    insert_branch,
    insert_workspace,
)

__all__ = [
    "PROJECT_ROOT",
    "SAMPLE_DATA",
    "SAMPLE_DOCUMENT_ID",
    "FIXTURE_TIME",
    "LEGACY_SAMPLE_DOCUMENT_CONTENT",
    "LEGACY_SAMPLE_DOCUMENT_SHA256",
    "LEGACY_SAMPLE_ROOT_ID",
    "ContentReadFixture",
    "RepositoryFixture",
    "TempPathTestCase",
    "copy_sample_data",
    "create_legacy_document",
    "create_legacy_folder",
    "create_schema_database",
    "entry_state",
    "legacy_connection",
    "legacy_object_id",
    "revision_state",
    "seed_content_read_fixture",
    "seed_repository_fixture",
    "table_counts",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = PROJECT_ROOT / "data"
SAMPLE_DOCUMENT_ID = "8b168849-1ab3-4542-9828-f2e2120f2d57"
LEGACY_SAMPLE_ROOT_ID = "783c8c94-fc40-4312-9e20-e90a7c57719f"
LEGACY_SAMPLE_DOCUMENT_CONTENT = (
    "Concrete cream is a classical staple of Kujikuji people.\n"
    "Concrete cream is delicious!"
)
LEGACY_SAMPLE_DOCUMENT_SHA256 = (
    "0dd92b5440aa9d35c1da22ca232e346d57fd8fb800c712a6f05358969c93d268"
)


class TempPathTestCase(unittest.TestCase):
    """Base test case giving each test an isolated, auto-cleaned temp dir."""

    def setUp(self) -> None:
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.temp_root = Path(self._temp_dir.name)

    def temp_path(self, name: str = "c156.sqlite") -> Path:
        return self.temp_root / name


def create_schema_database(path: Path) -> Path:
    """Create an empty schema-v1 database configured for WAL.

    No default tree is inserted: this is the storage-only fixture used by the
    database and repository tests.  ``initialize_database`` owns the tree.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    database = Database(path)
    with database.management_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            create_schema(connection)
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    database.configure_runtime()
    return path


def copy_sample_data(destination: Path) -> Path:
    """Copy the tracked legacy sample tree to *destination* for read-only use."""

    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SAMPLE_DATA, destination)
    return destination


@contextmanager
def legacy_connection(path: Path, *, write: bool = False):
    """Open a legacy SQLite container read-only or for test fixture mutation.

    Read-only connections use a ``mode=ro`` URI; write connections commit on a
    clean exit and are reserved for building or corrupting *copies* of the
    tracked sample.  This helper never calls the legacy ``src.file`` wrappers,
    whose constructors create tables as a side effect.
    """

    path = Path(path)
    if write:
        connection = sqlite3.connect(path)
    else:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        if write:
            connection.commit()
    finally:
        connection.close()


def legacy_object_id(container: Path) -> str:
    """Return the ``abstract_file.id`` stored in *container* (read-only)."""

    with legacy_connection(container) as connection:
        row = connection.execute("SELECT id FROM abstract_file").fetchone()
    if row is None:
        raise AssertionError(f"container has no abstract_file row: {container}")
    return row["id"]


def create_legacy_folder(
    container: Path,
    *,
    file_id: str,
    fullpath: str,
    parent_id: str | None,
    children: tuple[str, ...] = (),
    metadata: dict[str, str] | None = None,
) -> Path:
    """Create a synthetic legacy ``.folder`` container with raw SQL."""

    _create_legacy_container(
        container,
        file_id=file_id,
        fullpath=fullpath,
        kind="folder",
        parent_id=parent_id,
        children=children,
        content=None,
        metadata=metadata,
    )
    return Path(container)


def create_legacy_document(
    container: Path,
    *,
    file_id: str,
    fullpath: str,
    parent_id: str,
    content: str = "",
    metadata: dict[str, str] | None = None,
) -> Path:
    """Create a synthetic legacy document container with raw SQL."""

    _create_legacy_container(
        container,
        file_id=file_id,
        fullpath=fullpath,
        kind="document",
        parent_id=parent_id,
        children=(),
        content=content,
        metadata=metadata,
    )
    return Path(container)


def _create_legacy_container(
    container: Path,
    *,
    file_id: str,
    fullpath: str,
    kind: str,
    parent_id: str | None,
    children: tuple[str, ...],
    content: str | None,
    metadata: dict[str, str] | None,
) -> None:
    container = Path(container)
    container.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(container)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(
            """
            CREATE TABLE abstract_file (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                id TEXT NOT NULL UNIQUE,
                fullpath TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK (
                    kind IN ('folder', 'document', 'executable', 'resource')
                ),
                parent_id TEXT
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE "file" (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (parent_id, child_id),
                UNIQUE (parent_id, position)
            );
            """
        )
        connection.execute(
            "INSERT INTO abstract_file(singleton, id, fullpath, kind, parent_id) "
            "VALUES (1, ?, ?, ?, ?)",
            (file_id, fullpath, kind, parent_id),
        )
        connection.executemany(
            'INSERT INTO "file"(parent_id, child_id, position) VALUES (?, ?, ?)',
            [(file_id, child, position) for position, child in enumerate(children)],
        )
        if content is not None:
            connection.execute(
                "CREATE TABLE document_content ("
                "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                "content TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO document_content(singleton, content) VALUES (1, ?)",
                (content,),
            )
        if metadata:
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                tuple(metadata.items()),
            )
        connection.commit()
    finally:
        connection.close()


def table_counts(path: Path) -> dict:
    """Return a row count for each content table (test observation helper)."""

    tables = (
        "workspaces",
        "objects",
        "branches",
        "entries",
        "document_revisions",
        "legacy_imports",
    )
    with Database(path).transaction() as connection:
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }


def entry_state(path: Path, object_id: str) -> dict | None:
    """Return the stored ``entries`` row for *object_id* (or ``None``)."""

    with Database(path).transaction() as connection:
        row = connection.execute(
            "SELECT workspace_id, branch_id, object_id, parent_id, name, position, "
            "version, current_revision_id, metadata_json, created_at, modified_at, "
            "deleted_at FROM entries WHERE object_id = ?",
            (object_id,),
        ).fetchone()
    return None if row is None else dict(row)


def revision_state(path: Path, object_id: str) -> list:
    """Return stored revisions for *object_id* in creation order."""

    with Database(path).transaction() as connection:
        rows = connection.execute(
            "SELECT id, workspace_id, object_id, parent_revision_id, content, "
            "created_at FROM document_revisions WHERE object_id = ? "
            "ORDER BY created_at, id",
            (object_id,),
        ).fetchall()
    return [dict(row) for row in rows]


#: Timestamp shared by every fixture row (UTC ISO 8601).
FIXTURE_TIME = "2026-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class RepositoryFixture:
    """Identifiers of the two-workspace / two-branch repository fixture.

    The same ``document_id`` object has independent entries in ``branch_id`` and
    ``other_branch_id`` so scoped reads and conditional writes can be checked
    for leaking across branches.  ``other_branch_only_id`` exists only in the
    other branch and is used to prove cross-branch parent references are
    rejected by the composite foreign key.
    """

    path: Path
    workspace_id: str
    other_workspace_id: str
    branch_id: str
    other_branch_id: str
    foreign_branch_id: str
    root_id: str
    foreign_root_id: str
    folder_id: str
    document_id: str
    sibling_a_id: str
    sibling_b_id: str
    deleted_document_id: str
    other_branch_only_id: str
    foreign_document_id: str
    document_name: str
    other_branch_name: str
    document_version: int
    other_branch_version: int
    revision_id: str
    other_revision_id: str
    sibling_a_revision_id: str
    sibling_b_revision_id: str
    deleted_revision_id: str
    foreign_revision_id: str


def seed_repository_fixture(path: Path) -> RepositoryFixture:
    """Create a schema database and seed the scoped repository fixture.

    Seeding uses raw SQL so the repository code under test is never needed to
    build its own inputs.  The returned identifiers are the only contract the
    tests rely on.
    """

    path = create_schema_database(Path(path))
    database = Database(path)
    with database.transaction(write=True) as connection:
        _seed_repository_rows(connection)
    return RepositoryFixture(
        path=path,
        workspace_id="w1",
        other_workspace_id="w2",
        branch_id="b1",
        other_branch_id="b1b",
        foreign_branch_id="b2",
        root_id="root1",
        foreign_root_id="root2",
        folder_id="folder1",
        document_id="doc1",
        sibling_a_id="doca",
        sibling_b_id="docb",
        deleted_document_id="docdel",
        other_branch_only_id="onlyother",
        foreign_document_id="doc2",
        document_name="draft",
        other_branch_name="dev-draft",
        document_version=1,
        other_branch_version=5,
        revision_id="rev1a",
        other_revision_id="rev1b",
        sibling_a_revision_id="reva",
        sibling_b_revision_id="revb",
        deleted_revision_id="revdel",
        foreign_revision_id="rev2",
    )


def _seed_repository_rows(connection) -> None:
    now = FIXTURE_TIME
    connection.executemany(
        "INSERT INTO workspaces (id, name, created_at) VALUES (?,?,?)",
        (("w1", "W1", now), ("w2", "W2", now)),
    )
    connection.executemany(
        "INSERT INTO objects (id, workspace_id, kind, created_at) VALUES (?,?,?,?)",
        (
            ("root1", "w1", "folder", now),
            ("folder1", "w1", "folder", now),
            ("doc1", "w1", "document", now),
            ("doca", "w1", "document", now),
            ("docb", "w1", "document", now),
            ("docdel", "w1", "document", now),
            ("onlyother", "w1", "folder", now),
            ("root2", "w2", "folder", now),
            ("doc2", "w2", "document", now),
        ),
    )
    connection.executemany(
        "INSERT INTO document_revisions "
        "(id, workspace_id, object_id, parent_revision_id, content, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            ("rev1a", "w1", "doc1", None, "alpha", now),
            ("rev1b", "w1", "doc1", "rev1a", "beta", now),
            ("reva", "w1", "doca", None, "a-body", now),
            ("revb", "w1", "docb", None, "b-body", now),
            ("revdel", "w1", "docdel", None, "deleted-body", now),
            ("rev2", "w2", "doc2", None, "foreign-body", now),
        ),
    )
    connection.executemany(
        "INSERT INTO branches (id, workspace_id, name, root_object_id, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            ("b1", "w1", "main", "root1", now),
            ("b1b", "w1", "dev", "root1", now),
            ("b2", "w2", "main", "root2", now),
        ),
    )
    connection.executemany(
        "INSERT INTO entries (workspace_id, branch_id, object_id, parent_id, name, "
        "position, version, current_revision_id, metadata_json, created_at, "
        "modified_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ("w1", "b1", "root1", None, "", 0, 1, None, "{}", now, now, None),
            ("w1", "b1", "folder1", "root1", "folder", 0, 1, None, "{}", now, now, None),
            ("w1", "b1", "doc1", "folder1", "draft", 0, 1, "rev1a", "{}", now, now, None),
            ("w1", "b1", "doca", "folder1", "alpha", 1, 1, "reva", "{}", now, now, None),
            ("w1", "b1", "docb", "folder1", "zulu", 2, 1, "revb", "{}", now, now, None),
            (
                "w1", "b1", "docdel", "folder1", "gone", 1, 2, "revdel", "{}",
                now, now, now,
            ),
            ("w1", "b1b", "root1", None, "", 0, 1, None, "{}", now, now, None),
            (
                "w1", "b1b", "doc1", "root1", "dev-draft", 0, 5, "rev1b", "{}",
                now, now, None,
            ),
            (
                "w1", "b1b", "onlyother", "root1", "only-other", 1, 1, None, "{}",
                now, now, None,
            ),
            ("w2", "b2", "root2", None, "", 0, 1, None, "{}", now, now, None),
            ("w2", "b2", "doc2", "root2", "foreign", 0, 1, "rev2", "{}", now, now, None),
        ),
    )


@dataclass(frozen=True)
class ContentReadFixture:
    """Identifiers of the initialized read fixture built by :func:`seed_content_read_fixture`.

    The fixture starts from a real ``initialize_database`` workspace root and
    then seeds one ``main`` subtree plus a second branch in the same workspace
    and an independent foreign workspace.  It never touches ``data/``.
    """

    path: Path
    database: Database
    root_scope: ContentScope
    main_scope: ContentScope
    workspace_id: str
    branch_id: str
    root_id: str
    main_id: str
    admin_id: str
    resource_id: str
    bin_id: str
    products_id: str
    concretecream_id: str
    concretecream_revision_id: str
    concretecream_content: str
    draft_id: str
    draft_revision_id: str
    draft_content: str
    chinese_folder_id: str
    notes_id: str
    notes_revision_id: str
    notes_content: str
    deleted_folder_id: str
    dev_branch_id: str
    dev_document_id: str
    dev_revision_id: str
    foreign_workspace_id: str
    foreign_branch_id: str
    foreign_root_id: str
    foreign_document_id: str
    foreign_revision_id: str


#: Stable node ids used by the initialized read fixture (UUID-free on purpose).
_PRODUCTS_ID = "products"
_CONCRETECREAM_ID = "concretecream"
_DRAFT_ID = "draft"
_CHINESE_FOLDER_ID = "chinese-folder"
_NOTES_ID = "notes"
_DELETED_FOLDER_ID = "deleted-folder"
_DEV_BRANCH_ID = "dev-branch"
_DEV_DOCUMENT_ID = "dev-document"
_FOREIGN_WORKSPACE_ID = "foreign-workspace"
_FOREIGN_BRANCH_ID = "foreign-branch"
_FOREIGN_ROOT_ID = "foreign-root"
_FOREIGN_DOCUMENT_ID = "foreign-document"


def seed_content_read_fixture(path: Path) -> ContentReadFixture:
    """Initialize a database and seed the default-tree read fixture."""

    path = Path(path)
    root_scope = initialize_database(path)
    workspace_id = root_scope.workspace_id
    branch_id = root_scope.branch_id
    root_id = root_scope.root_id
    now = FIXTURE_TIME

    with Database(path).transaction(write=True) as connection:
        repo = Repository(connection, workspace_id=workspace_id, branch_id=branch_id)
        main = repo.find_child(root_id, "main")
        admin = repo.find_child(root_id, "admin")
        resource = repo.find_child(root_id, "resource")
        bin_entry = repo.find_child(root_id, "bin")
        if main is None or admin is None or resource is None or bin_entry is None:
            raise AssertionError("initialized default tree is incomplete")
        main_id = main.object_id

        _insert_folder(
            repo, workspace_id, branch_id, _PRODUCTS_ID, main_id, "products", 0, now,
            metadata_json='{"tag": "x", "nested": {"a": 1}}',
        )
        _insert_document(
            repo, workspace_id, branch_id, _CONCRETECREAM_ID, _PRODUCTS_ID,
            "concretecream", 0, "正文内容", now,
        )
        _insert_document(
            repo, workspace_id, branch_id, _DRAFT_ID, _PRODUCTS_ID,
            "草稿 二", 1, "中文 空格", now,
        )
        _insert_folder(
            repo, workspace_id, branch_id, _CHINESE_FOLDER_ID, main_id, "中文 空格", 1, now,
        )
        _insert_document(
            repo, workspace_id, branch_id, _NOTES_ID, _CHINESE_FOLDER_ID,
            "笔记", 0, "笔记正文", now,
        )
        _insert_folder(
            repo, workspace_id, branch_id, _DELETED_FOLDER_ID, main_id, "旧目录", 2, now,
        )
        repo.update_entry(
            _DELETED_FOLDER_ID, {"deleted_at": now, "version": 2}
        )

        insert_branch(connection, workspace_id, _DEV_BRANCH_ID, "dev", root_id, now)
        dev_repo = Repository(
            connection, workspace_id=workspace_id, branch_id=_DEV_BRANCH_ID
        )
        dev_repo.insert_entry(
            _entry(
                workspace_id, _DEV_BRANCH_ID, root_id, "folder", None, "", 0, 1,
                None, now,
            )
        )
        _insert_document(
            dev_repo, workspace_id, _DEV_BRANCH_ID, _DEV_DOCUMENT_ID, root_id,
            "dev-note", 0, "dev-body", now,
        )

        insert_workspace(connection, _FOREIGN_WORKSPACE_ID, "Foreign", now)
        foreign_repo = Repository(
            connection, workspace_id=_FOREIGN_WORKSPACE_ID, branch_id=_FOREIGN_BRANCH_ID
        )
        foreign_repo.insert_object(_FOREIGN_ROOT_ID, "folder", now)
        insert_branch(
            connection,
            _FOREIGN_WORKSPACE_ID,
            _FOREIGN_BRANCH_ID,
            "main",
            _FOREIGN_ROOT_ID,
            now,
        )
        foreign_repo.insert_entry(
            _entry(
                _FOREIGN_WORKSPACE_ID, _FOREIGN_BRANCH_ID, _FOREIGN_ROOT_ID, "folder",
                None, "", 0, 1, None, now,
            )
        )
        _insert_document(
            foreign_repo, _FOREIGN_WORKSPACE_ID, _FOREIGN_BRANCH_ID,
            _FOREIGN_DOCUMENT_ID, _FOREIGN_ROOT_ID, "foreign", 0, "foreign-body", now,
        )

    return ContentReadFixture(
        path=path,
        database=Database(path),
        root_scope=root_scope,
        main_scope=ContentScope(workspace_id, branch_id, main_id),
        workspace_id=workspace_id,
        branch_id=branch_id,
        root_id=root_id,
        main_id=main_id,
        admin_id=admin.object_id,
        resource_id=resource.object_id,
        bin_id=bin_entry.object_id,
        products_id=_PRODUCTS_ID,
        concretecream_id=_CONCRETECREAM_ID,
        concretecream_revision_id="rev-concretecream",
        concretecream_content="正文内容",
        draft_id=_DRAFT_ID,
        draft_revision_id="rev-draft",
        draft_content="中文 空格",
        chinese_folder_id=_CHINESE_FOLDER_ID,
        notes_id=_NOTES_ID,
        notes_revision_id="rev-notes",
        notes_content="笔记正文",
        deleted_folder_id=_DELETED_FOLDER_ID,
        dev_branch_id=_DEV_BRANCH_ID,
        dev_document_id=_DEV_DOCUMENT_ID,
        dev_revision_id="rev-dev-document",
        foreign_workspace_id=_FOREIGN_WORKSPACE_ID,
        foreign_branch_id=_FOREIGN_BRANCH_ID,
        foreign_root_id=_FOREIGN_ROOT_ID,
        foreign_document_id=_FOREIGN_DOCUMENT_ID,
        foreign_revision_id="rev-foreign-document",
    )


def _entry(
    workspace_id,
    branch_id,
    object_id,
    kind,
    parent_id,
    name,
    position,
    version,
    revision_id,
    now,
    metadata_json="{}",
):
    return EntryRecord(
        workspace_id=workspace_id,
        branch_id=branch_id,
        object_id=object_id,
        kind=kind,
        parent_id=parent_id,
        name=name,
        position=position,
        version=version,
        current_revision_id=revision_id,
        metadata_json=metadata_json,
        created_at=now,
        modified_at=now,
        deleted_at=None,
    )


def _insert_folder(repo, workspace_id, branch_id, object_id, parent_id, name, position, now, *, metadata_json="{}"):
    repo.insert_object(object_id, "folder", now)
    repo.insert_entry(
        _entry(
            workspace_id, branch_id, object_id, "folder", parent_id, name, position,
            1, None, now, metadata_json,
        )
    )


def _insert_document(repo, workspace_id, branch_id, object_id, parent_id, name, position, content, now):
    revision_id = "rev-" + object_id
    repo.insert_object(object_id, "document", now)
    repo.insert_revision(
        RevisionRecord(
            id=revision_id,
            workspace_id=workspace_id,
            object_id=object_id,
            parent_revision_id=None,
            content=content,
            created_at=now,
        )
    )
    repo.insert_entry(
        _entry(
            workspace_id, branch_id, object_id, "document", parent_id, name, position,
            1, revision_id, now,
        )
    )
