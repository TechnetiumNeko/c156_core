"""Read-only inspection of the legacy single-object SQLite containers.

The legacy protocol stores one SQLite container per object: a ``.folder`` file
inside every directory and one container file per document/executable/resource.
This module opens those containers strictly read-only (``mode=ro`` plus
``PRAGMA query_only``), validates the old protocol without ever calling the old
``src.file`` wrappers (whose constructors create tables), and returns immutable
objects plus a strict-JSON migration report.

No function here writes to the source tree, imports :mod:`src.file`, or touches
the new content database.  The only side effects are reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..core.errors import InvalidArgument, InvalidName, MigrationError
from ..core.json_values import RESERVED_METADATA_KEYS, validate_metadata
from ..core.paths import validate_name
from .management import DEFAULT_TOP_LEVEL_NAMES

__all__ = ["LegacyObject", "LegacyScan", "scan_legacy", "source_fingerprint"]

#: Virtual fullpath of the legacy root directory (the physical ``source``).
ROOT_FULLPATH = "/data"

_TIME_KEYS = ("created_at", "modified_at")
#: Object kinds this stage can faithfully migrate.  ``executable`` and
#: ``resource`` have no defined legacy payload here, so they abort rather than
#: risk an incomplete import.
_SUPPORTED_KINDS = frozenset({"folder", "document"})
_BASE_TABLES = frozenset({"abstract_file", "metadata", "file"})
_DOCUMENT_TABLES = _BASE_TABLES | {"document_content"}
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_TEMP_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")
_REQUIRED_COLUMNS = {
    "abstract_file": frozenset({"singleton", "id", "fullpath", "kind", "parent_id"}),
    "metadata": frozenset({"key", "value"}),
    "file": frozenset({"parent_id", "child_id", "position"}),
    "document_content": frozenset({"singleton", "content"}),
}
#: The canonical UUID embedded in Task 9's ``<target>.migrate-<uuid>.tmp`` name.
_MIGRATION_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


@dataclass(frozen=True)
class LegacyObject:
    """One validated legacy object with its raw and adopted values."""

    id: str
    kind: str
    relative_path: str
    fullpath: str
    parent_id: str | None
    name: str
    position: int
    content: str | None
    metadata: Mapping[str, Any]
    raw_metadata: Mapping[str, str]
    created_at: str
    modified_at: str


@dataclass(frozen=True)
class LegacyScan:
    """Result of one complete read-only legacy scan."""

    objects: tuple[LegacyObject, ...]
    source_digest: str
    report: dict
    imported_at: str


@dataclass
class _Container:
    """Internal, pre-tree view of one validated container."""

    path: Path
    relative_path: str
    directory: Path
    is_folder_file: bool
    id: str
    kind: str
    fullpath: str
    parent_id: str | None
    raw_metadata: dict[str, str]
    raw_values: dict[str, Any]
    metadata: dict[str, Any]
    content: str | None
    content_length: int
    content_sha256: str | None
    file_rows: list[tuple[str, str, int]]


# -- read-only SQLite helpers ------------------------------------------------


@contextmanager
def _open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    """Open *path* with a ``mode=ro`` URI and never create or modify it."""

    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise MigrationError(
            "legacy container could not be opened read-only",
            details={"path": str(path)},
        ) from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    except sqlite3.DatabaseError as exc:
        raise MigrationError(
            "legacy container is not a readable SQLite database",
            details={"path": str(path)},
        ) from exc
    finally:
        connection.close()


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _read_container(path: Path, relative_path: str) -> _Container:
    if not path.is_file():
        raise MigrationError(
            "legacy container is not a regular file",
            details={"path": str(path)},
        )

    with _open_readonly(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError(
                "legacy container failed SQLite integrity_check",
                details={"path": str(path), "result": integrity},
            )

        tables: set[str] = set()
        for row in connection.execute("SELECT type, name FROM sqlite_master"):
            object_type = row["type"]
            name = row["name"]
            if isinstance(name, str) and name.startswith("sqlite_"):
                # Internal autoindexes / sqlite_sequence are not user schema.
                continue
            if object_type == "table":
                tables.add(name)
            else:
                raise MigrationError(
                    "legacy container holds an unrecognised schema object",
                    details={"path": str(path), "type": object_type, "name": name},
                )

        missing_tables = _BASE_TABLES - tables
        if missing_tables:
            raise MigrationError(
                "legacy container is missing a required table",
                details={"path": str(path), "tables": sorted(missing_tables)},
            )
        unknown_tables = tables - _DOCUMENT_TABLES
        if unknown_tables:
            raise MigrationError(
                "legacy container holds an unknown table",
                details={"path": str(path), "tables": sorted(unknown_tables)},
            )

        for table in sorted(tables):
            columns = _table_columns(connection, table)
            expected = _REQUIRED_COLUMNS[table]
            absent = expected - columns
            if absent:
                raise MigrationError(
                    "legacy table is missing required columns",
                    details={"path": str(path), "table": table, "columns": sorted(absent)},
                )
            extra = columns - expected
            if extra:
                raise MigrationError(
                    "legacy table holds unrecognised columns",
                    details={"path": str(path), "table": table, "columns": sorted(extra)},
                )

        abstract_rows = connection.execute(
            "SELECT singleton, id, fullpath, kind, parent_id FROM abstract_file"
        ).fetchall()
        if len(abstract_rows) != 1:
            raise MigrationError(
                "legacy container must hold exactly one abstract_file row",
                details={"path": str(path), "rows": len(abstract_rows)},
            )
        abstract = abstract_rows[0]
        if abstract["singleton"] != 1:
            raise MigrationError(
                "legacy abstract_file singleton must be 1",
                details={"path": str(path), "singleton": abstract["singleton"]},
            )
        file_id = abstract["id"]
        fullpath = abstract["fullpath"]
        kind = abstract["kind"]
        parent_id = abstract["parent_id"]
        if not isinstance(file_id, str) or file_id == "":
            raise MigrationError(
                "legacy abstract_file id must be a non-empty string",
                details={"path": str(path)},
            )
        if not isinstance(fullpath, str) or fullpath == "":
            raise MigrationError(
                "legacy abstract_file fullpath must be a non-empty string",
                details={"path": str(path)},
            )
        if not isinstance(kind, str) or kind not in _SUPPORTED_KINDS:
            raise MigrationError(
                "legacy abstract_file kind is not a recognised object type",
                details={"path": str(path), "kind": kind},
            )
        if parent_id is not None and not isinstance(parent_id, str):
            raise MigrationError(
                "legacy abstract_file parent_id must be a string or NULL",
                details={"path": str(path)},
            )

        is_folder_file = path.name == ".folder"
        if (kind == "folder") != is_folder_file:
            raise MigrationError(
                "legacy container name does not match its declared kind",
                details={"path": str(path), "kind": kind},
            )

        if kind == "document" and "document_content" not in tables:
            raise MigrationError(
                "legacy document container is missing document_content",
                details={"path": str(path)},
            )
        if kind != "document" and "document_content" in tables:
            raise MigrationError(
                "legacy container holds an unexpected document_content table",
                details={"path": str(path), "kind": kind},
            )

        raw_metadata: dict[str, str] = {}
        for row in connection.execute("SELECT key, value FROM metadata"):
            key = row["key"]
            value = row["value"]
            if not isinstance(key, str):
                raise MigrationError(
                    "legacy metadata key must be a string",
                    details={"path": str(path)},
                )
            if key in raw_metadata:
                raise MigrationError(
                    "legacy metadata key is duplicated",
                    details={"path": str(path), "key": key},
                )
            if not isinstance(value, str):
                raise MigrationError(
                    "legacy metadata value must be stored as text",
                    details={"path": str(path), "key": key},
                )
            raw_metadata[key] = value

        raw_values: dict[str, Any] = {}
        for key, raw in raw_metadata.items():
            # Spec 5.1: every stored metadata value, including the legacy
            # created_at/modified_at strings, must itself be strict JSON.
            raw_values[key] = _decode_json_value(path, key, raw)

        reserved_conflicts = sorted(
            set(raw_metadata) & (RESERVED_METADATA_KEYS - set(_TIME_KEYS))
        )
        if reserved_conflicts:
            raise MigrationError(
                "legacy metadata uses a reserved structural key",
                details={"path": str(path), "keys": reserved_conflicts},
            )

        try:
            validate_metadata(raw_values, reject_reserved=False)
        except InvalidArgument as exc:
            raise MigrationError(
                "legacy metadata is not valid JSON data",
                details={"path": str(path), "reason": exc.message, **exc.details},
            ) from exc

        metadata: dict[str, Any] = {
            key: value for key, value in raw_values.items() if key not in _TIME_KEYS
        }

        file_rows: list[tuple[str, str, int]] = []
        for row in connection.execute('SELECT parent_id, child_id, position FROM "file"'):
            file_rows.append((row["parent_id"], row["child_id"], row["position"]))
        if kind != "folder" and file_rows:
            raise MigrationError(
                "legacy non-folder container must have an empty file table",
                details={"path": str(path), "kind": kind},
            )

        content: str | None = None
        content_length = 0
        content_sha256: str | None = None
        if kind == "document":
            content_rows = connection.execute(
                "SELECT singleton, content FROM document_content"
            ).fetchall()
            if len(content_rows) != 1 or content_rows[0]["singleton"] != 1:
                raise MigrationError(
                    "legacy document_content must hold exactly one singleton row",
                    details={"path": str(path), "rows": len(content_rows)},
                )
            content = content_rows[0]["content"]
            if not isinstance(content, str):
                raise MigrationError(
                    "legacy document content must be text",
                    details={"path": str(path)},
                )
            content_length = len(content)
            content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    return _Container(
        path=path,
        relative_path=relative_path,
        directory=path.parent,
        is_folder_file=is_folder_file,
        id=file_id,
        kind=kind,
        fullpath=fullpath,
        parent_id=parent_id,
        raw_metadata=raw_metadata,
        raw_values=raw_values,
        metadata=metadata,
        content=content,
        content_length=content_length,
        content_sha256=content_sha256,
        file_rows=file_rows,
    )


def _decode_json_value(path: Path, key: str, raw: str) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject_json_constant)
    except (ValueError, TypeError) as exc:
        raise MigrationError(
            "legacy metadata value is not valid JSON",
            details={"path": str(path), "key": key},
        ) from exc


def _reject_json_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {token}")


# -- source walking and fingerprint ------------------------------------------


def _is_target_migration_artifact(path: Path, target: Path) -> bool:
    """Match Task 9's exclusive ``<target>.migrate-<uuid>.tmp`` namespace.

    Only that exact base name and its SQLite sidecars (``-journal``/``-wal``/
    ``-shm``) are excluded.  A similarly prefixed name without the canonical
    UUID and ``.tmp`` base is *not* ignored, so unrelated files still change the
    fingerprint and abort the scan.
    """

    if path.parent != target.parent:
        return False
    name = path.name
    prefix = f"{target.name}.migrate-"
    if not name.startswith(prefix):
        return False
    remainder = name[len(prefix) :]
    for suffix in _TEMP_SIDECAR_SUFFIXES:
        if remainder.endswith(suffix):
            remainder = remainder[: -len(suffix)]
            break
    if not remainder.endswith(".tmp"):
        return False
    return _MIGRATION_UUID.fullmatch(remainder[: -len(".tmp")]) is not None


def _is_excluded(path: Path, target: Path) -> bool:
    """Return whether *path* is the target or one of its named artifacts."""

    if path == target:
        return True
    for suffix in _SIDECAR_SUFFIXES:
        if path == Path(str(target) + suffix):
            return True
    return _is_target_migration_artifact(path, target)


def _sidecar_suffix(path: Path) -> str | None:
    for suffix in _SIDECAR_SUFFIXES:
        if path.name.endswith(suffix):
            return suffix
    return None


def _walk_source(
    source: Path, target: Path, *, reject_sidecars: bool
) -> tuple[list[Path], list[Path]]:
    """Return ``(directories, files)`` under *source*, never following symlinks.

    The exact target, its SQLite sidecars and its ``*.migrate-*.tmp`` namespace
    are skipped.  Any other symlink aborts the scan; when *reject_sidecars* is
    true, a source WAL/journal/SHM also aborts.
    """

    directories: list[Path] = []
    files: list[Path] = []
    try:
        walker = os.walk(source)
        for dirpath, dirnames, filenames in walker:
            dirnames.sort()
            filenames.sort()
            directory = Path(dirpath)
            directories.append(directory)
            for name in dirnames:
                candidate = directory / name
                if candidate.is_symlink():
                    raise MigrationError(
                        "legacy source tree must not contain symlinks",
                        details={"path": str(candidate)},
                    )
            for name in filenames:
                candidate = directory / name
                if candidate.is_symlink():
                    raise MigrationError(
                        "legacy source tree must not contain symlinks",
                        details={"path": str(candidate)},
                    )
                if _is_excluded(candidate, target):
                    continue
                if reject_sidecars and _sidecar_suffix(candidate) is not None:
                    raise MigrationError(
                        "legacy source holds an unfinished WAL/journal/SHM sidecar",
                        details={"path": str(candidate)},
                    )
                files.append(candidate)
    except OSError as exc:
        raise MigrationError(
            "legacy source tree could not be read",
            details={"source": str(source)},
        ) from exc
    return directories, files


def source_fingerprint(source: Path, *, target: Path) -> str:
    """Return the canonical SHA-256 fingerprint of the complete source file set.

    Every regular file under *source* is re-read on each call so that later
    additions, deletions and content changes are detected.  Only the exact
    target, its sidecars and its named ``.migrate-*.tmp`` artifacts are
    excluded; unrelated files still change the digest.
    """

    source = Path(source).resolve()
    target = Path(target).resolve()
    if not source.is_dir():
        raise MigrationError(
            "legacy source must be an existing directory",
            details={"source": str(source)},
        )
    _, files = _walk_source(source, target, reject_sidecars=False)
    entries: list[list[str]] = []
    for path in files:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise MigrationError(
                "legacy source file could not be read",
                details={"path": str(path)},
            ) from exc
        entries.append([path.relative_to(source).as_posix(), digest])
    entries.sort(key=lambda item: item[0])
    canonical = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- target overlap ----------------------------------------------------------


def _looks_like_legacy_container(path: Path) -> bool:
    try:
        with _open_readonly(path) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'abstract_file'"
            ).fetchone()
        return row is not None
    except MigrationError:
        return False


def _check_target_overlap(source: Path, target: Path) -> None:
    if target.exists() and target.is_dir():
        raise MigrationError(
            "legacy target must be a file path, not a directory",
            details={"target": str(target)},
        )
    try:
        target.relative_to(source)
    except ValueError:
        return
    if target.exists() and _looks_like_legacy_container(target):
        raise MigrationError(
            "legacy target overlaps a source container",
            details={"target": str(target)},
        )


# -- time mapping ------------------------------------------------------------


def _resolve_time(container: _Container, key: str, fallback: str) -> tuple[str, str]:
    """Return ``(adopted UTC time, reason)`` for one strictly-decoded value.

    The raw value has already passed strict JSON parsing.  A missing key is a
    fallback, a valid JSON value that is not a timezone-aware ISO string is a
    fallback with an ``invalid``/``naive`` reason, and only a parseable
    timezone-aware value is adopted as a UTC ``stored`` time.
    """

    if key not in container.raw_metadata:
        return fallback, "missing"
    decoded = container.raw_values[key]
    if not isinstance(decoded, str):
        return fallback, "invalid"
    try:
        parsed = datetime.fromisoformat(decoded)
    except (TypeError, ValueError):
        return fallback, "invalid"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return fallback, "naive"
    return parsed.astimezone(timezone.utc).isoformat(), "stored"


def _normalize_imported_at(imported_at: Any) -> str:
    """Return the batch timestamp as a normalized timezone-aware UTC string."""

    if not isinstance(imported_at, str):
        raise MigrationError(
            "imported_at must be a timezone-aware ISO 8601 string",
            details={"value_type": type(imported_at).__name__},
        )
    try:
        parsed = datetime.fromisoformat(imported_at)
    except (TypeError, ValueError) as exc:
        raise MigrationError(
            "imported_at must be a timezone-aware ISO 8601 string",
            details={"imported_at": imported_at},
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MigrationError(
            "imported_at must include a UTC offset",
            details={"imported_at": imported_at},
        )
    return parsed.astimezone(timezone.utc).isoformat()


# -- scan --------------------------------------------------------------------


def scan_legacy(source: Path, *, target: Path, imported_at: str) -> LegacyScan:
    """Read-only scan, validate and report on a legacy container tree.

    The source tree is never modified.  Unknown files, tables, kinds, dangling
    or contradictory registrations, damaged containers and unfinished sidecars
    abort with :class:`MigrationError`.  Only a missing registration is repaired
    (the physical order is authoritative) and recorded in the report.
    """

    imported_at = _normalize_imported_at(imported_at)

    source = Path(source).resolve()
    target = Path(target).resolve()
    if not source.is_dir():
        raise MigrationError(
            "legacy source must be an existing directory",
            details={"source": str(source)},
        )
    _check_target_overlap(source, target)

    source_digest = source_fingerprint(source, target=target)
    directories, files = _walk_source(source, target, reject_sidecars=True)

    folder_by_directory: dict[Path, _Container] = {}
    for directory in directories:
        folder_path = directory / ".folder"
        if not folder_path.is_file():
            raise MigrationError(
                "legacy directory is missing its .folder container",
                details={"directory": str(directory)},
            )

    containers = [_read_container(path, path.relative_to(source).as_posix()) for path in files]
    by_id: dict[str, _Container] = {}
    for container in containers:
        if container.id in by_id:
            raise MigrationError(
                "legacy object id is duplicated across containers",
                details={"id": container.id, "path": str(container.path)},
            )
        by_id[container.id] = container
        if container.is_folder_file:
            folder_by_directory[container.directory] = container

    if set(folder_by_directory) != set(directories):
        missing = sorted(str(path) for path in set(directories) - set(folder_by_directory))
        raise MigrationError(
            "legacy directory is missing its .folder container",
            details={"directories": missing},
        )

    root_container = folder_by_directory.get(source)
    if root_container is None:
        raise MigrationError("legacy source root has no .folder container")

    derived: dict[Path, dict[str, Any]] = {}
    ordered_directories = sorted(
        directories,
        key=lambda path: (len(path.relative_to(source).parts), path.as_posix()),
    )
    for directory in ordered_directories:
        folder = folder_by_directory[directory]
        if directory == source:
            name = ""
            parent_id: str | None = None
            fullpath = ROOT_FULLPATH
        else:
            parent = folder_by_directory.get(directory.parent)
            if parent is None:
                raise MigrationError(
                    "legacy directory has no physical parent container",
                    details={"directory": str(directory)},
                )
            name = directory.name
            parent_id = parent.id
            fullpath = f"{derived[parent.path]['fullpath']}/{name}"
        derived[folder.path] = {
            "name": name,
            "parent_id": parent_id,
            "fullpath": fullpath,
        }

    for container in containers:
        if container.is_folder_file:
            continue
        parent = folder_by_directory.get(container.directory)
        if parent is None:
            raise MigrationError(
                "legacy object has no physical parent folder",
                details={"path": str(container.path)},
            )
        derived[container.path] = {
            "name": container.path.name,
            "parent_id": parent.id,
            "fullpath": f"{derived[parent.path]['fullpath']}/{container.path.name}",
        }

    for container in containers:
        info = derived[container.path]
        if container.fullpath != info["fullpath"]:
            raise MigrationError(
                "legacy fullpath contradicts the physical location",
                details={
                    "path": str(container.path),
                    "stored": container.fullpath,
                    "expected": info["fullpath"],
                },
            )
        if container.parent_id != info["parent_id"]:
            raise MigrationError(
                "legacy parent_id contradicts the physical location",
                details={
                    "path": str(container.path),
                    "stored": container.parent_id,
                    "expected": info["parent_id"],
                },
            )
        name = info["name"]
        if name != "":
            try:
                validate_name(name)
            except InvalidName as exc:
                raise MigrationError(
                    "legacy object name is invalid",
                    details={"path": str(container.path), "name": name, **exc.details},
                ) from exc

    root_name = derived[root_container.path]["name"]
    if root_container.kind != "folder" or root_name != "" or root_container.fullpath != ROOT_FULLPATH:
        raise MigrationError(
            "legacy root container is not a valid /data folder",
            details={"path": str(root_container.path)},
        )

    top_level_directories = {
        directory.name for directory in directories if directory.parent == source and directory != source
    }
    missing_defaults = sorted(set(DEFAULT_TOP_LEVEL_NAMES) - top_level_directories)
    if missing_defaults:
        raise MigrationError(
            "legacy root is missing mandatory default folders",
            details={"names": missing_defaults},
        )

    physical_children: dict[str, list[_Container]] = {container.id: [] for container in containers}
    for container in containers:
        parent_id = derived[container.path]["parent_id"]
        if parent_id is None:
            continue
        physical_children[parent_id].append(container)

    repairs: list[dict] = []
    ordered_children: dict[str, list[_Container]] = {}
    registration_parent: dict[str, str] = {}
    positions: dict[str, int] = {}
    for container in containers:
        if container.kind != "folder":
            continue
        registered: list[tuple[int, _Container]] = []
        seen_children: set[str] = set()
        seen_positions: set[int] = set()
        for parent_id, child_id, position in container.file_rows:
            if not isinstance(parent_id, str) or not isinstance(child_id, str):
                raise MigrationError(
                    "legacy file registration ids must be strings",
                    details={"path": str(container.path)},
                )
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                raise MigrationError(
                    "legacy file registration position must be a non-negative integer",
                    details={"path": str(container.path)},
                )
            if parent_id != container.id:
                raise MigrationError(
                    "legacy file registration names the wrong parent",
                    details={"path": str(container.path), "parent_id": parent_id},
                )
            child = by_id.get(child_id)
            if child is None:
                raise MigrationError(
                    "legacy file registration dangles to a missing object",
                    details={"path": str(container.path), "child_id": child_id},
                )
            if derived[child.path]["parent_id"] != container.id:
                raise MigrationError(
                    "legacy object is registered under the wrong parent",
                    details={"path": str(container.path), "child_id": child_id},
                )
            if child_id in seen_children:
                raise MigrationError(
                    "legacy file registration repeats a child",
                    details={"path": str(container.path), "child_id": child_id},
                )
            if position in seen_positions:
                raise MigrationError(
                    "legacy file registration repeats a position",
                    details={"path": str(container.path), "position": position},
                )
            previous_parent = registration_parent.get(child_id)
            if previous_parent is not None and previous_parent != container.id:
                raise MigrationError(
                    "legacy object is registered under two parents",
                    details={"child_id": child_id},
                )
            registration_parent[child_id] = container.id
            seen_children.add(child_id)
            seen_positions.add(position)
            registered.append((position, child))

        registered.sort(key=lambda item: item[0])
        registered_ids = {child.id for _, child in registered}
        missing = [
            child
            for child in physical_children.get(container.id, [])
            if child.id not in registered_ids
        ]
        missing.sort(key=lambda child: derived[child.path]["name"])
        ordered = [child for _, child in registered] + missing
        if {child.id for child in ordered} != {
            child.id for child in physical_children.get(container.id, [])
        }:
            raise MigrationError(
                "legacy folder registration does not cover its physical children",
                details={"path": str(container.path)},
            )
        ordered_children[container.id] = ordered
        for index, child in enumerate(ordered):
            positions[child.id] = index
        for child in missing:
            repairs.append(
                {
                    "kind": "missing_registration",
                    "parent_id": container.id,
                    "child_id": child.id,
                    "name": derived[child.path]["name"],
                    "position": positions[child.id],
                }
            )

    positions[root_container.id] = 0
    time_fallbacks: list[dict] = []
    report_objects: list[dict] = []
    objects: list[LegacyObject] = []
    visited: set[str] = set()

    def visit(folder_id: str) -> None:
        if folder_id in visited:
            raise MigrationError(
                "legacy tree contains a cycle or duplicate parent",
                details={"id": folder_id},
            )
        visited.add(folder_id)
        container = by_id[folder_id]
        info = derived[container.path]
        created_at, created_reason = _resolve_time(container, "created_at", imported_at)
        modified_at, modified_reason = _resolve_time(
            container, "modified_at", created_at
        )
        position = positions[container.id]
        objects.append(
            LegacyObject(
                id=container.id,
                kind=container.kind,
                relative_path=container.relative_path,
                fullpath=container.fullpath,
                parent_id=info["parent_id"],
                name=info["name"],
                position=position,
                content=container.content,
                metadata=dict(container.metadata),
                raw_metadata=dict(container.raw_metadata),
                created_at=created_at,
                modified_at=modified_at,
            )
        )
        report_objects.append(
            {
                "id": container.id,
                "kind": container.kind,
                "relative_path": container.relative_path,
                "fullpath": container.fullpath,
                "parent_id": info["parent_id"],
                "name": info["name"],
                "position": position,
                "content_length": container.content_length,
                "content_sha256": container.content_sha256,
                "raw_metadata": dict(container.raw_metadata),
                "metadata": dict(container.metadata),
                "raw_created_at": container.raw_metadata.get("created_at"),
                "raw_modified_at": container.raw_metadata.get("modified_at"),
                "created_at": created_at,
                "created_at_reason": created_reason,
                "modified_at": modified_at,
                "modified_at_reason": modified_reason,
            }
        )
        if created_reason != "stored":
            time_fallbacks.append(
                {
                    "object_id": container.id,
                    "field": "created_at",
                    "reason": created_reason,
                    "raw": container.raw_metadata.get("created_at"),
                    "adopted": created_at,
                }
            )
        if modified_reason != "stored":
            time_fallbacks.append(
                {
                    "object_id": container.id,
                    "field": "modified_at",
                    "reason": modified_reason,
                    "raw": container.raw_metadata.get("modified_at"),
                    "adopted": modified_at,
                }
            )
        for child in ordered_children.get(folder_id, ()):
            visit(child.id)

    visit(root_container.id)
    if len(objects) != len(containers):
        raise MigrationError(
            "legacy tree is not fully reachable from the root",
            details={"objects": len(objects), "containers": len(containers)},
        )

    counts = {
        "objects": len(objects),
        "folders": sum(1 for item in objects if item.kind == "folder"),
        "documents": sum(1 for item in objects if item.kind == "document"),
        "repairs": len(repairs),
        "time_fallbacks": len(time_fallbacks),
    }
    report = {
        "imported_at": imported_at,
        "source_digest": source_digest,
        "root": {
            "id": root_container.id,
            "relative_path": root_container.relative_path,
            "fullpath": ROOT_FULLPATH,
        },
        "counts": counts,
        "repairs": repairs,
        "time_fallbacks": time_fallbacks,
        "objects": report_objects,
    }
    try:
        json.dumps(report, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise MigrationError("legacy report is not strict JSON") from exc

    return LegacyScan(
        objects=tuple(objects),
        source_digest=source_digest,
        report=report,
        imported_at=imported_at,
    )
