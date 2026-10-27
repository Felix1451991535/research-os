. "$PSScriptRoot\_common.ps1"
Assert-Venv
exit (Invoke-Api @('doctor'))
