@echo off
REM ============================================================================
REM QwenPaw 联调环境 - [服务层启动文件] (Windows CMD / PowerShell)
REM 启动 tl-proxy (8089) 和 QwenPaw Python 后端 (8088)
REM ============================================================================
node "%~dp0scripts\manage-live.mjs" start-services
