# Shared helpers for Research OS PowerShell scripts. Windows execution = NOT RUN on the dev box (Linux).
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $env:RESEARCHOS_DATA_DIR) { $env:RESEARCHOS_DATA_DIR = 'D:\AI\ResearchOS\data' }
$DataDir = $env:RESEARCHOS_DATA_DIR
# setup .exe install: embedded CPython in app\python (ADR-215); checkout / install.bat: the uv venv
$PyDir = if (Test-Path (Join-Path $Root 'python\python.exe')) { Join-Path $Root 'python' } else { Join-Path $Root '.venv\Scripts' }
$Py = Join-Path $PyDir 'python.exe'
$PyW = Join-Path $PyDir 'pythonw.exe'   # no console window (owner rule: never pop windows)
$PidDir = Join-Path $DataDir 'run'
$env:PYTHONUTF8 = '1'
function Assert-Venv { if (-not (Test-Path $Py)) { throw "Python not found at $Py. Reinstall Research OS." } }
function Invoke-Api([string[]]$CliArgs) { & $Py -m researchos.app.cli @CliArgs; return $LASTEXITCODE }
