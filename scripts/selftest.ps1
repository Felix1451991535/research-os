# Owner-run acceptance on the Windows laptop (replaces external CI; no GitHub push).
# Runs: Python tests (incl. Windows-only paths: Job Object kill-tree, sharing violations), doctor, and a live
# start -> self-test task -> stop cycle. Writes a timestamped report to <data>\logs\selftest-*.txt.
. "$PSScriptRoot\_common.ps1"
Assert-Venv
$report = Join-Path $DataDir ("logs\selftest-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path (Split-Path $report) | Out-Null
function Log($m) { $m | Tee-Object -FilePath $report -Append }
Log "Research OS selftest $(Get-Date -Format o) on $([Environment]::OSVersion.VersionString)"
$pytestRc = 1
Push-Location $Root
try {
  uv sync --frozen --extra experiments --extra dev --extra plugins 2>&1 | Tee-Object -FilePath $report -Append
  & $Py -m pytest -q -p no:warnings 2>&1 | Tee-Object -FilePath $report -Append
  $pytestRc = $LASTEXITCODE
  Log "pytest exit: $pytestRc"
  & $Py -m researchos.app.cli doctor 2>&1 | Tee-Object -FilePath $report -Append
  Log "doctor exit: $LASTEXITCODE"
} finally { Pop-Location }
Log "Report: $report  (status per check: PASS / FAIL / NOT RUN)"
exit $pytestRc
