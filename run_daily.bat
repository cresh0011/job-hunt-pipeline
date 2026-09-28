@echo off
REM ============================================================
REM  Job-hunt pipeline - Windows Task Scheduler entry point
REM
REM  Keep this file ASCII-only and CRLF-terminated.
REM  cmd.exe reads .bat using the OEM codepage (GBK on zh-CN
REM  Windows), so UTF-8 Chinese comments get misparsed and the
REM  whole script fails silently with no error message.
REM
REM  Manual run: double-click this file, or run `run_daily.bat`
REM ============================================================

REM %~dp0 = the directory this script lives in, so the project
REM can be cloned anywhere (no hardcoded absolute path).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [X] python not found on PATH. Install Python 3.10+ first.
  exit /b 1
)

python scripts\run_daily.py --quiet
