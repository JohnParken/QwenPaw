#!/usr/bin/env bash
# ==============================================================================
# QwenPaw 联调环境 - [服务层启动文件] (macOS / Linux)
# 启动 tl-proxy (8089) 和 QwenPaw Python 后端 (8088)
# ==============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
node "${SCRIPT_DIR}/scripts/manage-live.mjs" start-services
