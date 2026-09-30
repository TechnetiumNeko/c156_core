"""Thin management command line for the content database.

Only the two explicit management actions live here:

``python -m src.storage init --database PATH``
    Create (or validate and reconfigure) the default content database.

``python -m src.storage migrate-legacy --source DIR --database PATH``
    Atomically import a legacy container tree into a new content database.

The parser stays a thin adapter: all business rules live in
:mod:`src.storage.management` and :mod:`src.storage.legacy`.
"""

from __future__ import annotations

import argparse
import sys
import shlex
from pathlib import Path

from ..core.errors import ContentError
from .errors import StorageError
from .legacy import migrate_legacy
from .management import initialize_database

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.storage",
        description="Initialize or migrate the unified content database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="create or validate the default content database"
    )
    init_parser.add_argument("--database", required=True, help="target database path")

    migrate_parser = subparsers.add_parser(
        "migrate-legacy", help="import a legacy container tree"
    )
    migrate_parser.add_argument("--source", required=True, help="legacy source tree")
    migrate_parser.add_argument(
        "--database", required=True, help="target database path"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one management action and return the process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize_database(Path(args.database))
        else:
            migrate_legacy(Path(args.source), Path(args.database))
    except (ContentError, StorageError, OSError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        print(f"error: {message}", file=sys.stderr)
        rerun_command = getattr(exc, "details", {}).get("rerun_command")
        if rerun_command:
            print("rerun: " + shlex.join(rerun_command), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
