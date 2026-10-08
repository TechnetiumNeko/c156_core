"""Plain database records returned by the repository.

These frozen dataclasses mirror the stored row shape.  They carry raw stored
values (``metadata_json`` stays a compact JSON string and revision ``content``
stays raw text); the service layer owns validation and snapshot assembly.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["EntryRecord", "RevisionRecord"]


@dataclass(frozen=True)
class EntryRecord:
    """One row of ``entries`` joined with its object ``kind``."""

    workspace_id: str
    branch_id: str
    object_id: str
    kind: str
    parent_id: str | None
    name: str
    position: int
    version: int
    current_revision_id: str | None
    metadata_json: str
    created_at: str
    modified_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class RevisionRecord:
    """One immutable row of ``document_revisions``."""

    id: str
    workspace_id: str
    object_id: str
    parent_revision_id: str | None
    content: str
    created_at: str
    actor_id: str | None = None
    source_kind: str = "unknown"
    restored_from_revision_id: str | None = None
