@echo off
REM ============================================================================
REM QwenPaw 联调环境 - [全流程一键启动文件] (Windows CMD / PowerShell)
REM 依次启动: tl-proxy (8089) -> QwenPaw 后端 (8088) -> Native Console (5179)
REM ============================================================================
node "%~dp0scripts\manage-live.mjs" start-all
