# Consistent snapshot (SQLite online backup) + originals/runs/exports with a sha256 manifest. Safe while running.
param([string]$Label = '')
. "$PSScriptRoot\_common.ps1"
Assert-Venv
exit (Invoke-Api @('backup','--label',$Label))
