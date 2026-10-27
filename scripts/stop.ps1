# Stop the worker (graceful: finishes/cancels current task), then the API.
# -DataDir: the install's data folder (the uninstaller passes it; review #80). Exit code != 0 when stopping failed.
param([string]$DataDir = '')
if ($DataDir) { $env:RESEARCHOS_DATA_DIR = $DataDir }
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # setup captures this output as UTF-8 into install.log
. "$PSScriptRoot\_common.ps1"
foreach ($name in 'worker','api') {
  $f = Join-Path $PidDir "$name.pid"
  if (Test-Path $f) {
    $procId = [int](Get-Content $f)
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($p) { Stop-Process -Id $procId; Wait-Process -Id $procId -Timeout 20 -ErrorAction SilentlyContinue }
    Remove-Item $f -Force
    Write-Host "stopped $name ($procId)"
  }
}
