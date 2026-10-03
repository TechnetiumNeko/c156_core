"""Authentication use cases with committed throttles and short atomic writes."""
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from ..core.errors import Conflict, InvalidArgument, RateLimited, Unauthenticated
from ..identity.models import SessionGrant, SessionView, user_view
from ..identity.passwords import PasswordHasher
from ..identity.tokens import new_token, token_digest, SESSION_HOURS
from ..identity.validation import normalize_login_name, validate_display_name, validate_password
from ..storage.identity_repository import PasswordCredentialRecord, SessionRecord
from ..storage.audit_repository import AuditEventRecord
from .unit_of_work import ApplicationUnitOfWork


class IdentityService:
    def __init__(self, database, *, clock=None, password_hasher=None):
        self._uow = ApplicationUnitOfWork(database, clock=clock)
        self._passwords = password_hasher or PasswordHasher()

    def _throttle(self, source, bucket_type, bucket_key):
        if not isinstance(source, str) or not source:
            raise InvalidArgument('authentication source is required')
        with self._uow.transaction(write=True) as work:
            results = [work.throttles.consume(kind, key, limit=limit, now=work.now)
                       for kind, key, limit in [('source', source, 60), (bucket_type, bucket_key, 10)]]
        retry = max((result.retry_after for result in results if not result.allowed), default=0)
        if retry:
            raise RateLimited('too many authentication attempts', details={'retry_after': retry})

    @staticmethod
    def _fail():
        raise Unauthenticated('authentication failed')

    @staticmethod
    def _audit(work, user_id, event):
        work.audit.append(AuditEventRecord(str(uuid4()), user_id, None, event, 'user',
                                           user_id, None, None, work.now.isoformat()))

    def login(self, login_name, password, *, source):
        # Invalid usernames still consume both buckets before validation refusal.
        key = login_name.lower() if isinstance(login_name, str) else ''
        self._throttle(source, 'login', key)
        try:
            login_name = normalize_login_name(login_name)
        except InvalidArgument:
            self._fail()
        if not isinstance(password, str):
            self._fail()
        with self._uow.transaction() as work:
            user = work.identity.get_user_by_login_name(login_name)
            credential = work.identity.get_password_credential(user.id) if user else None
        valid = self._passwords.verify(credential.password_hash if credential else self._passwords.dummy_hash, password)
        if not valid or user is None or credential is None or user.status != 'active':
            self._fail()
        with self._uow.transaction(write=True) as work:
            current = work.identity.get_user(user.id)
            current_credential = work.identity.get_password_credential(user.id)
            if (current is None or current.status != 'active'
                    or current.credential_version != user.credential_version
                    or current_credential != credential
                    or credential.credential_version != current.credential_version):
                self._fail()
            token, csrf = new_token(), new_token()
            expires = (work.now + timedelta(hours=SESSION_HOURS)).isoformat()
            work.identity.insert_session(SessionRecord(token_digest(token), user.id,
                current.credential_version, csrf, work.now.isoformat(), expires, None))
            return SessionGrant(user_view(current), token, csrf, expires)

    def current_session(self, *, session_token):
        with self._uow.transaction() as work:
            principal = work.resolve_principal(session_token)
            if principal.user_id is None:
                self._fail()
            session = work.identity.get_session(token_digest(session_token))
            return SessionView(user_view(work.identity.get_user(principal.user_id)), session.csrf_token, session.expires_at)

    def logout(self, *, session_token):
        with self._uow.transaction(write=True) as work:
            if work.resolve_principal(session_token).user_id is None:
                self._fail()
            work.identity.revoke_session(token_digest(session_token), now=work.now.isoformat())

    @staticmethod
    def _valid_account_token(work, digest, purpose):
        token = work.identity.get_account_token(digest)
        user = work.identity.get_user(token.user_id) if token else None
        required_status = 'invited' if purpose == 'activate' else 'reset_required'
        if (token is None or user is None or token.purpose != purpose
                or token.consumed_at is not None or token.revoked_at is not None
                or datetime.fromisoformat(token.expires_at) <= work.now
                or user.status != required_status
                or token.credential_version != user.credential_version):
            raise Unauthenticated('authentication failed')
        return token, user

    def _consume_account_token(self, token, password, source, purpose):
        invalid_token = not isinstance(token, str) or not token
        digest = token_digest(token if not invalid_token else 'invalid-account-token')
        self._throttle(source, 'token', digest)
        if invalid_token:
            self._fail()
        validate_password(password)
        with self._uow.transaction() as work:
            snapshot, user = self._valid_account_token(work, digest, purpose)
        encoded = self._passwords.hash(password)
        with self._uow.transaction(write=True) as work:
            current_token, current = self._valid_account_token(work, digest, purpose)
            if current_token != snapshot or current.credential_version != user.credential_version:
                self._fail()
            now = work.now.isoformat()
            if not work.identity.consume_account_token(digest, now=now, credential_version=current.credential_version):
                self._fail()
            updated = replace(current, status='active', version=current.version + 1,
                              credential_version=current.credential_version + 1, modified_at=now)
            if not work.identity.update_user(updated, expected_version=current.version):
                raise Conflict('account changed')
            work.identity.set_password_credential(PasswordCredentialRecord(current.id, encoded, updated.credential_version, now))
            work.identity.revoke_sessions(current.id, now=now)
            work.identity.revoke_account_tokens(current.id, now=now)
            self._audit(work, current.id, 'identity.' + purpose)
            return user_view(updated)

    def activate(self, token, password, *, source):
        return self._consume_account_token(token, password, source, 'activate')

    def reset_password(self, token, password, *, source):
        return self._consume_account_token(token, password, source, 'reset')

    def change_password(self, old_password, new_password, *, session_token, source):
        # Source accounting also persists when the supplied session is invalid.
        invalid_token = not isinstance(session_token, str) or not session_token
        digest = token_digest(session_token if not invalid_token else 'invalid')
        with self._uow.transaction() as work:
            session = work.identity.get_session(digest) if not invalid_token else None
        self._throttle(source, 'password', session.user_id if session else digest)
        if invalid_token:
            self._fail()
        validate_password(new_password)
        with self._uow.transaction() as work:
            principal = work.resolve_principal(session_token)
            if principal.user_id is None:
                self._fail()
            user = work.identity.get_user(principal.user_id)
            credential = work.identity.get_password_credential(user.id)
        if credential is None or not isinstance(old_password, str) or not self._passwords.verify(credential.password_hash, old_password):
            self._fail()
        encoded = self._passwords.hash(new_password)
        with self._uow.transaction(write=True) as work:
            principal = work.resolve_principal(session_token)
            current = work.identity.get_user(principal.user_id)
            if current.credential_version != user.credential_version or work.identity.get_password_credential(user.id) != credential:
                self._fail()
            now = work.now.isoformat()
            updated = replace(current, version=current.version + 1, credential_version=current.credential_version + 1, modified_at=now)
            if not work.identity.update_user(updated, expected_version=current.version):
                raise Conflict('account changed')
            work.identity.set_password_credential(PasswordCredentialRecord(user.id, encoded, updated.credential_version, now))
            work.identity.revoke_sessions(user.id, now=now)
            work.identity.revoke_account_tokens(user.id, now=now)
            self._audit(work, user.id, 'identity.change_password')

    def change_display_name(self, display_name, *, session_token, expected_version):
        display_name = validate_display_name(display_name)
        with self._uow.transaction(write=True) as work:
            principal = work.resolve_principal(session_token)
            if principal.user_id is None:
                self._fail()
            user = work.identity.get_user(principal.user_id)
            if user.version != expected_version:
                raise Conflict('account changed')
            if user.display_name == display_name:
                return user_view(user)
            updated = replace(user, display_name=display_name, version=user.version + 1, modified_at=work.now.isoformat())
            if not work.identity.update_user(updated, expected_version=expected_version):
                raise Conflict('account changed')
            self._audit(work, user.id, 'identity.change_display_name')
            return user_view(updated)
