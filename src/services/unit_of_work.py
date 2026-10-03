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
from ..storage.repository import Repository, is_sibling_name_conflict
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
