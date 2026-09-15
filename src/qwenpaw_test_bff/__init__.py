"""Development-only trusted BFF/File API facade.

Production deployments must supply their own identity and object storage BFF.
This package intentionally reuses the exact File API implementation/schema.
"""
from pathlib import Path

from qwenpaw_cloud.files import create_app as _create_file_app


def create_app(dsn: str, root: str | Path, signing_key: str):
    return _create_file_app(dsn, Path(root), signing_key)


__all__ = ["create_app"]
