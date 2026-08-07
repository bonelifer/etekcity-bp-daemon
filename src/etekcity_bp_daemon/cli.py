#!/usr/bin/env python3
"""Entry point for the etekcity-bp-daemon service (feature set not yet built)."""

from __future__ import annotations

import argparse

from ._version import __version__


def main(argv: list[str] | None = None) -> None:
    """Entry point for the etekcity-bp-daemon console script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.parse_args(argv)
    raise SystemExit("etekcity-bp-daemon has no functionality yet; see the repo README")


if __name__ == "__main__":
    main()
