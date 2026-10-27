# Register (or remove with -Remove) a per-user Task Scheduler task that starts Research OS hidden at logon.
param([switch]$Remove)
. "$PSScriptRoot\_common.ps1"
$name = 'ResearchOS'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; Write-Host 'removed'; exit 0 }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\start.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Description 'Research OS (hidden)' -Force | Out-Null
Write-Host "Autostart registered for $env:USERNAME (Task Scheduler: $name)."
