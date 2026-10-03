"""Explicit local first-administrator bootstrap for initialized empty libraries."""
from uuid import uuid4

from ..core.errors import Forbidden
from ..identity.models import user_view
from ..identity.passwords import PasswordHasher
from ..identity.validation import normalize_login_name, validate_display_name, validate_password
from ..storage.identity_repository import UserRecord, PasswordCredentialRecord
from ..storage.access_repository import MembershipRecord
from ..storage.audit_repository import AuditEventRecord
from .unit_of_work import ApplicationUnitOfWork


def bootstrap_admin(database, login_name, display_name, password):
    login_name = normalize_login_name(login_name)
    display_name = validate_display_name(display_name)
    validate_password(password)
    encoded = PasswordHasher().hash(password)
    with ApplicationUnitOfWork(database).transaction(write=True) as work:
        scope = work.default_scope()
        access = work.access(scope)
        if work.identity.list_users() or access.has_any_owner():
            raise Forbidden('bootstrap requires an empty unowned library')
        now = work.now.isoformat()
        user = UserRecord(str(uuid4()), login_name, display_name, 'active', True, 1, 1, now, now)
        work.identity.insert_user(user)
        work.identity.set_password_credential(PasswordCredentialRecord(user.id, encoded, 1, now))
        access.insert_membership(MembershipRecord(scope.workspace_id, user.id, 'owner', 'active', 1, now, now))
        work.audit.append(AuditEventRecord(str(uuid4()), user.id, scope.workspace_id,
            'accounts.bootstrap', 'user', user.id, None, None, now))
        return user_view(user)
