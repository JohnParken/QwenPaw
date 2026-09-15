@echo off
REM ============================================================================
REM QwenPaw 联调环境 - [方案 B 实战联调启动文件] (Windows CMD / PowerShell)
REM 启动 Native Console 前端测试台 (5179) 并开展方案 B 联调
REM ============================================================================
node "%~dp0scripts\manage-live.mjs" start-console
