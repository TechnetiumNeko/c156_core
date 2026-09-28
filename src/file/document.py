"""Markdown document abstract-file implementation."""

from __future__ import annotations

import uuid
from pathlib import Path

from .sql import AbstractFileRecord, SqlFile


class Document:
    def __init__(self, container: str | Path):
        self.sql = SqlFile(container)
        self._record = self.sql.record()
        if self._record.kind != "document":
            raise ValueError(f"容器不是 document: {container}")
        with self.sql.transaction() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS document_content (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    content TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO document_content(singleton, content) VALUES (1, '')"
            )

    @classmethod
    def create(
        cls,
        container: str | Path,
        fullpath: str,
        parent_id: str,
        content: str = "",
        file_id: str | None = None,
    ) -> "Document":
        record = AbstractFileRecord(
            id=file_id or str(uuid.uuid4()),
            fullpath=fullpath,
            kind="document",
            parent_id=parent_id,
        )
        sql = SqlFile(container)
        sql.create(record)
        document = cls(container)
        document.content = content
        return document

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

    @property
    def content(self) -> str:
        with self.sql.transaction() as connection:
            row = connection.execute(
                "SELECT content FROM document_content WHERE singleton = 1"
            ).fetchone()
            return "" if row is None else row["content"]

    @content.setter
    def content(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("文档 content 必须是字符串")
        with self.sql.transaction() as connection:
            connection.execute(
                """INSERT INTO document_content(singleton, content) VALUES (1, ?)
                   ON CONFLICT(singleton) DO UPDATE SET content = excluded.content""",
                (value,),
            )
