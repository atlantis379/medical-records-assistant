param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$Version = "",
  [string]$PackageZip = "",
  [string]$OutputExe = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$manifestPath = Join-Path $ProjectRoot "extension\manifest.json"
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if (-not $Version) { $Version = $manifest.version }
if (-not $PackageZip) { $PackageZip = Join-Path $ProjectRoot "dist\bingli-assistant-v$Version-beta-offline.zip" }
if (-not $OutputExe) { $OutputExe = Join-Path $ProjectRoot "dist\bingli-assistant-setup-v$Version-beta.exe" }

$source = Join-Path $ProjectRoot "packaging\windows\sfx\BingliAssistantSetupStub.cs"
$buildDir = Join-Path $ProjectRoot "dist\installer-build"
$stubExe = Join-Path $buildDir "BingliAssistantSetupStub.exe"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

if (-not (Test-Path $PackageZip)) { throw "Package ZIP not found: $PackageZip" }
if (-not (Test-Path $source)) { throw "Installer stub source not found: $source" }

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "Cannot find .NET Framework csc.exe" }

Write-Host "Compiling installer stub..."
& $csc /nologo /target:exe /platform:anycpu /optimize+ /utf8output /out:$stubExe /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll $source
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

$magic = [System.Text.Encoding]::ASCII.GetBytes("BLASFX1!")
$package = Get-Item $PackageZip
$outDir = Split-Path -Parent $OutputExe
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
if (Test-Path $OutputExe) { Remove-Item -Force $OutputExe }

Write-Host "Creating self-extracting setup EXE..."
Write-Host "Input ZIP: $PackageZip"
Write-Host "Output EXE: $OutputExe"

$sha = [System.Security.Cryptography.SHA256]::Create()
$buffer = New-Object byte[] (4MB)
$out = [System.IO.File]::Open($OutputExe, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
try {
  $stub = [System.IO.File]::OpenRead($stubExe)
  try { $stub.CopyTo($out) } finally { $stub.Dispose() }

  $zip = [System.IO.File]::OpenRead($PackageZip)
  try {
    while (($read = $zip.Read($buffer, 0, $buffer.Length)) -gt 0) {
      $out.Write($buffer, 0, $read)
      [void]$sha.TransformBlock($buffer, 0, $read, $null, 0)
    }
    [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
  } finally { $zip.Dispose() }

  $lenBytes = [System.BitConverter]::GetBytes([int64]$package.Length)
  $out.Write($magic, 0, $magic.Length)
  $out.Write($lenBytes, 0, $lenBytes.Length)
  $out.Write($sha.Hash, 0, $sha.Hash.Length)
} finally {
  $out.Dispose()
  $sha.Dispose()
}

$exeHash = Get-FileHash -Algorithm SHA256 $OutputExe
$shaPath = "$OutputExe.sha256"
$exeHash.Hash | Set-Content -Encoding ASCII $shaPath

Write-Host "Setup EXE created: $OutputExe"
Write-Host "Size MB: $([math]::Round((Get-Item $OutputExe).Length / 1MB, 1))"
Write-Host "SHA256: $($exeHash.Hash)"
Write-Host "SHA256 file: $shaPath"
