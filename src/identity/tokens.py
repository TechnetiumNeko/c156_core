"""Opaque 256-bit bearer credentials; only digests are persisted."""
import hashlib
import secrets
from ..core.errors import Unauthenticated

SESSION_HOURS = 24
ACTIVATION_HOURS = 48
RESET_HOURS = 1


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise Unauthenticated('authentication failed')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()
