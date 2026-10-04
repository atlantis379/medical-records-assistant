param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
  [string]$Output = ""
)

# Compiles packaging\windows\launcher\*.cs into BingliAssistant.exe with the csc.exe that ships with Windows
# (no SDK needed). The exe is a tray program: no console window. Next to it the WebView2 components are placed
# (packaging\windows\vendor\webview2): the program shows the dictation page in its own window with them, and
# falls back to the default browser where they or the WebView2 runtime are missing.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$launcherDir = Join-Path $ProjectRoot "packaging\windows\launcher"
$sources = @(Get-ChildItem $launcherDir -Filter *.cs | ForEach-Object { $_.FullName })
$webview = Join-Path $ProjectRoot "packaging\windows\vendor\webview2"
if (-not $Output) { $Output = Join-Path $ProjectRoot "dist\launcher\BingliAssistant.exe" }
if (-not (Test-Path (Join-Path $launcherDir "BingliLauncher.cs"))) { throw "Launcher source not found in $launcherDir" }

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe" }
if (-not (Test-Path $csc)) { throw "Cannot find the .NET Framework compiler (csc.exe)" }

$webviewFiles = @("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")
foreach ($file in $webviewFiles) {
  if (-not (Test-Path (Join-Path $webview $file))) { throw "WebView2 component missing: $(Join-Path $webview $file)" }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
if (Test-Path $Output) { Remove-Item -Force $Output }

$coreDll = Join-Path $webview "Microsoft.Web.WebView2.Core.dll"
$formsDll = Join-Path $webview "Microsoft.Web.WebView2.WinForms.dll"
& $csc /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 /utf8output `
  /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/reference:$coreDll" "/reference:$formsDll" `
  "/out:$Output" $sources
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE" }

# the window needs these next to the exe (the loader goes where the WebView2 package expects it on 64-bit Windows)
$outDir = Split-Path -Parent $Output
Copy-Item -Force $coreDll $outDir
Copy-Item -Force $formsDll $outDir
$loaderDir = Join-Path $outDir "runtimes\win-x64\native"
New-Item -ItemType Directory -Force -Path $loaderDir | Out-Null
Copy-Item -Force (Join-Path $webview "WebView2Loader.dll") $loaderDir
Write-Host "Built $Output"
