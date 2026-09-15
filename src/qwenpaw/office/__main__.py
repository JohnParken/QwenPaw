# -*- coding: utf-8 -*-
"""Run the independent QwenPaw Office API."""

from __future__ import annotations

import uvicorn

from .config import OfficeSettings


def main() -> None:
    settings = OfficeSettings.from_env()
    uvicorn.run(
        "qwenpaw.office.api:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
