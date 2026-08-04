@echo off
setlocal
cd /d "%~dp0"
title MuMu Pattern Studio Launcher

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: creating .venv and installing dependencies...
  call install_dependencies.bat
  if errorlevel 1 (
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import sys, tkinter, PIL, pynput; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Project dependencies are missing or Python is too old.
  call install_dependencies.bat
  if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" app.py
if errorlevel 1 (
  echo The app closed with an error. See the message above.
  pause
)
