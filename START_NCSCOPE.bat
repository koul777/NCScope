@echo off
setlocal
cd /d "%~dp0"

echo Starting NCScope...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"

if errorlevel 1 (
  echo.
  echo NCScope failed to start. Check the messages above.
  pause
)
