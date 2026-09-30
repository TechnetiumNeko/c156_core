"""Deprecated explicit-init forwarding entry; old trees need migrate-legacy."""
from __future__ import annotations

import argparse
import sys

from ..storage.__main__ import main as management_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deprecated: use python -m src.storage init; old data needs migrate-legacy.")
    parser.add_argument("--database", required=True, help="explicit target database path")
    args = parser.parse_args(argv)
    print("Deprecated: use python -m src.storage init --database PATH; "
          "use migrate-legacy for old data.", file=sys.stderr)
    return management_main(["init", "--database", args.database])


if __name__ == "__main__":
    raise SystemExit(main())
