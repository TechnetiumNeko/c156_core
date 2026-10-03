"""Trusted HTTP identity and cookie boundary values."""
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie, CookieError
import re

from ..core.errors import Unauthenticated
from ..identity.models import SessionGrant

@dataclass(frozen=True)
class RequestIdentity:
    session_token: str | None
    source: str

@dataclass(frozen=True)
class APIResponse:
    status: int
    body: dict
    session_grant: SessionGrant | None = None
    clear_cookie: bool = False
    retry_after: int | None = None


def request_identity(headers, source):
    values = headers.get_all('Cookie', [])
    if not values:
        return RequestIdentity(None, source)
    # Duplicate session cookies and malformed cookie syntax must never become guest.
    raw = '; '.join(values)
    if len(re.findall(r'(?:^|;)\s*c156_session\s*=', raw)) != 1:
        if 'c156_session' in raw:
            raise Unauthenticated('authentication required')
        return RequestIdentity(None, source)
    try:
        cookies = SimpleCookie()
        cookies.load(raw)
        token = cookies['c156_session'].value
    except (CookieError, KeyError):
        raise Unauthenticated('authentication required') from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise Unauthenticated('authentication required')
    return RequestIdentity(token, source)


def session_cookie(grant=None, *, clear=False, secure=False):
    cookie = SimpleCookie()
    cookie['c156_session'] = '' if clear else grant.session_token
    value = cookie['c156_session']
    value['path'] = '/'
    value['httponly'] = True
    value['samesite'] = 'Strict'
    value['max-age'] = 0 if clear else max(0, min(86400, int((datetime.fromisoformat(grant.expires_at) - datetime.now(timezone.utc)).total_seconds())))
    if secure:
        value['secure'] = True
    return value.OutputString()
