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
