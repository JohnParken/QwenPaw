#!/usr/bin/env python3
"""Compatibility entry point for the current /v1 multi-user test console."""

import importlib.util
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "server-console" / "server.py"
spec = importlib.util.spec_from_file_location("qwenpaw_v1_test_console", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
create_server = module.create_server
main = module.main

if __name__ == "__main__":
    main()
