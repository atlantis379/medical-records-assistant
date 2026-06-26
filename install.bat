@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher not found. Please install Python 3.10 or 3.11.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3.12 -m venv .venv 2>nul
  if errorlevel 1 py -3.11 -m venv .venv
)

echo Installing dependencies. This may take several minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r server\requirements.txt
if errorlevel 1 (
  echo [ERROR] Installation failed. Check network access and Python version.
  pause
  exit /b 1
)

echo.
echo Installation completed.
echo Run start_server.bat, then load the extension folder in Chrome or Edge.
pause
