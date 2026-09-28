"""Folder abstract-file implementation backed by a SQLite container."""

from __future__ import annotations

import uuid
from pathlib import Path

from .sql import AbstractFileRecord, SqlFile


class Folder:
    def __init__(self, container: str | Path):
        self.sql = SqlFile(container)
        self._record = self.sql.record()
        if self._record.kind != "folder":
            raise ValueError(f"容器不是 folder: {container}")

    @classmethod
    def create(
        cls,
        container: str | Path,
        fullpath: str,
        parent_id: str | None,
        file_id: str | None = None,
    ) -> "Folder":
        record = AbstractFileRecord(
            id=file_id or str(uuid.uuid4()),
            fullpath=fullpath,
            kind="folder",
            parent_id=parent_id,
        )
        sql = SqlFile(container)
        sql.create(record)
        return cls(container)

    @property
    def id(self) -> str:
        return self._record.id

    @property
    def fullpath(self) -> str:
        return self._record.fullpath

    @property
    def parent_id(self) -> str | None:
        return self._record.parent_id

    @property
    def container_path(self) -> Path:
        return self.sql.path

    def add_child(self, child_id: str) -> None:
        with self.sql.transaction() as connection:
            exists = connection.execute(
                'SELECT 1 FROM "file" WHERE parent_id = ? AND child_id = ?',
                (self.id, child_id),
            ).fetchone()
            if exists:
                raise ValueError(f"子文件已存在: {child_id}")
            position = connection.execute(
                'SELECT COALESCE(MAX(position), -1) + 1 FROM "file" WHERE parent_id = ?',
                (self.id,),
            ).fetchone()[0]
            connection.execute(
                'INSERT INTO "file"(parent_id, child_id, position) VALUES (?, ?, ?)',
                (self.id, child_id, position),
            )

    def remove_child(self, child_id: str) -> None:
        with self.sql.transaction() as connection:
            cursor = connection.execute(
                'DELETE FROM "file" WHERE parent_id = ? AND child_id = ?',
                (self.id, child_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"找不到子文件: {child_id}")

    def children(self) -> list[str]:
        with self.sql.transaction() as connection:
            rows = connection.execute(
                'SELECT child_id FROM "file" WHERE parent_id = ? ORDER BY position',
                (self.id,),
            ).fetchall()
            return [row["child_id"] for row in rows]

    def set_children(self, child_ids: list[str]) -> None:
        """Replace the ordered child list while keeping this folder's ID stable."""
        if len(set(child_ids)) != len(child_ids):
            raise ValueError("子文件 ID 不能重复")
        with self.sql.transaction() as connection:
            connection.execute('DELETE FROM "file" WHERE parent_id = ?', (self.id,))
            connection.executemany(
                'INSERT INTO "file"(parent_id, child_id, position) VALUES (?, ?, ?)',
                [(self.id, child_id, position) for position, child_id in enumerate(child_ids)],
            )
