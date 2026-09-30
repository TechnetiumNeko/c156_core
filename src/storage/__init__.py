"""SQLite storage boundary: protocol, schema, connections and transactions.

This package depends only on the standard library and the pure ``src.core``
models.  It never imports services, CLI or editor modules.
"""

from __future__ import annotations

from .database import Database
from .errors import BusyError, ConstraintError, SchemaError, StorageError
from .schema import SCHEMA_VERSION, create_schema

__all__ = [
    "Database",
    "BusyError",
    "ConstraintError",
    "SchemaError",
    "StorageError",
    "SCHEMA_VERSION",
    "create_schema",
]
