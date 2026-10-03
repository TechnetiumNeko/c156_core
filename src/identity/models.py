"""Immutable public identity values; credential material stays in storage."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Principal:
    user_id: str | None
    site_admin: bool

@dataclass(frozen=True)
class UserView:
    id: str
    login_name: str
    display_name: str
    status: str
    site_admin: bool
    version: int

@dataclass(frozen=True)
class SessionView:
    user: UserView
    csrf_token: str
    expires_at: str

@dataclass(frozen=True)
class SessionGrant:
    user: UserView
    session_token: str
    csrf_token: str
    expires_at: str

@dataclass(frozen=True)
class AccountTokenGrant:
    user: UserView
    token: str
    purpose: str
    expires_at: str


def user_view(record) -> UserView:
    return UserView(record.id, record.login_name, record.display_name, record.status,
                    record.site_admin, record.version)
