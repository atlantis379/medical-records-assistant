
param(
  [Parameter(Mandatory=$true)]
  [string]$PackageRoot
)

$ErrorActionPreference = "Stop"
$PackageRoot = (Resolve-Path $PackageRoot).Path
$required = @(
  "extension\manifest.json",
  "server\app.py",
  "server\data\reference\essential_drugs_2026.json",
  "server\data\reference\imaging_terms.txt",
  ".venv\Lib\site-packages\rapidocr_onnxruntime",
  ".venv\Lib\site-packages\cv2",
  ".venv\Lib\site-packages\pypinyin",
  ".venv\Lib\site-packages",
  "runtime\python\python.exe",
  "models\modelscope\hub\models\iic\speech_fsmn_vad_zh-cn-16k-common-pytorch",
  "models\modelscope\hub\models\iic\speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
  "BingliAssistant.exe",
  "Microsoft.Web.WebView2.Core.dll",
  "Microsoft.Web.WebView2.WinForms.dll",
  "runtimes/win-x64/native/WebView2Loader.dll",
  "start_server_offline.bat",
  "check_service.bat",
  "README_OFFLINE_BETA.md",
  "package-manifest.json"
)

# the streaming (live dictation) model is optional: batch-only packages leave it out on purpose
$manifestFile = Join-Path $PackageRoot "package-manifest.json"
if (Test-Path $manifestFile) {
  $declared = Get-Content -Raw -Encoding UTF8 $manifestFile | ConvertFrom-Json
  $includesStreaming = $true
  if ($declared.PSObject.Properties.Name -contains "includes_streaming_model") { $includesStreaming = [bool]$declared.includes_streaming_model }
  if ($includesStreaming) { $required += "models\modelscope\hub\models\iic\speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online" }
}
$stray = Join-Path $PackageRoot "models\modelscope\models"
if (Test-Path $stray) { Write-Host "Warning: unexpected folder $stray (a second copy of a model made by a wrong cache setting); remove it." -ForegroundColor Yellow }

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
