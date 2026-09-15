#!/usr/bin/env bash
# ==============================================================================
# QwenPaw 联调环境 - [全流程一键启动文件] (macOS / Linux)
# 依次启动: tl-proxy (8089) -> QwenPaw 后端 (8088) -> Native Console (5179)
# ==============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
node "${SCRIPT_DIR}/scripts/manage-live.mjs" start-all
