"""Detached combinations of public content and access snapshots."""
from dataclasses import dataclass
from ..access.models import ContentAccessView
from ..core.models import NodeSnapshot, DocumentSnapshot
from ..identity.models import SessionView

@dataclass(frozen=True)
class NodeAccessView:
    node: NodeSnapshot
    access: ContentAccessView

@dataclass(frozen=True)
class DocumentAccessView:
    document: DocumentSnapshot
    access: ContentAccessView

@dataclass(frozen=True)
class BootstrapView:
    initialized: bool
    session: SessionView | None
    workspace_role: str | None
    workspace_access_version: int | None
    root: NodeAccessView | None

@dataclass(frozen=True)
class RevisionSummary:
    revision_id: str
    parent_revision_id: str | None
    actor_id: str | None
    actor_display_name: str | None
    source_kind: str
    restored_from_revision_id: str | None
    created_at: str

@dataclass(frozen=True)
class RevisionView(RevisionSummary):
    content: str

@dataclass(frozen=True)
class RevisionPage:
    revisions: tuple[RevisionSummary, ...]
    head_revision_id: str
    next_cursor: str | None

@dataclass(frozen=True)
class RevisionDiff:
    from_revision_id: str
    to_revision_id: str
    diff: str

@dataclass(frozen=True)
class DeletedDocumentSummary:
    object_id: str
    name: str
    path: str

@dataclass(frozen=True)
class DeletedDocumentPage:
    documents: tuple[DeletedDocumentSummary, ...]
    next_cursor: str | None
