"""Normalize supplied text for the request-scoped writing skill."""

from __future__ import annotations

import argparse


def normalize(parts: list[str]) -> str:
    """Collapse whitespace without reading files or invoking a shell."""
    return " ".join(" ".join(parts).split())


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize supplied text")
    parser.add_argument("text", nargs="*", help="text fragments")
    args = parser.parse_args()
    print(normalize(args.text))


if __name__ == "__main__":
    main()
