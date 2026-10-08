"""Schema creation uses the same Alembic revision chain as explicit upgrades."""
from .migrations import LEGACY_WRITER_BLOCKER, create_schema, validate_schema

# Compatibility export only, not a schema version: Alembic owns version truth.
SCHEMA_VERSION = LEGACY_WRITER_BLOCKER

__all__ = ["SCHEMA_VERSION", "create_schema", "validate_schema"]
