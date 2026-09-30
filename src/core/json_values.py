"""Strict JSON helpers for metadata and canonical tokens.

The kernel does not depend on SQLite's JSON extension.  Values are validated
explicitly against the JSON data model, deep-frozen into read-only structures
for snapshots, and thawed into independent plain copies for callers.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .errors import InvalidArgument

__all__ = [
    "FrozenDict",
    "RESERVED_METADATA_KEYS",
    "freeze_json",
    "thaw_json",
    "json_equal",
    "validate_metadata",
]


class FrozenDict(Mapping):
    """A deep read-only mapping that stays hashable and copyable.

    ``MappingProxyType`` is read-only but not hashable or deepcopy-able, which
    would make frozen snapshots unusable as values, dict keys or ``asdict``
    inputs.  ``FrozenDict`` keeps the same read-only contract while remaining
    safe to hash and copy.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        self._data = dict(data) if data is not None else {}
        if kwargs:
            self._data.update(kwargs)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("FrozenDict is read-only")

    def __delitem__(self, key: str) -> None:
        raise TypeError("FrozenDict is read-only")

    def __hash__(self) -> int:
        return hash(frozenset(self._data.items()))

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Mapping):
            return self._data == dict(other)
        return NotImplemented

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FrozenDict({self._data!r})"

    def __copy__(self) -> "FrozenDict":
        return self

    def __deepcopy__(self, memo: Any) -> "FrozenDict":
        return self

    def __reduce__(self):
        return (FrozenDict, (self._data,))

#: Structural fields that ``set_metadata`` must never accept as extensions.
RESERVED_METADATA_KEYS = frozenset(
    {
        "id",
        "kind",
        "parent_id",
        "name",
        "position",
        "version",
        "revision_id",
        "created_at",
        "modified_at",
        "deleted_at",
        "workspace_id",
        "branch_id",
        "object_id",
        "current_revision_id",
    }
)


def freeze_json(value: Any) -> Any:
    """Return a deep, read-only view of a JSON value.

    JSON objects become read-only mappings and JSON arrays become tuples.
    Scalars are returned unchanged.
    """

    if isinstance(value, Mapping):
        return FrozenDict({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    return value


def thaw_json(value: Any) -> Any:
    """Return an independent plain-Python copy of a frozen JSON value."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(item) for item in value]
    return value


def json_equal(left: Any, right: Any) -> bool:
    """Compare two JSON values by strict JSON type and value.

    Object key order does not matter and JSON numbers compare numerically, but
    a boolean is never equal to a number (``json_equal(True, 1)`` is False).
    """

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right

    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right

    if left is None or right is None:
        return left is None and right is None

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if len(left) != len(right):
            return False
        for key, value in left.items():
            if key not in right or not json_equal(value, right[key]):
                return False
        return True

    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False
        return all(json_equal(a, b) for a, b in zip(left, right))

    return False


def validate_metadata(value: Mapping[str, Any], *, reject_reserved: bool = True) -> None:
    """Validate extension metadata against the strict JSON data model.

    Raises :class:`InvalidArgument` for a non-mapping top level, non-string
    keys, non-finite numbers, cyclic or non-JSON values, or reserved top-level keys when
    ``reject_reserved`` is true.
    """

    if not isinstance(value, Mapping):
        raise InvalidArgument(
            "metadata must be a JSON object",
            details={"value_type": type(value).__name__},
        )
    try:
        _validate_object(value, reject_reserved=reject_reserved, top=True,
                         active={id(value)})
    except RecursionError as exc:
        raise InvalidArgument("metadata exceeds JSON nesting limits") from exc


def _validate_object(
    value: Mapping[Any, Any], *, reject_reserved: bool, top: bool, active: set[int]
) -> None:
    for key, item in value.items():
        if not isinstance(key, str):
            raise InvalidArgument(
                "metadata keys must be strings",
                details={"key_type": type(key).__name__},
            )
        if top and reject_reserved and key in RESERVED_METADATA_KEYS:
            raise InvalidArgument(
                "metadata key is reserved",
                details={"key": key},
            )
        _validate_json_value(item, active)


def _validate_json_value(value: Any, active: set[int]) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidArgument(
                "metadata numbers must be finite",
                details={"value": repr(value)},
            )
        return
    if isinstance(value, (Mapping, list)):
        identity = id(value)
        if identity in active:
            raise InvalidArgument("metadata must not contain cyclic references")
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                _validate_object(value, reject_reserved=False, top=False, active=active)
            else:
                for item in value:
                    _validate_json_value(item, active)
        finally:
            active.remove(identity)
        return
    raise InvalidArgument(
        "metadata value is not valid JSON",
        details={"value_type": type(value).__name__},
    )
