"""Command-line interface: argparse parser, composition root, exit codes.

T-0.1 ships ``--version`` only; the subcommands are added in T-6.4.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from freshcal import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freshcal",
        description="Business-calendar-aware data freshness checks.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"freshcal {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code.

    ``argparse`` reports ``--version``, ``--help``, and usage errors by raising
    ``SystemExit``; catching it here keeps ``main`` a normal function that tests
    can call directly, as the CLI specification requires.
    """
    parser = build_parser()
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
