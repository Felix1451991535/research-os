# One-click update helper (ADR-223/228). The server copies this file next to the verified installer (temp folder,
# outside app\, which setup replaces) and starts it hidden. Steps: stop the worker + API (stop.ps1), wait until the
# server process has exited, run the setup silently (/S /RUNAFTER /KEEPPREV: setup restarts Research OS and keeps the
# previous version as app.prev), then wait until <HealthUrl> answers with the new version. Healthy: app.prev is
# removed. Setup failed: setup has already restored app\; the previous version is started again. Not healthy in time:
# the new app\ is set aside as app.failed, app.prev becomes app\ again and the previous version is started; the reason
# is logged. data\ is never touched. Log: <InstallDir>\data\logs\update.log.
# Windows execution: NOT RUN on the dev box (Linux); parse/static checks only.
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [Parameter(Mandatory = $true)][int]$ServerPid,
  [Parameter(Mandatory = $true)][string]$Version,
  [Parameter(Mandatory = $true)][string]$HealthUrl,
  [int]$HealthTimeoutSec = 120
)
$ErrorActionPreference = 'Stop'
$DataDir = Join-Path $InstallDir 'data'
$LogDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'update.log'
$App = Join-Path $InstallDir 'app'
$Prev = Join-Path $InstallDir 'app.prev'
$Failed = Join-Path $InstallDir 'app.failed'
function Write-Log([string]$m) { Add-Content -Path $Log -Value ("{0:s} {1}" -f (Get-Date), $m) -Encoding UTF8 }
function Start-Previous {
  $vbs = Join-Path $App 'scripts\launch.vbs'
  if (Test-Path $vbs) { Start-Process -FilePath "$env:SystemRoot\System32\wscript.exe" -ArgumentList "`"$vbs`"" }
}
function Stop-App {
  $stop = Join-Path $App 'scripts\stop.ps1'
  if (Test-Path $stop) { & $stop -DataDir $DataDir *>> $Log }
}

function Get-RunningVersion {
  # our own server on loopback (self-signed certificate if TLS is on): this process only talks to it
  [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
  try { return (Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3).version } catch { return $null }
}

function Wait-Healthy {
  $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
  $seen = $null
  while ((Get-Date) -lt $deadline) {
    $seen = Get-RunningVersion
    if ($seen -eq $Version) { return $null }
    Start-Sleep -Seconds 2
  }
  if ($seen) { return "server answers with version $seen, expected $Version" }
  return "no answer from $HealthUrl within $HealthTimeoutSec s"
}

function Undo-Update([string]$reason) {
  Write-Log "health check failed: $reason; rolling back to the previous version"
  if (-not (Test-Path $Prev)) {
    Write-Log "ROLLBACK NOT POSSIBLE: no $Prev (the new version stays installed)"
    return
  }
  Stop-App
  if (Test-Path $Failed) { Remove-Item -Recurse -Force $Failed }
  Rename-Item -Path $App -NewName 'app.failed'
  Rename-Item -Path $Prev -NewName 'app'
  Write-Log ("rolled back: previous version restored to app\, the failed one kept as app.failed. If the new version" +
    " already migrated the database, the pre-migration backup is in data\backups (researchos-api restore).")
  Start-Previous
}

try {
  Write-Log "update to $Version`: $Installer -> $InstallDir (server pid $ServerPid)"
  Start-Sleep -Seconds 2   # let the server finish answering the install request
  Stop-App
  Wait-Process -Id $ServerPid -Timeout 60 -ErrorAction SilentlyContinue
  if (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue) {
    Write-Log "server still running after 60 s; update not started, nothing changed"
    exit 1
  }
  $p = Start-Process -FilePath $Installer -ArgumentList '/S', '/RUNAFTER', '/KEEPPREV' -WindowStyle Hidden -Wait -PassThru
  Write-Log "setup exit code $($p.ExitCode) (details: $InstallDir\install.log)"
  if ($p.ExitCode -ne 0) { Start-Previous; exit $p.ExitCode }
  $problem = Wait-Healthy
  if ($problem) { Undo-Update $problem; exit 2 }
  if (Test-Path $Prev) { Remove-Item -Recurse -Force $Prev }
  Write-Log "health check OK: version $Version is running"
} catch {
  Write-Log "update failed: $($_.Exception.Message)"
  Start-Previous
  exit 1
}
