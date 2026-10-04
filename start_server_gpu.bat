@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Please run install.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
  echo [ERROR] Current PyTorch is CPU-only. Install a CUDA-enabled PyTorch build first.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\service_ctl.ps1" -Action stop
if errorlevel 2 (
  echo Port 8765 is used by another program. It was not stopped; close it and run this file again.
  pause
  exit /b 1
)
set "ASR_MODEL=%USERPROFILE%\.cache\modelscope\hub\models\iic\speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
set "ASR_STREAMING_MODEL=%USERPROFILE%\.cache\modelscope\hub\models\iic\speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
set ASR_PRELOAD_STREAMING=0
set ASR_DEVICE=cuda
echo Starting GPU ASR service at http://127.0.0.1:8765
".venv\Scripts\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port 8765
pause
