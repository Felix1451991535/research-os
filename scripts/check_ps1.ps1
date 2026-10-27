# Parse every .ps1 in the repo with the PowerShell language parser (syntax gate; does not execute anything).
$errors = 0
Get-ChildItem -Path (Split-Path -Parent $PSScriptRoot) -Recurse -Filter *.ps1 |
  Where-Object { $_.FullName -notmatch 'node_modules|\.venv' } | ForEach-Object {
    $tokens = $null; $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$parseErrors) | Out-Null
    foreach ($e in $parseErrors) { $errors++; Write-Host "$($_.Name):$($e.Extent.StartLineNumber): $($e.Message)" }
  }
if ($errors) { Write-Host "PS1 PARSE: $errors error(s)"; exit 1 } else { Write-Host 'PS1 PARSE: OK' }
