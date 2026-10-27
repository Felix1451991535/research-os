# One-click update helper (ADR-223). The server copies this file next to the verified installer (temp folder, outside
# app\, which setup replaces) and starts it hidden. Steps: stop the worker + API (stop.ps1), wait until the server
# process has exited, run the setup silently (/S /RUNAFTER: setup restarts Research OS after a successful update).
# Failure at any step: the current install stays (setup restores the previous app\ itself; data\ is never touched)
# and the previous version is started again. Log: <InstallDir>\data\logs\update.log.
# Windows execution: NOT RUN on the dev box (Linux); parse/static checks only.
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [Parameter(Mandatory = $true)][int]$ServerPid
)
$ErrorActionPreference = 'Stop'
$DataDir = Join-Path $InstallDir 'data'
$LogDir = Join-Path $DataDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'update.log'
function Write-Log([string]$m) { Add-Content -Path $Log -Value ("{0:s} {1}" -f (Get-Date), $m) -Encoding UTF8 }
function Start-Previous {
  $vbs = Join-Path $InstallDir 'app\scripts\launch.vbs'
  if (Test-Path $vbs) { Start-Process -FilePath "$env:SystemRoot\System32\wscript.exe" -ArgumentList "`"$vbs`"" }
}

try {
  Write-Log "update: $Installer -> $InstallDir (server pid $ServerPid)"
  Start-Sleep -Seconds 2   # let the server finish answering the install request
  $stop = Join-Path $InstallDir 'app\scripts\stop.ps1'
  if (Test-Path $stop) { & $stop -DataDir $DataDir *>> $Log }
  Wait-Process -Id $ServerPid -Timeout 60 -ErrorAction SilentlyContinue
  if (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue) {
    Write-Log "server still running after 60 s; update not started, nothing changed"
    exit 1
  }
  $p = Start-Process -FilePath $Installer -ArgumentList '/S', '/RUNAFTER' -WindowStyle Hidden -Wait -PassThru
  Write-Log "setup exit code $($p.ExitCode) (details: $InstallDir\install.log)"
  if ($p.ExitCode -ne 0) { Start-Previous; exit $p.ExitCode }
} catch {
  Write-Log "update failed: $($_.Exception.Message)"
  Start-Previous
  exit 1
}
