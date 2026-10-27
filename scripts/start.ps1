# Start the Research OS API + worker hidden in the background; print the URLs to open.
. "$PSScriptRoot\_common.ps1"
Assert-Venv
New-Item -ItemType Directory -Force -Path $PidDir | Out-Null
if (Invoke-Api @('migrate')) { throw 'migration failed; see logs' }
$token = & $Py -m researchos.app.cli setup-token
$api = Start-Process -FilePath $PyW -ArgumentList '-m','researchos.app.cli','serve' -WorkingDirectory $Root -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
$wrk = Start-Process -FilePath $PyW -ArgumentList '-m','researchos_worker.main' -WorkingDirectory $Root -WindowStyle Hidden -PassThru
Set-Content -Path (Join-Path $PidDir 'api.pid') -Value $api.Id
Set-Content -Path (Join-Path $PidDir 'worker.pid') -Value $wrk.Id
Write-Host "Research OS started (api pid $($api.Id), worker pid $($wrk.Id)). Data: $DataDir"
Write-Host "Open on this computer:   see listen address in $DataDir\config\researchos.toml (default http://127.0.0.1:8443)"
& $Py -m researchos.app.cli lan-urls | ForEach-Object { Write-Host "Candidate LAN URL (needs host + TLS config): $_" }
if ($token -notmatch 'already initialized') { Write-Host "First-run setup token: $token" }
