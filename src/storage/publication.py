"""Atomic, no-replace publication of a fully built temporary database.

A migration builds the complete target in a sibling temporary file, commits and
closes it, flushes it to stable storage, and only then links it to the final
name.  The temporary and the target live in the same directory so the hard link
stays on one filesystem.  An existing target is never replaced: there is no
fallback to a covering ``rename``.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["publish_no_replace"]


def _fsync_file(path: Path) -> None:
    """Flush an already closed file; failures prevent publication."""

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    """Flush directory changes, surfacing durability failures to the caller."""

    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_no_replace(temporary: Path, target: Path) -> None:
    """Publish *temporary* as *target* without ever overwriting *target*.

    The temporary file is fsynced, then hard-linked to the target name.  A
    pre-existing target makes :func:`os.link` raise :class:`FileExistsError`;
    that error is propagated unchanged and no covering rename is attempted.
    After a successful link the temporary name is removed and the parent
    directory is fsynced.
    """

    temporary = Path(temporary)
    target = Path(target)
    _fsync_file(temporary)
    os.link(temporary, target)
    try:
        temporary.unlink()
    except FileNotFoundError:  # pragma: no cover - defensive
        pass
    _fsync_directory(target.parent)
