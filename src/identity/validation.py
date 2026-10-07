"""Identity input contracts, without Unicode password transformations."""
import re
from ..core.errors import InvalidArgument


def normalize_login_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{2,31}', value):
        raise InvalidArgument('login name must be 3–32 ASCII letters, digits, underscores or hyphens, starting with a letter')
    return value.lower()


def validate_display_name(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise InvalidArgument('display name must contain 1–80 characters')
    return value


def validate_password(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise InvalidArgument('password must contain 8–128 characters')
    return value
