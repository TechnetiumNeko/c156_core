"""Cookie identities and constant-time request proof checks."""
from dataclasses import dataclass
from http.cookies import SimpleCookie, CookieError
import re
import secrets
from ..core.errors import Unauthenticated, Forbidden

@dataclass(frozen=True)
class RequestIdentity:
    session_token: str | None
    source: str


def parse_identity(request):
    source = request.client.host if request.client else 'unknown'
    raw = '; '.join(request.headers.getlist('cookie'))
    if not re.search(r'(?:^|;)\s*c156_session(?:\s*=|\s*(?:;|$))', raw):
        return RequestIdentity(None, source)
    if len(re.findall(r'(?:^|;)\s*c156_session\s*=', raw)) != 1:
        raise Unauthenticated()
    try:
        cookies = SimpleCookie()
        cookies.load(raw)
        token = cookies['c156_session'].value
    except (CookieError, KeyError):
        raise Unauthenticated() from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise Unauthenticated()
    return RequestIdentity(token, source)


def _proof(request, name, expected):
    values = request.headers.getlist(name)
    if len(values) != 1 or not secrets.compare_digest(values[0].encode(), expected.encode()):
        raise Forbidden()


def require_login_nonce(request, services):
    identity = parse_identity(request)
    if identity.session_token is not None:
        services.identity.current_session(session_token=identity.session_token)
    _proof(request, 'x-c156-nonce', services.nonce)


def require_session_csrf(request, services, identity):
    session = services.identity.current_session(session_token=identity.session_token)
    _proof(request, 'x-c156-csrf', session.csrf_token)


def set_session_cookie(response, grant, *, secure):
    from datetime import datetime, timezone
    age = max(0, min(86400, int((datetime.fromisoformat(grant.expires_at) - datetime.now(timezone.utc)).total_seconds())))
    response.set_cookie('c156_session', grant.session_token, max_age=age, httponly=True, samesite='strict', path='/', secure=secure)
