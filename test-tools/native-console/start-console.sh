#!/usr/bin/env bash
# ==============================================================================
# QwenPaw 联调环境 - [方案 B 实战联调启动文件] (macOS / Linux)
# 启动 Native Console 前端测试台 (5179) 并开展方案 B 联调
# ==============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
node "${SCRIPT_DIR}/scripts/manage-live.mjs" start-console
