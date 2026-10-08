"""Application transaction boundary; repositories share one SQLite connection."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator
from datetime import datetime, timezone

from ..core.errors import AlreadyExists, StorageBusy, UnsupportedSchema, Unauthenticated
from ..identity.models import Principal
from ..identity.tokens import token_digest
from ..core.models import ContentScope
from ..storage.database import Database
from ..storage.errors import BusyError, ConstraintError, SchemaError
from ..storage.repository import Repository, is_sibling_name_conflict, lookup_default_main
from ..storage.management import validate_default_tree
from ..storage.identity_repository import IdentityRepository
from ..storage.access_repository import AccessRepository
from ..storage.audit_repository import AuditRepository
from ..storage.auth_throttle_repository import AuthThrottleRepository
from .content_operations import ContentOperations


class _ApplicationTransaction:
    """Internal context-only handle; return snapshots rather than this handle."""

    def __init__(self, connection, clock: Callable[[], datetime]) -> None:
        self._connection = connection
        instant = clock()
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        self.now = instant.astimezone(timezone.utc)
        self.clock = lambda: self.now
        self.identity = IdentityRepository(connection)
        self.audit = AuditRepository(connection)
        self.throttles = AuthThrottleRepository(connection)

    def _require_active(self) -> None:
        if self._connection is None:
            raise RuntimeError("application transaction has ended")

    def resolve_principal(self, token: str | None) -> Principal:
        self._require_active()
        if token is None:
            return Principal(None, False)
        session = self.identity.get_session(token_digest(token))
        user = self.identity.get_user(session.user_id) if session else None
        if (session is None or user is None or session.revoked_at is not None
                or datetime.fromisoformat(session.expires_at) <= self.now
                or user.status != 'active'
                or session.credential_version != user.credential_version):
            raise Unauthenticated('authentication failed')
        return Principal(user.id, user.site_admin)

    def content(self, scope: ContentScope) -> ContentOperations:
        self._require_active()
        return ContentOperations(Repository(self._connection, workspace_id=scope.workspace_id, branch_id=scope.branch_id))

    def access(self, scope: ContentScope) -> AccessRepository:
        self._require_active()
        return AccessRepository(self._connection, workspace_id=scope.workspace_id, branch_id=scope.branch_id)

    def policy(self, scope, principal):
        """Copy authorization records into one immutable transaction snapshot."""
        from ..access.policy import AccessPolicy
        from ..core.errors import NotFound
        access = self.access(scope)
        settings = access.get_settings()
        if settings is None:
            raise NotFound("Object not found")
        ownership = access.list_ownership()
        return AccessPolicy(principal,
            access.get_membership(principal.user_id) if principal.user_id else None,
            settings, rules=access.list_rules(),
            privacy={r.object_id: r.owner_id for r in access.list_privacy()},
            locks={r.object_id: r.locked_by for r in access.list_locks()},
            ownership={r.object_id: r.creator_id for r in ownership})

    def authorized_content(self, scope, principal):
        return ContentOperations(Repository(self._connection,
            workspace_id=scope.workspace_id, branch_id=scope.branch_id),
            policy=self.policy(scope, principal))

    def history(self, scope, principal):
        from .history import HistoryOperations
        self._require_active()
        return HistoryOperations(Repository(self._connection,
            workspace_id=scope.workspace_id, branch_id=scope.branch_id),
            self.policy(scope, principal), self.identity)

    def configured_scope(self) -> ContentScope:
        """Locate the fixed display root without validating sibling contents."""
        self._require_active()
        from ..core.errors import NotFound
        workspace_id, branch_id = lookup_default_main(self._connection)
        repo = Repository(self._connection, workspace_id=workspace_id, branch_id=branch_id)
        root_id = repo.get_branch_root_id()
        main = repo.find_child(root_id, "main") if root_id else None
        if main is None:
            raise NotFound("Object not found")
        return ContentScope(workspace_id, branch_id, main.object_id)

    def default_scope(self) -> ContentScope:
        self._require_active()
        root_scope = validate_default_tree(self._connection)
        return self.content(root_scope).default_scope()


class ApplicationUnitOfWork:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] | None = None) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[_ApplicationTransaction]:
        """Own one read snapshot or BEGIN IMMEDIATE write transaction."""
        try:
            with self._database.transaction(write=write) as connection:
                work = _ApplicationTransaction(connection, self._clock)
                try:
                    yield work
                finally:
                    work._connection = None
        except BusyError as exc:
            raise StorageBusy(str(exc), details=dict(exc.details)) from exc
        except SchemaError as exc:
            raise UnsupportedSchema(str(exc), details=dict(exc.details)) from exc

    @staticmethod
    @contextmanager
    def name_conflicts(details: dict) -> Iterator[None]:
        """Translate only the active sibling-name constraint at the boundary."""
        try:
            yield
        except ConstraintError as exc:
            if is_sibling_name_conflict(exc):
                raise AlreadyExists("an active sibling already uses this name", details=dict(details)) from exc
            raise
