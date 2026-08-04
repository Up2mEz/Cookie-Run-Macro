@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=python"
python --version >nul 2>&1
if errorlevel 1 (
  py -3 --version >nul 2>&1
  if errorlevel 1 (
    echo Python 3.10 or newer was not found.
    echo Install Python from https://www.python.org/downloads/windows/
    pause
    exit /b 1
  )
  set "PYTHON_CMD=py -3"
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  python --version
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the project virtual environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo Could not create .venv.
    pause
    exit /b 1
  )
)

echo Installing project dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check Python and your Internet connection.
  pause
  exit /b 1
)
echo Installation completed. Open START_HERE.bat to run the app.
exit /b 0
