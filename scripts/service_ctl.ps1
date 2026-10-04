param(
  [ValidateSet("status", "stop")][string]$Action = "status",
  [int]$Port = 8765
)

# Developer helper (the installed package uses BingliAssistant.exe). Same rules as the launcher:
# only the 病历助手 service is ever stopped, and a program that merely holds the port is left alone.
#   exit 0: stopped / running      1: not running      2: port used by another program
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:$Port"

function Test-PortOpen {
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne(600)) { return $false }
    $client.EndConnect($async)
    return $true
  } catch { return $false } finally { $client.Close() }
}

function Get-ServiceState {
  try {
    $request = [System.Net.HttpWebRequest]::Create("$base/service/status")
    $request.Proxy = $null
    $request.Timeout = 1500
    $response = $request.GetResponse()
    $body = (New-Object System.IO.StreamReader($response.GetResponseStream(), [System.Text.Encoding]::UTF8)).ReadToEnd()
    $response.Close()
    if ($body -match '"service":"bingli-assistant"') { return "ours" }
    return "foreign"
  } catch [System.Net.WebException] {
    if ($_.Exception.Response) { return "foreign" }
    if (Test-PortOpen) { return "foreign" }
    return "none"
  } catch {
    if (Test-PortOpen) { return "foreign" }
    return "none"
  }
}

$state = Get-ServiceState
if ($Action -eq "status") {
  Write-Host $state
  switch ($state) { "ours" { exit 0 } "none" { exit 1 } default { exit 2 } }
}

# stop
if ($state -eq "foreign") { Write-Host "Port $Port is used by another program. Nothing was stopped."; exit 2 }
if ($state -eq "none") { Write-Host "The service is not running."; exit 0 }

$request = [System.Net.HttpWebRequest]::Create("$base/service/shutdown")
$request.Method = "POST"
$request.Proxy = $null
$request.Timeout = 4000
$request.ContentLength = 0
$request.Headers["Origin"] = $base
$request.Headers["X-Bingli-Control"] = "1"
try { $request.GetResponse().Close() } catch { Write-Host "Shutdown request failed: $($_.Exception.Message)" }
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 300
  if ((Get-ServiceState) -ne "ours") { Write-Host "The service is stopped."; exit 0 }
}
Write-Host "The service did not stop."
exit 1
