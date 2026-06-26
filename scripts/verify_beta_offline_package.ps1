
param(
  [Parameter(Mandatory=$true)]
  [string]$PackageRoot
)

$ErrorActionPreference = "Stop"
$PackageRoot = (Resolve-Path $PackageRoot).Path
$required = @(
  "extension\manifest.json",
  "server\app.py",
  ".venv\Lib\site-packages",
  "runtime\python\python.exe",
  "models\modelscope\hub\models\iic\speech_fsmn_vad_zh-cn-16k-common-pytorch",
  "models\modelscope\hub\models\iic\speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
  "models\modelscope\hub\models\iic\speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
  "start_server_offline.bat",
  "check_service.bat",
  "README_OFFLINE_BETA.md",
  "package-manifest.json"
)

$missing = @()
foreach ($item in $required) {
  $path = Join-Path $PackageRoot $item
  if (-not (Test-Path $path)) { $missing += $item }
}

if ($missing.Count -gt 0) {
  Write-Host "Missing required package items:" -ForegroundColor Red
  $missing | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
  exit 1
}

$manifest = Get-Content -Raw -Encoding UTF8 (Join-Path $PackageRoot "package-manifest.json") | ConvertFrom-Json
Write-Host "Package OK:" $manifest.package -ForegroundColor Green
Write-Host "Version:" $manifest.version
Write-Host "Total size MB:" $manifest.sizes_mb.total
Write-Host "Models size MB:" $manifest.sizes_mb.models
Write-Host "Venv packages size MB:" $manifest.sizes_mb.venv
Write-Host "Python runtime size MB:" $manifest.sizes_mb.python_runtime
