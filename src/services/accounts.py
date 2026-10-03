"""Site administrator account lifecycle, with atomic retention checks."""
import json
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from ..core.errors import AlreadyExists, Conflict, Forbidden, InvalidArgument, NotFound, Unauthenticated
from ..identity.models import AccountTokenGrant, user_view
from ..identity.tokens import new_token, token_digest, ACTIVATION_HOURS, RESET_HOURS
from ..identity.validation import normalize_login_name, validate_display_name
from ..storage.identity_repository import UserRecord, AccountTokenRecord
from ..storage.audit_repository import AuditEventRecord
from .unit_of_work import ApplicationUnitOfWork


class AccountService:
    def __init__(self, database, *, clock=None):
        self._uow = ApplicationUnitOfWork(database, clock=clock)

    @staticmethod
    def _admin(work, token):
        actor = work.resolve_principal(token)
        if actor.user_id is None:
            raise Unauthenticated('authentication required')
        if not actor.site_admin:
            raise Forbidden('site administrator required')
        return actor.user_id

    @staticmethod
    def _audit(work, actor, before, after, event):
        def state(user):
            if user is None:
                return None
            return json.dumps({'status': user.status, 'site_admin': user.site_admin,
                               'version': user.version}, sort_keys=True)
        work.audit.append(AuditEventRecord(str(uuid4()), actor, None, 'accounts.' + event,
            'user', after.id, state(before), state(after), work.now.isoformat()))

    @staticmethod
    def _grant(work, user, purpose):
        raw = new_token()
        expires = (work.now + timedelta(hours=ACTIVATION_HOURS if purpose == 'activate' else RESET_HOURS)).isoformat()
        work.identity.insert_account_token(AccountTokenRecord(token_digest(raw), user.id,
            purpose, user.credential_version, work.now.isoformat(), expires, None, None))
        return AccountTokenGrant(user_view(user), raw, purpose, expires)

    def list_users(self, *, session_token):
        with self._uow.transaction() as work:
            self._admin(work, session_token)
            return tuple(user_view(user) for user in work.identity.list_users())

    def create_user(self, login_name, display_name, *, session_token):
        login_name = normalize_login_name(login_name)
        display_name = validate_display_name(display_name)
        with self._uow.transaction(write=True) as work:
            actor = self._admin(work, session_token)
            if work.identity.get_user_by_login_name(login_name):
                raise AlreadyExists('login name already exists')
            now = work.now.isoformat()
            user = UserRecord(str(uuid4()), login_name, display_name, 'invited', False, 1, 1, now, now)
            work.identity.insert_user(user)
            grant = self._grant(work, user, 'activate')
            self._audit(work, actor, None, user, 'create')
            return grant

    def _change(self, user_id, *, session_token, expected_version, operation, enabled=None):
        with self._uow.transaction(write=True) as work:
            actor = self._admin(work, session_token)
            user = work.identity.get_user(user_id)
            if user is None:
                raise NotFound('account not found')
            if user.version != expected_version:
                raise Conflict('account changed')
            status, admin = user.status, user.site_admin
            purpose = None
            if operation == 'resend_activation':
                if status != 'invited':
                    raise InvalidArgument('only invited accounts can receive activation')
                purpose = 'activate'
            elif operation == 'reset':
                if status not in ('active', 'reset_required'):
                    raise InvalidArgument('account cannot be reset')
                status, purpose = 'reset_required', 'reset'
            elif operation == 'disable':
                status = 'disabled'
            elif operation == 'enable':
                if status != 'disabled':
                    raise InvalidArgument('only disabled accounts can be enabled')
                status, purpose = 'reset_required', 'reset'
            elif operation == 'set_site_admin':
                admin = enabled
            if user.status == 'active' and user.site_admin and (status != 'active' or not admin):
                if work.identity.count_active_site_admins(excluding_user_id=user.id) == 0:
                    raise Forbidden('last active site administrator must be retained')
            if user.status in ('active', 'reset_required') and status == 'disabled':
                access = work.access(work.default_scope())
                for workspace_id in access.owner_workspace_ids(user.id):
                    if access.count_effective_owners_in_workspace(workspace_id, excluding_user_id=user.id) == 0:
                        raise Forbidden('last effective workspace owner must be retained')
            if operation in ('disable', 'set_site_admin') and status == user.status and admin == user.site_admin:
                return user_view(user)
            now = work.now.isoformat()
            revoke = operation != 'set_site_admin'
            updated = replace(user, status=status, site_admin=admin, version=user.version + 1,
                credential_version=user.credential_version + int(revoke), modified_at=now)
            if not work.identity.update_user(updated, expected_version=expected_version):
                raise Conflict('account changed')
            if revoke:
                work.identity.revoke_sessions(user.id, now=now)
                work.identity.revoke_account_tokens(user.id, now=now)
                work.identity.delete_password_credential(user.id)
            grant = self._grant(work, updated, purpose) if purpose else None
            self._audit(work, actor, user, updated, operation)
            return grant or user_view(updated)

    def resend_activation(self, user_id, *, session_token, expected_version):
        return self._change(user_id, session_token=session_token, expected_version=expected_version, operation='resend_activation')

    def reset_user(self, user_id, *, session_token, expected_version):
        return self._change(user_id, session_token=session_token, expected_version=expected_version, operation='reset')

    def disable_user(self, user_id, *, session_token, expected_version):
        return self._change(user_id, session_token=session_token, expected_version=expected_version, operation='disable')

    def enable_user(self, user_id, *, session_token, expected_version):
        return self._change(user_id, session_token=session_token, expected_version=expected_version, operation='enable')

    def set_site_admin(self, user_id, *, session_token, expected_version, enabled):
        if not isinstance(enabled, bool):
            raise InvalidArgument('enabled must be boolean')
        return self._change(user_id, session_token=session_token, expected_version=expected_version, operation='set_site_admin', enabled=enabled)
