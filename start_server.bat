@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Please run install.bat first.
  pause
  exit /b 1
)

for /f %%P in ('powershell -NoProfile -Command "$c=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if($c){$c.OwningProcess ^| Select-Object -Unique}"') do taskkill /PID %%P /F >nul 2>nul
set ASR_MODEL=paraformer-zh
set ASR_STREAMING_MODEL=paraformer-zh-streaming
set ASR_PRELOAD_STREAMING=1
set ASR_DEVICE=cpu
echo Starting local ASR service v0.4 at http://127.0.0.1:8765
echo Batch model loads on demand; streaming model warms in the background.
".venv\Scripts\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port 8765
pause
