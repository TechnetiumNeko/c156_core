"""Pure content kernel: models, domain errors, path rules and JSON helpers.

This package must not import storage, services, CLI, editor or any database
module, and importing it never opens a database.
"""

from __future__ import annotations

from .errors import (
    AlreadyExists,
    Conflict,
    ContentError,
    DirectoryNotEmpty,
    InvalidArgument,
    InvalidMove,
    InvalidName,
    MigrationError,
    NotDirectory,
    NotDocument,
    NotFound,
    PathOutsideRoot,
    ProtectedNode,
    StorageBusy,
    UnsupportedSchema,
)
from .json_values import (
    RESERVED_METADATA_KEYS,
    FrozenDict,
    freeze_json,
    json_equal,
    thaw_json,
    validate_metadata,
)
from .models import (
    ContentScope,
    DeleteSnapshot,
    DocumentSnapshot,
    NodeSnapshot,
    TreeItem,
)
from .paths import ParsedPath, parse_path, validate_name

__all__ = [
    # models
    "ContentScope",
    "NodeSnapshot",
    "DocumentSnapshot",
    "TreeItem",
    "DeleteSnapshot",
    # errors
    "ContentError",
    "NotFound",
    "AlreadyExists",
    "NotDirectory",
    "NotDocument",
    "InvalidName",
    "InvalidArgument",
    "PathOutsideRoot",
    "Conflict",
    "DirectoryNotEmpty",
    "ProtectedNode",
    "InvalidMove",
    "StorageBusy",
    "UnsupportedSchema",
    "MigrationError",
    # paths
    "ParsedPath",
    "parse_path",
    "validate_name",
    # json values
    "FrozenDict",
    "RESERVED_METADATA_KEYS",
    "validate_metadata",
    "freeze_json",
    "thaw_json",
    "json_equal",
]
