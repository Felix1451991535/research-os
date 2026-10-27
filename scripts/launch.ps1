# Research OS launcher for the Desktop / Start Menu shortcut (via launch.vbs, so no console window ever shows).
# Starts the server hidden if it is not answering, waits until it answers, then opens the browser.
# First run: shows the one-time setup token (also copied to the clipboard). Errors: message box + data\logs\launcher.log.
# Windows execution: NOT RUN on the dev box (Linux); compiled/static checks only.
$ErrorActionPreference = 'Stop'
$AppRoot = Split-Path -Parent $PSScriptRoot
$InstallData = Join-Path (Split-Path -Parent $AppRoot) 'data'
if (Test-Path $InstallData) { $env:RESEARCHOS_DATA_DIR = $InstallData }   # the data dir next to app\ wins
. "$PSScriptRoot\_common.ps1"
Add-Type -AssemblyName System.Windows.Forms
$LogDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'launcher.log'
function Write-Log([string]$m) { Add-Content -Path $Log -Value ("{0:s} {1}" -f (Get-Date), $m) -Encoding UTF8 }

function Get-Url {
  $cfg = Join-Path $DataDir 'config\researchos.toml'
  $h, $p, $tls = '127.0.0.1', '8443', $false
  if (Test-Path $cfg) {
    foreach ($line in Get-Content $cfg) {
      if ($line -match '^\s*host\s*=\s*"([^"]+)"') { $h = $Matches[1] }
      if ($line -match '^\s*port\s*=\s*(\d+)') { $p = $Matches[1] }
      if ($line -match '^\s*tls_cert_file\s*=') { $tls = $true }
    }
  }
  if ($h -eq '0.0.0.0') { $h = '127.0.0.1' }
  return ('{0}://{1}:{2}/' -f ($(if ($tls) { 'https' } else { 'http' }), $h, $p))
}

function Test-Up([string]$url) {
  try {
    # our own self-signed LAN certificate (install -Lan): this process only talks to its own server
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    $r = Invoke-WebRequest -Uri ($url + 'api/v1/health') -UseBasicParsing -TimeoutSec 3
    return $r.StatusCode -eq 200
  } catch { return $false }
}

try {
  Assert-Venv
  $url = Get-Url
  if (-not (Test-Up $url)) {
    Write-Log "starting server for $url"
    & (Join-Path $PSScriptRoot 'start.ps1') *>> $Log
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Up $url)) {
      if ((Get-Date) -gt $deadline) { throw "the server did not answer at $url within 90 s" }
      Start-Sleep -Milliseconds 700
    }
  }
  $tokenFile = Join-Path $DataDir 'config\setup-token.txt'
  if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    Set-Clipboard -Value $token
    [System.Windows.Forms.MessageBox]::Show(
      "First start: create your account in the browser with this one-time setup token (already copied):`n`n$token`n`n" +
      "首次启动：请在浏览器中用这个一次性设置口令创建账号（已复制到剪贴板）。",
      'Research OS', 'OK', 'Information') | Out-Null
  }
  Write-Log "opening $url"
  Start-Process $url
} catch {
  Write-Log "ERROR: $($_.Exception.Message)"
  [System.Windows.Forms.MessageBox]::Show(
    "Research OS could not start:`n$($_.Exception.Message)`n`nLog: $Log", 'Research OS', 'OK', 'Error') | Out-Null
  exit 1
}
