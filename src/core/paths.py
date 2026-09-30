"""Virtual POSIX path tokenization and name rules.

This module performs lexical work only: it recognises the root prefixes,
splits segments and records a trailing slash.  It never folds ``.`` or ``..``
and never touches the host filesystem; the service resolves each segment.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidArgument, InvalidName

__all__ = ["ParsedPath", "parse_path", "validate_name"]


@dataclass(frozen=True)
class ParsedPath:
    """Lexical decomposition of a virtual path.

    ``absolute`` is true for paths anchored at the access root (a leading
    ``/`` or the ``~`` / ``~/`` prefix) and therefore independent of the
    current working directory.  ``parts`` keeps ``.`` and ``..`` intact so the
    service can process them segment by segment.
    """

    absolute: bool
    parts: tuple[str, ...]
    trailing_slash: bool


def validate_name(name: str) -> None:
    """Validate a single virtual file or folder name.

    Chinese characters and spaces are allowed.  Empty names, ``.``, ``..``,
    path separators and NUL are rejected with :class:`InvalidName`.
    """

    if not isinstance(name, str):
        raise InvalidName(
            "name must be a string",
            details={"name_type": type(name).__name__},
        )
    if name == "":
        raise InvalidName("name must not be empty")
    if name in (".", ".."):
        raise InvalidName("name must not be . or ..", details={"name": name})
    if "/" in name or "\\" in name or "\x00" in name:
        raise InvalidName(
            "name must not contain a path separator or NUL",
            details={"name": name},
        )


def parse_path(value: str) -> ParsedPath:
    """Tokenize a virtual path without resolving it.

    Consecutive ``/`` collapse into one separator, ``.`` and ``..`` are kept,
    and only a leading ``~`` or ``~/`` is treated as the access-root prefix
    (``~name`` is an ordinary relative name).  An empty path is invalid.
    """

    if not isinstance(value, str):
        raise InvalidArgument(
            "path must be a string",
            details={"value_type": type(value).__name__},
        )
    if value == "":
        raise InvalidArgument("path must not be empty")

    if value == "~" or value.startswith("~/"):
        absolute = True
        body = value[1:]
    elif value.startswith("~"):
        absolute = False
        body = value
    elif value.startswith("/"):
        absolute = True
        body = value
    else:
        absolute = False
        body = value

    trailing_slash = value.endswith("/")
    body = body.lstrip("/")
    parts = tuple(segment for segment in body.split("/") if segment != "")
    return ParsedPath(absolute=absolute, parts=parts, trailing_slash=trailing_slash)
