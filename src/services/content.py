"""Temporary unauthed facade for regression validation during extraction.

Identity enforcement replaces this facade in the approved integration tasks.
"""
from __future__ import annotations

import json
from ..core.errors import InvalidArgument
from ..core.json_values import thaw_json, validate_metadata
from ..core.paths import parse_path, validate_name
from .content_operations import ContentOperations

from ..core.models import ContentScope, DeleteSnapshot, DocumentSnapshot, NodeSnapshot, TreeItem
from ..storage.database import Database
from .unit_of_work import ApplicationUnitOfWork

__all__ = ["ContentService"]

class ContentService:
    def __init__(self, database: Database) -> None:
        self._uow = ApplicationUnitOfWork(database)

    _require_str = staticmethod(ContentOperations._require_str)
    _require_positive_version = staticmethod(ContentOperations._require_positive_version)

    def _read(self):
        return self._uow.transaction()

    def _write(self):
        return self._uow.transaction(write=True)

    def default_scope(self) -> ContentScope:
        with self._uow.transaction() as work:
            return work.default_scope()

    def get_node(self, scope: ContentScope, object_id: str) -> NodeSnapshot:
        with self._read() as work:
            return work.content(scope).get_node(scope, object_id)

    def get_path(self, scope: ContentScope, object_id: str) -> str:
        with self._read() as work:
            return work.content(scope).get_path(scope, object_id)

    def resolve_path(self, scope: ContentScope, path: str, *, cwd_id: str | None=None) -> NodeSnapshot:
        parse_path(path)
        with self._read() as work:
            return work.content(scope).resolve_path(scope, path, cwd_id=cwd_id)

    def list_children(self, scope: ContentScope, folder_id: str) -> list[NodeSnapshot]:
        with self._read() as work:
            return work.content(scope).list_children(scope, folder_id)

    def list_tree(self, scope: ContentScope, folder_id: str, *, max_depth: int | None=None) -> list[TreeItem]:
        if max_depth is not None and (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
        ):
            raise InvalidArgument(
                "max_depth must be a non-negative integer or None",
                details={"max_depth": max_depth},
            )
        with self._read() as work:
            return work.content(scope).list_tree(scope, folder_id, max_depth=max_depth)

    def get_metadata(self, scope: ContentScope, object_id: str) -> dict:
        with self._read() as work:
            return work.content(scope).get_metadata(scope, object_id)

    def create_folder(self, scope: ContentScope, parent_id: str, name: str) -> NodeSnapshot:
        self._require_str(parent_id, "parent_id")
        validate_name(name)
        with self._uow.name_conflicts({"parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).create_folder(scope, parent_id, name)

    def create_document(self, scope: ContentScope, parent_id: str, name: str, *, content: str='') -> DocumentSnapshot:
        self._require_str(parent_id, "parent_id")
        validate_name(name)
        self._require_str(content, "content")
        with self._uow.name_conflicts({"parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).create_document(scope, parent_id, name, content=content)

    def read_document(self, scope: ContentScope, object_id: str) -> DocumentSnapshot:
        self._require_str(object_id, "object_id")
        with self._read() as work:
            return work.content(scope).read_document(scope, object_id)

    def save_document(self, scope: ContentScope, object_id: str, content: str, *, expected_revision_id: str) -> DocumentSnapshot:
        self._require_str(object_id, "object_id")
        self._require_str(content, "content")
        self._require_str(expected_revision_id, "expected_revision_id")
        with self._write() as work:
            return work.content(scope).save_document(scope, object_id, content, expected_revision_id=expected_revision_id)

    def set_metadata(self, scope: ContentScope, object_id: str, changes: dict, *, expected_version: int) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        self._require_positive_version(expected_version)
        validate_metadata(changes)
        try:
            changes = json.loads(json.dumps(
                thaw_json(changes), ensure_ascii=False, allow_nan=False,
                separators=(",", ":"), sort_keys=True,
            ).encode("utf-8"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise InvalidArgument("metadata is not JSON serialisable") from exc
        with self._write() as work:
            return work.content(scope).set_metadata(scope, object_id, changes, expected_version=expected_version)

    def rename_node(self, scope: ContentScope, object_id: str, name: str, *, expected_version: int) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        validate_name(name)
        self._require_positive_version(expected_version)
        with self._uow.name_conflicts({"object_id": object_id, "name": name}):
            with self._write() as work:
                return work.content(scope).rename_node(scope, object_id, name, expected_version=expected_version)

    def move_node(self, scope: ContentScope, object_id: str, parent_id: str, *, expected_version: int, name: str | None=None) -> NodeSnapshot:
        self._require_str(object_id, "object_id")
        self._require_str(parent_id, "parent_id")
        self._require_positive_version(expected_version)
        if name is not None:
            validate_name(name)
        with self._uow.name_conflicts({"object_id": object_id, "parent_id": parent_id, "name": name}):
            with self._write() as work:
                return work.content(scope).move_node(scope, object_id, parent_id, expected_version=expected_version, name=name)

    def prepare_delete(self, scope: ContentScope, folder_id: str) -> DeleteSnapshot:
        self._require_str(folder_id, "folder_id")
        with self._read() as work:
            return work.content(scope).prepare_delete(scope, folder_id)

    def delete_node(self, scope: ContentScope, object_id: str, *, expected_version: int, recursive: bool=False, expected_subtree_token: str | None=None) -> None:
        self._require_str(object_id, "object_id")
        self._require_positive_version(expected_version)
        if not isinstance(recursive, bool):
            raise InvalidArgument(
                "recursive must be a boolean",
                details={"recursive": recursive},
            )
        if recursive:
            if (
                not isinstance(expected_subtree_token, str)
                or expected_subtree_token == ""
            ):
                raise InvalidArgument(
                    "recursive delete requires a non-empty expected_subtree_token",
                    details={"object_id": object_id},
                )
        elif expected_subtree_token is not None:
            raise InvalidArgument(
                "non-recursive delete does not accept an expected_subtree_token",
                details={"object_id": object_id},
            )
        with self._write() as work:
            return work.content(scope).delete_node(scope, object_id, expected_version=expected_version, recursive=recursive, expected_subtree_token=expected_subtree_token)
