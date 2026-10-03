"""Pure authorization over a caller-validated full root-to-target chain.

Identity resolution rejects invalid credentials before constructing this policy.
No database records are retained: all input values are copied at construction.
"""
from types import MappingProxyType
from typing import Iterable, Mapping

from src.core.errors import Forbidden, Frozen, InvalidArgument, NotFound
from src.identity.models import Principal
from .models import AccessRule, AccessSettings, Decision, Membership, PolicyNode

ACTIONS = ('read', 'edit', 'create', 'rename', 'move', 'delete', 'review', 'publish')
EDITOR_ACTIONS = frozenset(('read', 'edit', 'create', 'rename', 'move', 'delete'))

class AccessPolicy:
    def __init__(self, principal: Principal, membership: Membership | None,
                 settings: AccessSettings, *, rules: Iterable[AccessRule],
                 privacy: Mapping[str, str], locks: Mapping[str, str],
                 ownership: Mapping[str, str | None]):
        self.principal = Principal(principal.user_id, principal.site_admin)
        self.role = (membership.role if membership is not None
                     and membership.user_id == principal.user_id
                     and principal.user_id is not None and membership.status == 'active'
                     else None)
        self.read_scope = settings.read_scope
        self.version = settings.version
        copied = tuple(AccessRule(r.object_id, r.subject_type, r.subject_key,
                                  r.action, r.effect) for r in rules)
        self.rules = copied
        self.privacy = MappingProxyType(dict(privacy))
        self.locks = MappingProxyType(dict(locks))
        self.ownership = MappingProxyType(dict(ownership))
        self._rules = MappingProxyType({(r.object_id, r.subject_type, r.subject_key, r.action): r
                                        for r in copied})

    @property
    def management(self) -> bool:
        return self.role in ('admin', 'owner')

    def _action(self, chain: tuple[PolicyNode, ...], action: str) -> Decision:
        subjects = []
        if self.principal.user_id is not None:
            subjects.append(('user', self.principal.user_id))
        if self.role is not None:
            subjects.append(('role', self.role))
        if action == 'read':
            if self.principal.user_id is not None:
                subjects.append(('authenticated', ''))
            subjects.append(('everyone', ''))
        for node in reversed(chain):
            for subject_type, subject_key in subjects:
                rule = self._rules.get((node.object_id, subject_type, subject_key, action))
                if rule is not None:
                    return Decision(rule.effect == 'allow', 'rule:' + subject_type, node.object_id)
        if action == 'read':
            allowed = (self.role is not None or self.read_scope == 'everyone'
                       or (self.read_scope == 'authenticated' and self.principal.user_id is not None))
            return Decision(allowed, 'role_baseline' if self.role else 'read_scope', None)
        return Decision(self.role == 'editor' and action in EDITOR_ACTIONS,
                        'role_baseline', None)

    def decide(self, chain: tuple[PolicyNode, ...], action: str) -> Decision:
        if action not in ACTIONS:
            raise InvalidArgument('Unknown content action')
        if not chain:
            raise InvalidArgument('A full ancestry chain is required')
        if action == 'create' and chain[-1].kind != 'folder':
            return Decision(False, 'create_requires_folder', chain[-1].object_id)
        if self.management:
            return Decision(True, 'management_override', None)
        for index, node in enumerate(chain):
            owner = self.privacy.get(node.object_id)
            if owner is not None and (self.role is None or owner != self.principal.user_id):
                return Decision(False, 'private' if index == len(chain) - 1 else 'ancestor_private', node.object_id)
            read = self._action(chain[:index + 1], 'read')
            if not read.allowed:
                if index != len(chain) - 1:
                    return Decision(False, 'ancestor_read:' + read.reason, read.source_object_id)
                return read
        if action == 'read':
            return self._action(chain, 'read')
        if self.role is None:
            return Decision(False, 'active_membership_required', None)
        return self._action(chain, action)

    def can_read(self, chain: tuple[PolicyNode, ...]) -> bool:
        return self.decide(chain, 'read').allowed

    def require_action(self, chain: tuple[PolicyNode, ...], action: str) -> None:
        if not self.can_read(chain):
            raise NotFound('Object not found')
        if not self.decide(chain, action).allowed:
            raise Forbidden('Content action is not permitted')

    def require_unfrozen(self, records: tuple[PolicyNode, ...], *, operation: str) -> None:
        """Independent constraint, called AFTER normal action checks on all records.

        Records may be a full active subtree; normal ancestry checks belong to
        require_action, never to this lock-only check. Locks grant no rights.
        """
        if operation not in ACTIONS or operation == 'read':
            raise InvalidArgument('A modifying content operation is required')
        for node in records:
            actor = self.locks.get(node.object_id)
            if node.kind == 'document' and actor is not None and (
                    self.role is None or actor != self.principal.user_id):
                raise Frozen('Content is frozen')
