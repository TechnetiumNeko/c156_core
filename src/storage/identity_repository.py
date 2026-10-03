"""Typed storage records and SQL only; caller owns connection and transaction."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, astuple

@dataclass(frozen=True)
class UserRecord:
    id: str
    login_name: str
    display_name: str
    status: str
    site_admin: bool
    version: int
    credential_version: int
    created_at: str
    modified_at: str


@dataclass(frozen=True)
class PasswordCredentialRecord:
    user_id: str
    password_hash: str
    credential_version: int
    updated_at: str


@dataclass(frozen=True)
class AccountTokenRecord:
    token_digest: str
    user_id: str
    purpose: str
    credential_version: int
    issued_at: str
    expires_at: str
    consumed_at: str|None
    revoked_at: str|None


@dataclass(frozen=True)
class SessionRecord:
    token_digest: str
    user_id: str
    credential_version: int
    csrf_token: str
    issued_at: str
    expires_at: str
    revoked_at: str|None


class IdentityRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get_user(self, user_id: str) -> UserRecord | None:
        row = self.connection.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        return UserRecord(**{**dict(row), 'site_admin': bool(row['site_admin'])}) if row else None

    def get_user_by_login_name(self, login_name: str) -> UserRecord | None:
        row = self.connection.execute('SELECT id FROM users WHERE login_name=?', (login_name,)).fetchone()
        return self.get_user(row['id']) if row else None

    def list_users(self) -> tuple[UserRecord, ...]:
        return tuple(UserRecord(**{**dict(r), 'site_admin': bool(r['site_admin'])}) for r in self.connection.execute('SELECT * FROM users ORDER BY login_name'))

    def insert_user(self, record: UserRecord) -> None:
        self.connection.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)', astuple(record))

    def update_user(self, record: UserRecord, *, expected_version: int) -> bool:
        cursor = self.connection.execute('UPDATE users SET display_name=?,status=?,site_admin=?,version=?,credential_version=?,modified_at=? WHERE id=? AND version=?', (record.display_name,record.status,record.site_admin,record.version,record.credential_version,record.modified_at,record.id,expected_version))
        return cursor.rowcount == 1

    def count_active_site_admins(self, *, excluding_user_id: str | None = None) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM users WHERE status='active' AND site_admin=1 AND (? IS NULL OR id<>?)", (excluding_user_id,excluding_user_id)).fetchone()[0]

    def get_password_credential(self, user_id: str) -> PasswordCredentialRecord | None:
        row = self.connection.execute('SELECT * FROM password_credentials WHERE user_id=?', (user_id,)).fetchone()
        return PasswordCredentialRecord(**dict(row)) if row else None

    def set_password_credential(self, record: PasswordCredentialRecord) -> None:
        self.connection.execute('INSERT INTO password_credentials VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET password_hash=excluded.password_hash,credential_version=excluded.credential_version,updated_at=excluded.updated_at', astuple(record))

    def delete_password_credential(self, user_id: str) -> None:
        self.connection.execute('DELETE FROM password_credentials WHERE user_id=?', (user_id,))

    def get_account_token(self, token_digest: str) -> AccountTokenRecord | None:
        row = self.connection.execute('SELECT * FROM account_tokens WHERE token_digest=?', (token_digest,)).fetchone()
        return AccountTokenRecord(**dict(row)) if row else None

    def list_account_tokens(self, user_id: str, *, purpose: str | None = None) -> tuple[AccountTokenRecord, ...]:
        return tuple(AccountTokenRecord(**dict(row)) for row in self.connection.execute(
            'SELECT * FROM account_tokens WHERE user_id=? AND (? IS NULL OR purpose=?) ORDER BY issued_at,token_digest',
            (user_id, purpose, purpose),
        ))

    def insert_account_token(self, record: AccountTokenRecord) -> None:
        self.connection.execute('INSERT INTO account_tokens VALUES (?,?,?,?,?,?,?,?)', astuple(record))

    def consume_account_token(self, token_digest: str, *, now: str, credential_version: int) -> bool:
        cursor = self.connection.execute('UPDATE account_tokens SET consumed_at=? WHERE token_digest=? AND credential_version=? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>?', (now,token_digest,credential_version,now))
        return cursor.rowcount == 1

    def revoke_account_tokens(self, user_id: str, *, now: str) -> None:
        self.connection.execute('UPDATE account_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL', (now,user_id))

    def get_session(self, token_digest: str) -> SessionRecord | None:
        row = self.connection.execute('SELECT * FROM sessions WHERE token_digest=?', (token_digest,)).fetchone()
        return SessionRecord(**dict(row)) if row else None

    def insert_session(self, record: SessionRecord) -> None:
        self.connection.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)', astuple(record))

    def revoke_session(self, token_digest: str, *, now: str) -> None:
        self.connection.execute('UPDATE sessions SET revoked_at=? WHERE token_digest=? AND revoked_at IS NULL', (now,token_digest))

    def revoke_sessions(self, user_id: str, *, now: str) -> None:
        self.connection.execute('UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL', (now,user_id))
