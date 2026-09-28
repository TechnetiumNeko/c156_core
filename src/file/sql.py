"""Small SQLite container wrapper shared by virtual file object types."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


FILE_KINDS = ("folder", "document", "executable", "resource")


@dataclass(frozen=True)
class AbstractFileRecord:
    id: str
    fullpath: str
    kind: str
    parent_id: str | None

    def __post_init__(self) -> None:
        if self.kind not in FILE_KINDS:
            raise ValueError(f"不支持的文件类型: {self.kind}")
        if not self.fullpath.startswith("/"):
            raise ValueError("fullpath 必须是绝对虚拟路径")


class SqlFile:
    """Access one SQLite single-file container for one AbstractFile."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            self._create_schema(connection)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS abstract_file (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                id TEXT NOT NULL UNIQUE,
                fullpath TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK (kind IN ('folder', 'document', 'executable', 'resource')),
                parent_id TEXT
            );

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS "file" (
                parent_id TEXT NOT NULL,
                child_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (parent_id, child_id),
                UNIQUE (parent_id, position)
            );
            """
        )

    def create(self, record: AbstractFileRecord) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO abstract_file(singleton, id, fullpath, kind, parent_id)
                   VALUES (1, ?, ?, ?, ?)""",
                (record.id, record.fullpath, record.kind, record.parent_id),
            )

    def record(self) -> AbstractFileRecord:
        if not self.path.is_file():
            raise FileNotFoundError(f"容器不存在: {self.path}")
        connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT id, fullpath, kind, parent_id FROM abstract_file WHERE singleton = 1"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"不是有效的抽象文件容器: {self.path}") from exc
        finally:
            connection.close()
        if row is None:
            raise FileNotFoundError(f"容器中没有抽象文件记录: {self.path}")
        return AbstractFileRecord(
            id=row["id"],
            fullpath=row["fullpath"],
            kind=row["kind"],
            parent_id=row["parent_id"],
        )

    def set_metadata(self, key: str, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, encoded),
            )

    def get_metadata(self, key: str, default: object = None) -> object:
        with self.transaction() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return default if row is None else json.loads(row["value"])
