
param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$OutputRoot = "",
  [switch]$CreateZip,
  [switch]$SkipVenv,
  [switch]$SkipPythonRuntime,
  [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not $OutputRoot) { $OutputRoot = Join-Path $ProjectRoot "dist" }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$manifestPath = Join-Path $ProjectRoot "extension\manifest.json"
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
$version = $manifest.version
$packageName = "bingli-assistant-v$version-beta-offline"
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

Write-Host "Building offline beta package: $stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

Write-Host "Copying project files..."
Invoke-RoboCopyChecked (Join-Path $ProjectRoot "extension") (Join-Path $stage "extension")
Invoke-RoboCopyChecked (Join-Path $ProjectRoot "server") (Join-Path $stage "server") @("/XD", "__pycache__", ".pytest_cache", "/XF", "*.pyc", "*.log")
if (Test-Path (Join-Path $ProjectRoot "docs")) { Invoke-RoboCopyChecked (Join-Path $ProjectRoot "docs") (Join-Path $stage "docs") }
if (Test-Path (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md")) { Copy-Item -Force (Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md") (Join-Path $stage "THIRD_PARTY_NOTICES.md") }
Copy-Item -Force (Join-Path $ProjectRoot "README.md") (Join-Path $stage "README.md")
Copy-Item -Force (Join-Path $ProjectRoot "start_server.bat") (Join-Path $stage "start_server_dev.bat")
Copy-Item -Force (Join-Path $ProjectRoot "install.bat") (Join-Path $stage "install_online_fallback.bat")

if (-not $SkipVenv) {
  $venv = Join-Path $ProjectRoot ".venv"
  if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { throw "Missing embedded Python environment: $venv" }
  Write-Host "Copying .venv dependencies. This can take several minutes..."
  Invoke-RoboCopyChecked $venv (Join-Path $stage ".venv") @("/XD", "__pycache__", ".pytest_cache", "/XF", "*.pyc")
} else {
  Write-Host "Skipping .venv copy by request. Offline package will require online install or preinstalled dependencies."
}

if (-not $SkipPythonRuntime) {
  $pyvenvCfg = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
  if (-not (Test-Path $pyvenvCfg)) { throw "Missing pyvenv.cfg; cannot locate base Python runtime" }
  $homeLine = Get-Content -Encoding UTF8 $pyvenvCfg | Where-Object { $_ -like "home = *" } | Select-Object -First 1
  if (-not $homeLine) { throw "Cannot find Python home in pyvenv.cfg" }
  $pythonHome = $homeLine.Substring(7).Trim()
  if (-not (Test-Path (Join-Path $pythonHome "python.exe"))) { throw "Missing base Python runtime: $pythonHome" }
  Write-Host "Copying base Python runtime from $pythonHome ..."
  Invoke-RoboCopyChecked $pythonHome (Join-Path $stage "runtime\python") @("/XD", "__pycache__", "/XF", "*.pyc")
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
set ASR_DEVICE=cpu
set ASR_PRELOAD_STREAMING=1
set MODELSCOPE_CACHE=%~dp0models\modelscope\hub
set MODELSCOPE_OFFLINE=1
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set PYTHONUTF8=1
set PYTHONPATH=%~dp0.venv\Lib\site-packages;%~dp0

echo Starting local ASR service at http://127.0.0.1:8765 ...
echo Model cache: %MODELSCOPE_CACHE%

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>nul

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
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do taskkill /F /PID %%a
pause
"@
Write-Utf8NoBom (Join-Path $stage "stop_server.bat") $stopServer

$guideLines = @(
  "# ???? Windows ?????",
  "",
  "???v$version",
  "",
  "## ????",
  "",
  "????????????? Windows ?????????",
  "",
  "- Chrome/Edge ????????``extension/``",
  "- Windows ???????``server/``",
  "- Python ????``runtime/python/``",
  "- Python ????``.venv/Lib/site-packages/``",
  "- ???? ASR????? ASR?VAD ?????``models/modelscope/``",
  "- ???????????????",
  "",
  "## ????",
  "",
  "1. ???????????????????",
  "2. ?? ``start_server_offline.bat``?",
  "3. ????? ``http://127.0.0.1:8765/health``??????? ``status: ok``?",
  "4. Edge ?? ``edge://extensions``?Chrome ?? ``chrome://extensions``?",
  "5. ???????????",
  "6. ??????????????????? ``extension`` ????",
  "7. ???????????",
  "",
  "## ????",
  "",
  "- ???????????????????????????",
  "- ????????????????????",
  "- ?????????????????????????????",
  "- ???????? bat ?????????????????????????",
  "",
  "## ????",
  "",
  "- ????????????????? ``start_server_offline.bat``?",
  "- ????????? ``stop_server.bat`` ??????",
  "- ???????????? ``http://127.0.0.1:8765/feedback/export``?",
  "- ??????????? ``http://127.0.0.1:8765/hotword-packs``?"
)
$guide = $guideLines -join [Environment]::NewLine
Write-Utf8NoBom (Join-Path $stage "README_OFFLINE_BETA.md") $guide

$sizeInfo = [ordered]@{
  package = $packageName
  version = $version
  built_at = (Get-Date).ToString("s")
  includes_venv = -not $SkipVenv
  includes_python_runtime = -not $SkipPythonRuntime
  includes_models = -not $SkipModels
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
  Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
  Write-Host "ZIP created: $zip"
}
