"""Verified SQLite online snapshots, published without replacing existing files."""
from pathlib import Path
from contextlib import closing
import os
import sqlite3
import tempfile

from .migrations import validate_backup_schema
from .publication import publish_no_replace


def backup_database(source: Path, target: Path) -> Path:
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError('backup target must differ from source')
    if target.exists():
        raise FileExistsError(target)
    connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.snapshot-', suffix='.sqlite', dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        with closing(sqlite3.connect(temporary)) as snapshot:
            connection.backup(snapshot)
            validate_backup_schema(snapshot)
            snapshot.execute('PRAGMA journal_mode=DELETE')
        publish_no_replace(temporary, target)
        return target
    finally:
        connection.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
