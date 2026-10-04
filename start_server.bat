@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Please run install.bat first.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\service_ctl.ps1" -Action stop
if errorlevel 2 (
  echo Port 8765 is used by another program. It was not stopped; close it and run this file again.
  pause
  exit /b 1
)
set ASR_MODEL=paraformer-zh
set ASR_STREAMING_MODEL=paraformer-zh-streaming
set ASR_PRELOAD_STREAMING=1
set ASR_DEVICE=cpu
echo Starting local ASR service at http://127.0.0.1:8765
echo Batch model loads on demand; streaming model warms in the background.
".venv\Scripts\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port 8765
pause
