@echo off
setlocal
cd /d "%~dp0"
echo Installing Pillow and pynput...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check Python and your Internet connection.
  pause
  exit /b 1
)
echo Installation completed. Open START_HERE.bat to run the app.
pause
