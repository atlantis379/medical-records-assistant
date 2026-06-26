param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$extensionDir = Join-Path $ProjectRoot "extension"
$distDir = Join-Path $ProjectRoot "dist"
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

$manifestPath = Join-Path $extensionDir "manifest.json"
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if (-not $Version) { $Version = $manifest.version }

$out = Join-Path $distDir ("bingli-assistant-extension-v{0}.zip" -f $Version)
if (Test-Path $out) { Remove-Item -Force $out }

$staging = Join-Path $env:TEMP ("clinical-dictation-extension-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Copy-Item -Recurse -Force (Join-Path $extensionDir "*") $staging

Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $out -Force
Remove-Item -Recurse -Force $staging

Write-Host "Extension package created: $out"