"""Return bounded shape metadata for a JSON array of row objects."""

from __future__ import annotations

import argparse
import json
from typing import Any


def profile(payload: str | None) -> dict[str, Any]:
    """Profile JSON rows without opening paths or importing data engines."""
    if not payload:
        rows: Any = []
    else:
        rows = json.loads(payload)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("payload must be a JSON array of objects")
    columns = sorted({str(key) for row in rows for key in row})
    return {"rows": len(rows), "columns": columns, "column_count": len(columns)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile JSON rows")
    parser.add_argument("payload", nargs="?", help="JSON array of row objects")
    args = parser.parse_args()
    print(json.dumps(profile(args.payload), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
