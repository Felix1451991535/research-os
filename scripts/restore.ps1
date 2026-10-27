# Restore a backup into a NEW directory and verify hashes + DB references. Does not switch the live data dir:
# after a PASS, stop.ps1, then point RESEARCHOS_DATA_DIR (or rename folders) to the restored directory.
param([Parameter(Mandatory)][string]$BackupDir, [Parameter(Mandatory)][string]$TargetDir)
. "$PSScriptRoot\_common.ps1"
Assert-Venv
exit (Invoke-Api @('restore',$BackupDir,$TargetDir))
