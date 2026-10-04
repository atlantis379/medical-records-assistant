param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
)

$ErrorActionPreference = "Stop"
$extensionDir = Join-Path $ProjectRoot "extension"
$serverDir = Join-Path $ProjectRoot "server"

Write-Host "Checking manifest..."
$manifest = Get-Content -Raw -Encoding UTF8 (Join-Path $extensionDir "manifest.json") | ConvertFrom-Json
if ($manifest.manifest_version -ne 3) { throw "manifest_version must be 3" }
if (-not $manifest.permissions.Contains("storage")) { throw "storage permission is required" }
$hostPermissions = @($manifest.host_permissions)
if (-not ($hostPermissions -contains "http://127.0.0.1:8765/*")) { throw "host permission for local service is missing" }
if ($hostPermissions -contains "<all_urls>") { throw "<all_urls> should not be used for store release" }

Write-Host "Checking JavaScript syntax..."
node --check (Join-Path $extensionDir "editor.js") | Out-Host
node --check (Join-Path $extensionDir "background.js") | Out-Host

Write-Host "Checking Python syntax..."
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }
& $python -B -m py_compile (Join-Path $serverDir "app.py")

Write-Host "Running regression tests..."
Push-Location $ProjectRoot
try {
  & $python -X utf8 -m unittest discover -s tests -t .
  if ($LASTEXITCODE -ne 0) { throw "Regression tests failed" }
} finally { Pop-Location }

Write-Host "Running JavaScript tests..."
node --check (Join-Path $extensionDir "fields.js") | Out-Host
node --check (Join-Path $extensionDir "miner_logic.js") | Out-Host
node --check (Join-Path $extensionDir "miner.js") | Out-Host
node --check (Join-Path $extensionDir "auth_logic.js") | Out-Host
node --check (Join-Path $extensionDir "auth.js") | Out-Host
node --check (Join-Path $extensionDir "report_logic.js") | Out-Host
node --check (Join-Path $extensionDir "report.js") | Out-Host
node --test (Join-Path $ProjectRoot "tests/js/fields.test.js") | Out-Host
node --test (Join-Path $ProjectRoot "tests/js/miner_logic.test.js") | Out-Host
node --test (Join-Path $ProjectRoot "tests/js/auth_logic.test.js") | Out-Host
node --test (Join-Path $ProjectRoot "tests/js/report_logic.test.js") | Out-Host
if ($LASTEXITCODE -ne 0) { throw "JavaScript tests failed" }

Write-Host "Checking productization docs..."
$required = @(
  "docs\PRIVACY_POLICY_DRAFT.md",
  "docs\STORE_LISTING_DRAFT.md",
  "docs\RELEASE_CHECKLIST.md",
  "docs\DISTRIBUTION_PLAN.md",
  "THIRD_PARTY_NOTICES.md"
)
foreach ($file in $required) {
  if (-not (Test-Path (Join-Path $ProjectRoot $file))) { throw "Missing $file" }
}

Write-Host "Release checks passed."