# Renders reading prompts to 16 kHz mono WAV with the Windows text-to-speech voice.
#
# SMOKE-TEST AUDIO ONLY. A synthetic voice has no accents, hesitations or noise, so results on it say
# nothing about real clinicians. The manifest must use source "synthetic_tts"; reports then refuse to
# call any metric PASS.
#
#   powershell -ExecutionPolicy Bypass -File evaluation\tools\synthesize_prompts.ps1 `
#       -OutDir evaluation\datasets\smoke\audio -Ids r03,r07,i03
param(
  [string]$PromptsFile = "",
  [Parameter(Mandatory = $true)][string]$OutDir,
  [string[]]$Ids = @(),
  [string]$Voice = "Microsoft Huihui Desktop"
)

$ErrorActionPreference = "Stop"
if (-not $PromptsFile) {
  $here = Split-Path -Parent $MyInvocation.MyCommand.Path
  $PromptsFile = Join-Path $here "..\prompts\prompts.jsonl"
}
# -File passes "a,b,c" as one string
$Ids = @($Ids | ForEach-Object { $_ -split "," } | Where-Object { $_ })
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$installed = $synth.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
if ($installed -notcontains $Voice) { throw "Voice '$Voice' is not installed. Installed: $($installed -join ', ')" }
$synth.SelectVoice($Voice)
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

$count = 0
foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path $PromptsFile), [System.Text.Encoding]::UTF8)) {
  if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) { continue }
  $prompt = $line | ConvertFrom-Json
  if ($Ids.Count -gt 0 -and $Ids -notcontains $prompt.id) { continue }
  $path = Join-Path (Resolve-Path $OutDir) ($prompt.id + ".wav")
  $synth.SetOutputToWaveFile($path, $format)
  $synth.Speak($prompt.spoken)
  $synth.SetOutputToNull()
  $count++
}
$synth.Dispose()
Write-Host "Wrote $count file(s) to $OutDir"
