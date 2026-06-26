
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

$guide = @"
# 病历助手 离线内测包

版本：v$version

## 适用场景

本包面向无法稳定访问外网的内测环境，已包含：

- Chrome/Edge 浏览器插件目录：``extension/``
- Windows 本地语音服务：``server/``
- Python 运行时：``runtime/python/``
- Python 依赖包：``.venv/Lib/site-packages/``
- 中文批量 ASR、中文流式 ASR、VAD 模型缓存：``models/modelscope/``
- 医用专业词汇包和用户自定义热词

## 使用步骤

1. 解压整个文件夹，不要只复制其中一部分。
2. 双击 ``start_server_offline.bat``。
3. 浏览器打开 ``http://127.0.0.1:8765/health``，确认服务显示 ``status: ok``。
4. Chrome 打开 ``chrome://extensions``，Edge 打开 ``edge://extensions``。
5. 打开“开发者模式”。
6. 点击“加载已解压的扩展”，选择本包里的 ``extension`` 文件夹。
7. 点击扩展图标开始试用。

## 注意事项

- 本包默认只支持中文本地语音识别；英文模型包不默认包含。
- 首次启动会加载模型，可能需要等待几十秒。
- 语音和病历草稿默认只在本机处理；反馈也默认保存到本机服务。
- 如果医院电脑拦截 bat 文件，请让信息科将本目录加入信任或改用正式安装器。

## 常见问题

- 如果插件提示本地服务未启动：先运行 ``start_server_offline.bat``。
- 如果端口占用：运行 ``stop_server.bat`` 后重新启动。
- 如果要导出内测反馈：打开 ``http://127.0.0.1:8765/feedback/export``。
- 如果要查看词库包：打开 ``http://127.0.0.1:8765/hotword-packs``。
"@
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
