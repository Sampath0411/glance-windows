@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo The Python environment is missing.
  echo Run run.bat once, then start Glance again.
  pause
  exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" -m desktop.main
