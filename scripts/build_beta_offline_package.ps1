
param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$OutputRoot = "",
  [switch]$CreateZip,
  [switch]$SkipVenv,
  [switch]$SkipPythonRuntime,
  [switch]$SkipModels,
  [switch]$SkipStreamingModel,
  [string]$VenvPath = "",
  [switch]$AllowGpuTorch
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not $OutputRoot) { $OutputRoot = Join-Path $ProjectRoot "dist" }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$manifestPath = Join-Path $ProjectRoot "extension\manifest.json"
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
$version = $manifest.version
$packageName = "bingli-assistant-v$version-beta-offline"
if ($SkipStreamingModel) { $packageName = "bingli-assistant-v$version-beta-offline-batch" }   # no live (streaming) dictation
$stage = Join-Path $OutputRoot $packageName

function Write-Utf8NoBom([string]$Path, [string]$Text) {
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Get-DirSizeMB([string]$Path) {
  if (-not (Test-Path $Path)) { return 0 }
  $sum = (Get-ChildItem -Path $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  return [math]::Round(($sum / 1MB), 1)
}

function Invoke-RoboCopyChecked([string]$Source, [string]$Destination, [string[]]$ExtraArgs = @()) {
  if (-not (Test-Path $Source)) { throw "Missing source: $Source" }
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  $args = @($Source, $Destination, "/MIR", "/R:2", "/W:2", "/NFL", "/NDL", "/NP") + $ExtraArgs
  & robocopy @args | Out-Host
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE : $Source" }
}

$modelCacheRoot = Join-Path $env:USERPROFILE ".cache\modelscope"
$modelRoot = Join-Path $modelCacheRoot "hub\models\iic"
$requiredModels = @(
  "speech_fsmn_vad_zh-cn-16k-common-pytorch",
  "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
  "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
)

if ($SkipStreamingModel) {
  # Dictation then works after the doctor stops recording (batch recognition); live text while speaking is not available.
  $requiredModels = @($requiredModels | Where-Object { $_ -notlike "*-online" })
}

Write-Host "Building offline beta package: $stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

Write-Host "Copying project files..."
Invoke-RoboCopyChecked (Join-Path $ProjectRoot "extension") (Join-Path $stage "extension")
Invoke-RoboCopyChecked (Join-Path $ProjectRoot "server") (Join-Path $stage "server") @("/XD", "__pycache__", ".pytest_cache", "/XF", "*.pyc", "*.log", "feedback.jsonl")
if (Test-Path (Join-Path $ProjectRoot "docs")) { Invoke-RoboCopyChecked (Join-Path $ProjectRoot "docs") (Join-Path $stage "docs") }
if (Test-Path (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md")) { Copy-Item -Force (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") (Join-Path $stage "THIRD_PARTY_NOTICES.md") }
Copy-Item -Force (Join-Path $ProjectRoot "README.md") (Join-Path $stage "README.md")
Copy-Item -Force (Join-Path $ProjectRoot "start_server.bat") (Join-Path $stage "start_server_dev.bat")
Copy-Item -Force (Join-Path $ProjectRoot "install.bat") (Join-Path $stage "install_online_fallback.bat")

# The packaged Python is the plain interpreter; the libraries live in .venv\Lib\site-packages and are reached through
# PYTHONPATH. A path added that way is NOT processed as a site directory, so its .pth files are ignored. One of them
# (setuptools' distutils-precedence.pth) is what gives Python 3.12 the `distutils` module that FunASR imports; without it
# FunASR silently fails to register its models. sitecustomize.py (found through PYTHONPATH) fixes that.
$siteCustomize = @"
import os
import site

_root = os.path.dirname(os.path.abspath(__file__))
site.addsitedir(os.path.join(_root, ".venv", "Lib", "site-packages"))
"@
Write-Utf8NoBom (Join-Path $stage "sitecustomize.py") $siteCustomize

Write-Host "Building the tray launcher (BingliAssistant.exe)..."
& (Join-Path $ProjectRoot "scripts\build_windows_launcher.ps1") -ProjectRoot $ProjectRoot -Output (Join-Path $stage "BingliAssistant.exe")
if (-not (Test-Path (Join-Path $stage "BingliAssistant.exe"))) { throw "Launcher build failed" }

# Which environment to package. Prefer the clean CPU one made by scripts\build_package_venv.ps1; the development
# .venv may hold a CUDA build of torch, which is 5+ GB and useless on computers without a GPU.
if (-not $VenvPath) {
  $clean = Join-Path $ProjectRoot "build\package-venv"
  $VenvPath = if (Test-Path (Join-Path $clean "Scripts\python.exe")) { $clean } else { Join-Path $ProjectRoot ".venv" }
}

if (-not $SkipVenv) {
  $venv = $VenvPath
  if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { throw "Missing embedded Python environment: $venv" }
  $site = Join-Path $venv "Lib\site-packages"
  $cudaTorch = @(Get-ChildItem $site -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "^torch-.*\+cu\d+" -or $_.Name -like "nvidia_*" })
  $cudaLibs = Test-Path (Join-Path $site "torch\lib\cudnn64_9.dll")
  if (($cudaTorch.Count -gt 0 -or $cudaLibs) -and -not $AllowGpuTorch) {
    throw "$venv holds a CUDA build of torch (5+ GB). Run scripts\build_package_venv.ps1 to create a clean CPU environment, or pass -AllowGpuTorch if you really want it."
  }
  Write-Host "Packaging the Python environment: $venv"
  Write-Host "Copying .venv dependencies. This can take several minutes..."
  Invoke-RoboCopyChecked $venv (Join-Path $stage ".venv") @("/XD", "__pycache__", ".pytest_cache", "/XF", "*.pyc")
} else {
  Write-Host "Skipping .venv copy by request. Offline package will require online install or preinstalled dependencies."
}

if (-not $SkipPythonRuntime) {
  $pyvenvCfg = Join-Path $VenvPath "pyvenv.cfg"
  if (-not (Test-Path $pyvenvCfg)) { throw "Missing pyvenv.cfg; cannot locate base Python runtime" }
  $homeLine = Get-Content -Encoding UTF8 $pyvenvCfg | Where-Object { $_ -like "home = *" } | Select-Object -First 1
  if (-not $homeLine) { throw "Cannot find Python home in pyvenv.cfg" }
  $pythonHome = $homeLine.Substring(7).Trim()
  if (-not (Test-Path (Join-Path $pythonHome "python.exe"))) { throw "Missing base Python runtime: $pythonHome" }
  Write-Host "Copying base Python runtime from $pythonHome ..."
  # Only the interpreter and standard library are needed: the service's packages come from .venv (PYTHONPATH).
  # Copying the base Python as it is also copied everything installed globally on the build computer
  # (spaCy models, paddle, playwright ...: about 2 GB). Documentation, tests and Tk are not used either.
  Invoke-RoboCopyChecked $pythonHome (Join-Path $stage "runtime\python") @("/XD", "__pycache__", "site-packages", "test", "tests", "idlelib", "turtledemo", "Doc", "tcl", "/XF", "*.pyc")
} else {
  Write-Host "Skipping Python runtime copy by request. Target computers must have a compatible Python installed."
}

if (-not $SkipModels) {
  Write-Host "Copying ModelScope ASR model cache..."
  foreach ($model in $requiredModels) {
    $src = Join-Path $modelRoot $model
    if (-not (Test-Path $src)) { throw "Missing required model cache: $src" }
    $dst = Join-Path $stage ("models\modelscope\hub\models\iic\" + $model)
    Invoke-RoboCopyChecked $src $dst @("/XD", "__pycache__", ".git", "/XF", "*.lock")
  }
} else {
  Write-Host "Skipping model copy by request. Offline recognition will not work until models are installed."
}

$offlineStart = @"
@echo off
setlocal
cd /d "%~dp0"
rem Visible-window start for troubleshooting. Daily use: double-click BingliAssistant.exe (tray, no window).
set ASR_DEVICE=cpu
set ASR_PRELOAD_STREAMING=1
set ASR_PRELOAD_BATCH=1
set MODELSCOPE_CACHE=%~dp0models\modelscope\hub
set MODELSCOPE_OFFLINE=1
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set PYTHONUTF8=1
set PYTHONNOUSERSITE=1
set PYTHONPATH=%~dp0.venv\Lib\site-packages;%~dp0

if exist "%~dp0BingliAssistant.exe" (
  start "" /wait "%~dp0BingliAssistant.exe" --status
  if errorlevel 2 (
    echo Port 8765 is used by another program. Nothing was stopped.
    pause
    exit /b 1
  )
  if not errorlevel 1 (
    echo The service is already running. Run stop_server.bat first if you want to restart it.
    pause
    exit /b 0
  )
)

echo Starting local ASR service at http://127.0.0.1:8765 ...
echo Model cache: %MODELSCOPE_CACHE%

if exist "%~dp0runtime\python\python.exe" (
  set PYTHON_EXE=%~dp0runtime\python\python.exe
) else (
  set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
)

if not exist "%PYTHON_EXE%" (
  echo Missing Python runtime. This offline package is incomplete.
  pause
  exit /b 1
)

"%PYTHON_EXE%" -B -m uvicorn server.app:app --host 127.0.0.1 --port 8765
pause
"@
Write-Utf8NoBom (Join-Path $stage "start_server_offline.bat") $offlineStart

$healthCheck = @"
@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod http://127.0.0.1:8765/health | ConvertTo-Json -Depth 5 } catch { Write-Host `$_.Exception.Message; exit 1 }"
pause
"@
Write-Utf8NoBom (Join-Path $stage "check_service.bat") $healthCheck

$stopServer = @"
@echo off
setlocal
if not exist "%~dp0BingliAssistant.exe" (
  echo BingliAssistant.exe was not found.
  pause
  exit /b 1
)
start "" /wait "%~dp0BingliAssistant.exe" --stop
if errorlevel 2 (
  echo Port 8765 is used by another program. Nothing was stopped.
) else if errorlevel 1 (
  echo The service could not be stopped.
) else (
  echo The service is stopped.
)
pause
"@
Write-Utf8NoBom (Join-Path $stage "stop_server.bat") $stopServer

$template = Get-Content -Raw -Encoding UTF8 (Join-Path $ProjectRoot "packaging\windows\README_OFFLINE_BETA.template.md")
$guide = $template.Replace("{version}", $version)
Write-Utf8NoBom (Join-Path $stage "README_OFFLINE_BETA.md") $guide

$sizeInfo = [ordered]@{
  package = $packageName
  version = $version
  built_at = (Get-Date).ToString("s")
  includes_venv = -not $SkipVenv
  includes_python_runtime = -not $SkipPythonRuntime
  includes_models = -not $SkipModels
  includes_streaming_model = -not ($SkipModels -or $SkipStreamingModel)
  sizes_mb = [ordered]@{
    extension = Get-DirSizeMB (Join-Path $stage "extension")
    server = Get-DirSizeMB (Join-Path $stage "server")
    venv = Get-DirSizeMB (Join-Path $stage ".venv")
    python_runtime = Get-DirSizeMB (Join-Path $stage "runtime\python")
    models = Get-DirSizeMB (Join-Path $stage "models")
    total = Get-DirSizeMB $stage
  }
  model_cache_root = "models/modelscope"
  required_models = $requiredModels
}
$sizeInfo | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $stage "package-manifest.json")

Write-Host "Offline package folder created: $stage"
Write-Host "Approx size: $((Get-DirSizeMB $stage)) MB"

if ($CreateZip) {
  $zip = Join-Path $OutputRoot ("$packageName.zip")
  if (Test-Path $zip) { Remove-Item -Force $zip }
  Write-Host "Creating ZIP. This can take a long time for a 3GB+ package..."
  # Compress-Archive fails above 2 GB in Windows PowerShell 5.1; ZipFile writes Zip64. The models do not
  # compress, so the fastest level is used.
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [System.IO.Compression.CompressionLevel]::Fastest, $false)
  Write-Host "ZIP created: $zip"
}
