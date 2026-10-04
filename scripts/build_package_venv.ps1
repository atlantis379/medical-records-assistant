param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$VenvPath = "",
  [string]$Python = ""
)

# Creates a clean virtual environment for the offline package: only what server\requirements.txt asks for,
# with the CPU build of PyTorch (customer computers have no GPU). The development .venv may hold a CUDA build
# of torch (5+ GB), which must never be packaged.
#
# Needs internet access once. On PyPI the default Windows torch wheel is the CPU build.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
if (-not $VenvPath) { $VenvPath = Join-Path $ProjectRoot "build\package-venv" }

if (-not $Python) {
  $cfg = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
  if (-not (Test-Path $cfg)) { throw "Pass -Python <python.exe>; .venv\pyvenv.cfg was not found" }
  $homeLine = Get-Content -Encoding UTF8 $cfg | Where-Object { $_ -like "home = *" } | Select-Object -First 1
  $Python = Join-Path $homeLine.Substring(7).Trim() "python.exe"
}
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }

if (Test-Path $VenvPath) { Write-Host "Removing the old package environment $VenvPath"; Remove-Item -Recurse -Force $VenvPath }
Write-Host "Creating $VenvPath with $Python"
& $Python -m venv $VenvPath
if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }

$venvPython = Join-Path $VenvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $ProjectRoot "server\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Picture recognition (outside reports). rapidocr's own dependency list names opencv-python; the headless build from
# server/requirements.txt is used instead, so rapidocr is installed without its dependencies.
& $venvPython -m pip install --no-deps "rapidocr-onnxruntime==1.4.4"
if ($LASTEXITCODE -ne 0) { throw "pip install rapidocr-onnxruntime failed" }
& $venvPython -m pip uninstall -y opencv-python 2>$null | Out-Null
$cv2Dir = Join-Path $VenvPath "Lib\site-packages\cv2"
# OpenCV's video library (about 25 MB) is not used: pictures are only decoded
Get-ChildItem $cv2Dir -Filter "opencv_videoio_ffmpeg*.dll" -ErrorAction SilentlyContinue | Remove-Item -Force
& $venvPython -c "import cv2, rapidocr_onnxruntime; print('picture recognition ok, OpenCV', cv2.__version__)"
if ($LASTEXITCODE -ne 0) { throw "picture recognition components do not load" }

$torch = & $venvPython -c "import torch; print(torch.__version__ + ' cuda=' + str(torch.version.cuda))"
Write-Host "torch: $torch"
if ($torch -match "cuda=(?!None)") { throw "This environment has a CUDA build of torch ($torch); the package must use the CPU build." }

$sizeMB = [math]::Round(((Get-ChildItem (Join-Path $VenvPath "Lib\site-packages") -Recurse -File -Force | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "Package environment ready: $VenvPath ($sizeMB MB of packages)"
