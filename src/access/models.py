"""Detached public access values and structural policy inputs."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from src.identity.models import UserView

class PolicyNode(Protocol):
    object_id: str
    kind: str

class Membership(Protocol):
    user_id: str
    role: str
    status: str

class AccessSettings(Protocol):
    read_scope: str
    version: int

@dataclass(frozen=True)
class AccessRule:
    object_id: str
    subject_type: str
    subject_key: str
    action: str
    effect: str

@dataclass(frozen=True)
class MembershipView:
    user: UserView
    role: str
    status: str

@dataclass(frozen=True)
class WorkspaceAccessView:
    version: int
    read_scope: str
    members: tuple[MembershipView, ...]

    def __post_init__(self):
        object.__setattr__(self, 'members', tuple(self.members))

@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    source_object_id: str | None

@dataclass(frozen=True)
class ObjectAccessView:
    version: int
    rules: tuple[AccessRule, ...]
    visibility: str
    private_owner_id: str | None
    locked_by: str | None
    inherited_rules: tuple[AccessRule, ...]
    decisions: Mapping[str, Decision]

    def __post_init__(self):
        object.__setattr__(self, 'rules', tuple(self.rules))
        object.__setattr__(self, 'inherited_rules', tuple(self.inherited_rules))
        object.__setattr__(self, 'decisions', MappingProxyType(dict(self.decisions)))

@dataclass(frozen=True)
class ContentAccessView:
    version: int
    actions: tuple[str, ...]
    visibility: str
    frozen: bool
    can_freeze: bool
    can_unfreeze: bool

    def __post_init__(self):
        object.__setattr__(self, 'actions', tuple(self.actions))
