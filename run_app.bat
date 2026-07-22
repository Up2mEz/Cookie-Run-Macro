@echo off
setlocal
cd /d "%~dp0"
title MuMu Pattern Studio Launcher
python --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer first.
  pause
  exit /b 1
)
python -c "import PIL, pynput" >nul 2>&1
if errorlevel 1 (
  echo Installing required packages...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Installation failed. Check your Internet connection and try again.
    pause
    exit /b 1
  )
)
python app.py
if errorlevel 1 (
  echo The app closed with an error. See the message above.
  pause
)
