"""Immutable pure-data result types for the content kernel.

These dataclasses hold no storage handles and never open a database.  Snapshot
collections are tuples, metadata is deep-frozen into read-only mappings and
tuples, and ``DeleteSnapshot`` carries the canonical subtree token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .json_values import freeze_json

__all__ = [
    "ContentScope",
    "NodeSnapshot",
    "DocumentSnapshot",
    "TreeItem",
    "DeleteSnapshot",
]


@dataclass(frozen=True)
class ContentScope:
    """Immutable access scope: workspace, branch and access-root object."""

    workspace_id: str
    branch_id: str
    root_id: str


@dataclass(frozen=True)
class NodeSnapshot:
    """A read-only view of one entry within a scope."""

    id: str
    kind: str
    name: str
    parent_id: str | None
    position: int
    version: int
    path: str
    created_at: str
    modified_at: str
    deleted_at: str | None
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_json(self.metadata))


@dataclass(frozen=True)
class DocumentSnapshot(NodeSnapshot):
    """A node snapshot that also carries the current revision content."""

    content: str
    revision_id: str


@dataclass(frozen=True)
class TreeItem:
    """One node in a pre-order tree listing together with its depth."""

    node: NodeSnapshot
    depth: int


@dataclass(frozen=True)
class DeleteSnapshot:
    """Immutable description of a subtree prepared for recursive deletion."""

    object_id: str
    version: int
    items: tuple[TreeItem, ...]
    subtree_token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
